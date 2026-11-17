"""Check 37: the three ClickHouseRadar reads against a live ClickHouse, and the GET read-only guarantee."""

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
import pytest

from radar_alerts.alerts.radar import ClickHouseRadar, SteamChart

pytestmark: list[pytest.MarkDecorator] = [pytest.mark.clickhouse]

DB: str = "radar_alerts_test"
DDL: str = f"""CREATE TABLE {DB}.observations (
    event_id UUID, source LowCardinality(String), entity_type LowCardinality(String), entity_id String,
    metric LowCardinality(String), value Float64, observed_at DateTime64(6,'UTC'),
    window_start Nullable(DateTime64(6,'UTC')), ingested_at DateTime64(6,'UTC')
) ENGINE = ReplacingMergeTree(ingested_at) PARTITION BY toYYYYMM(observed_at)
ORDER BY (source, entity_type, entity_id, metric, observed_at, event_id)"""
INGESTED: str = "2026-11-17 12:00:00.000000"


def ts(day: int, hour: int = 0, minute: int = 0) -> str:
    return f"2026-11-{day:02d} {hour:02d}:{minute:02d}:00.000000"


def utc(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, tzinfo=UTC)


def row(
    n: int, entity_type: str, entity_id: str, metric: str, value: float, observed: str
) -> dict[str, object]:
    return {
        "event_id": f"00000000-0000-0000-0000-{n:012d}",
        "source": "steam",
        "entity_type": entity_type,
        "entity_id": entity_id,
        "metric": metric,
        "value": value,
        "observed_at": observed,
        "window_start": None,
        "ingested_at": INGESTED,
    }


def post_row(n: int, tag: str, window_start: str, observed: str) -> dict[str, object]:
    return {
        **row(n, "post", f"at://did:plc:x/app.bsky.feed.post/{n}", f"tag_{tag}", 1.0, observed),
        "source": "bluesky",
        "window_start": window_start,
    }


def rows() -> list[dict[str, object]]:
    out: list[dict[str, object]] = [
        row(1, "game", "570", "players_current", 1000.0, ts(17, 10)),
        row(2, "game", "570", "players_current", 1100.0, ts(17, 11)),
        row(3, "game", "730", "players_current", 50.0, ts(17, 11)),
        row(4, "game", "999", "players_current", 7.0, ts(17, 11)),
        row(10, "game", "570", "chart_rank", 5.0, ts(14)),
        row(11, "game", "570", "chart_rank", 12.0, ts(15)),
        row(12, "game", "730", "chart_rank", 3.0, ts(15)),
        row(13, "game", "570", "chart_rank", 7.0, ts(16)),
        row(14, "game", "999", "chart_rank", 1.0, ts(16)),
        post_row(20, "godot", ts(17, 11), ts(17, 11, 1)),
        post_row(20, "godot", ts(17, 11), ts(17, 11, 1)),  # same event_id twice: FINAL must count it once
        post_row(21, "godot", ts(17, 11, 10), ts(17, 11, 11)),
        post_row(22, "godot", ts(17, 12, 20), ts(17, 12, 21)),
        post_row(23, "godot", ts(17, 13), ts(17, 13, 1)),  # at "until": excluded
        post_row(24, "pixelart", ts(17, 11, 30), ts(17, 11, 31)),
        post_row(25, "unity", ts(17, 11, 30), ts(17, 11, 31)),  # not asked for
    ]
    return out


@pytest.fixture
def clickhouse() -> Iterator[httpx.Client]:
    url: str | None = os.environ.get("CLICKHOUSE_TEST_URL")
    if not url:
        pytest.skip("CLICKHOUSE_TEST_URL is not set")
    client: httpx.Client = httpx.Client(
        base_url=url,
        headers={
            "X-ClickHouse-User": os.environ.get("CLICKHOUSE_TEST_USER", "default"),
            "X-ClickHouse-Key": os.environ.get("CLICKHOUSE_TEST_PASSWORD", ""),
        },
        timeout=30.0,
    )

    def execute(sql: str) -> None:
        client.post("/", content=sql).raise_for_status()

    execute(f"DROP DATABASE IF EXISTS {DB}")
    execute(f"CREATE DATABASE {DB}")
    execute(DDL)
    body: str = "\n".join(json.dumps(r) for r in rows())
    client.post(
        "/", params={"query": f"INSERT INTO {DB}.observations FORMAT JSONEachRow"}, content=body
    ).raise_for_status()
    yield client
    execute(f"DROP DATABASE IF EXISTS {DB}")
    client.close()


def radar_for(clickhouse: httpx.Client) -> ClickHouseRadar:
    return ClickHouseRadar(
        httpx.Client(timeout=30.0),
        url=str(clickhouse.base_url).rstrip("/"),
        user=clickhouse.headers["X-ClickHouse-User"],
        password=clickhouse.headers["X-ClickHouse-Key"],
        database=DB,
    )


def test_steam_players(clickhouse: httpx.Client) -> None:
    result: dict[str, dict[datetime, int]] = radar_for(clickhouse).steam_players(
        ["570", "730"], datetime(2026, 11, 17, 10, 30, tzinfo=UTC)
    )
    assert result == {"570": {utc(17, 11): 1100}, "730": {utc(17, 11): 50}}


def test_steam_chart_two_latest_days(clickhouse: httpx.Client) -> None:
    chart: SteamChart = radar_for(clickhouse).steam_chart(["570", "730"], utc(10))
    assert chart.days == (utc(16), utc(15))
    assert chart.ranks == {utc(16): {"570": 7}, utc(15): {"570": 12, "730": 3}}


def test_tag_counts_count_each_event_once(clickhouse: httpx.Client) -> None:
    counts: dict[str, dict[datetime, int]] = radar_for(clickhouse).tag_counts(
        ["godot", "pixelart"], utc(17, 11), utc(17, 13)
    )
    assert counts == {"godot": {utc(17, 11): 2, utc(17, 12): 1}, "pixelart": {utc(17, 11): 1}}


def test_get_requests_cannot_write(clickhouse: httpx.Client) -> None:
    """The protection against writes is the GET method: ClickHouse runs such a query with readonly=1."""
    count_sql: str = f"SELECT count() FROM {DB}.observations FINAL FORMAT TabSeparated"
    before: str = clickhouse.get("/", params={"query": count_sql}).text.strip()
    attempt: httpx.Response = clickhouse.get(
        "/",
        params={"query": f"INSERT INTO {DB}.observations SELECT * FROM {DB}.observations LIMIT 1"},
    )
    assert attempt.status_code != 200
    assert "READONLY" in attempt.text
    assert clickhouse.get("/", params={"query": count_sql}).text.strip() == before
