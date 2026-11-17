"""Checks 6 to 10: contract_problems, check_radar_contract, the worker_ready log (MockTransport)."""

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from io import StringIO

import httpx
import pytest
from celery.signals import worker_ready
from django.core.management import call_command
from django.core.management.base import CommandError
from pytest_django.fixtures import Settings

import radar_alerts.alerts.tasks  # noqa: F401  (connects the worker_ready handler)
from radar_alerts.alerts.radar import CONTRACT_COLUMNS, ClickHouseRadar

type Handler = Callable[[httpx.Request], httpx.Response]
PASSWORD: str = "s3cret-pw"
SINCE: datetime = datetime(2026, 11, 17, 10, tzinfo=UTC)
VIEWS: tuple[str, ...] = ("steam_players", "steam_chart", "bluesky_tag_hourly")


def describe_body(columns: tuple[tuple[str, str], ...]) -> str:
    return "\n".join(json.dumps({"name": n, "type": t, "default_type": ""}) for n, t in columns) + "\n"


def view_of(request: httpx.Request) -> str:
    query: str = request.url.params["query"]
    view: str
    for view in VIEWS:
        if f"DESCRIBE TABLE alerts_v1.{view}" in query:
            return view
    raise AssertionError(f"unexpected query: {query}")


def good(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text=describe_body(CONTRACT_COLUMNS[view_of(request)]))


def radar_with(handler: Handler, database: str = "alerts_v1") -> ClickHouseRadar:
    return ClickHouseRadar(
        httpx.Client(transport=httpx.MockTransport(handler)),
        url="http://clickhouse:8123",
        user="radar_alerts",
        password=PASSWORD,
        database=database,
    )


def broken_handler(request: httpx.Request) -> httpx.Response:
    view: str = view_of(request)
    if view == "steam_players":
        columns: tuple[tuple[str, str], ...] = (
            ("appid", "String"),
            ("observed_at", "DateTime64(6, 'UTC')"),
            ("players", "Float64"),
        )
        return httpx.Response(200, text=describe_body(columns))
    if view == "steam_chart":
        return httpx.Response(404, text="Code: 60. DB::Exception: UNKNOWN_TABLE")
    return good(request)


def test_matching_contract_has_no_problems() -> None:
    """Check 6: exactly three GET requests, in key order, with credentials; nothing else."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return good(request)

    assert radar_with(handler).contract_problems() == []
    assert [r.method for r in seen] == ["GET", "GET", "GET"]
    queries: list[str] = [r.url.params["query"] for r in seen]
    assert "DESCRIBE TABLE alerts_v1.steam_players" in queries[0]
    assert "DESCRIBE TABLE alerts_v1.steam_chart" in queries[1]
    assert "DESCRIBE TABLE alerts_v1.bluesky_tag_hourly" in queries[2]
    request: httpx.Request
    for request in seen:
        assert request.headers["X-ClickHouse-User"] == "radar_alerts"
        assert request.headers["X-ClickHouse-Key"] == PASSWORD


def test_mismatch_and_missing_view() -> None:
    """Check 7."""
    problems: list[str] = radar_with(broken_handler).contract_problems()
    assert len(problems) == 2
    assert problems[0].startswith("alerts_v1.steam_players:")
    assert "players UInt32" in problems[0]
    assert "players Float64" in problems[0]
    assert problems[1].startswith("alerts_v1.steam_chart:")
    assert not any(PASSWORD in p for p in problems)


def test_database_name_goes_into_the_query_and_the_problem() -> None:
    """Check 6/7: the database is the one the client was built with."""
    problems: list[str] = radar_with(
        lambda request: httpx.Response(404, text="UNKNOWN_DATABASE"), database="radar_x"
    ).contract_problems()
    assert len(problems) == 3
    assert all(p.startswith(f"radar_x.{v}:") for p, v in zip(problems, VIEWS, strict=True))


def answer_for_steam_players(body: str) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        if view_of(request) == "steam_players":
            return httpx.Response(200, text=body)
        return good(request)

    return handler


def test_extra_column_is_a_problem() -> None:
    """Check 8."""
    columns: tuple[tuple[str, str], ...] = (*CONTRACT_COLUMNS["steam_players"], ("extra", "String"))
    problems: list[str] = radar_with(answer_for_steam_players(describe_body(columns))).contract_problems()
    assert len(problems) == 1
    assert problems[0].startswith("alerts_v1.steam_players:")


def test_column_order_is_a_problem() -> None:
    """Check 8."""
    columns: tuple[tuple[str, str], ...] = tuple(reversed(CONTRACT_COLUMNS["steam_players"]))
    problems: list[str] = radar_with(answer_for_steam_players(describe_body(columns))).contract_problems()
    assert len(problems) == 1
    assert problems[0].startswith("alerts_v1.steam_players:")


def test_row_without_type_is_an_unexpected_answer() -> None:
    """Check 8."""
    body: str = '{"name":"appid"}\n'
    problems: list[str] = radar_with(answer_for_steam_players(body)).contract_problems()
    assert len(problems) == 1
    assert problems[0].startswith("alerts_v1.steam_players:")
    assert problems[0].endswith("unexpected DESCRIBE answer")


def test_check_command_ok(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """Check 9."""
    settings.CLICKHOUSE_DATABASE = "alerts_v1"
    monkeypatch.setattr("radar_alerts.alerts.radar.clickhouse_from_settings", lambda: radar_with(good))
    out: StringIO = StringIO()
    err: StringIO = StringIO()
    call_command("check_radar_contract", stdout=out, stderr=err)
    assert "radar contract v1 in alerts_v1: ok" in out.getvalue()
    assert PASSWORD not in out.getvalue() + err.getvalue()


def test_check_command_reports_problems(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """Check 9."""
    settings.CLICKHOUSE_DATABASE = "alerts_v1"
    monkeypatch.setattr(
        "radar_alerts.alerts.radar.clickhouse_from_settings", lambda: radar_with(broken_handler)
    )
    out: StringIO = StringIO()
    err: StringIO = StringIO()
    info: pytest.ExceptionInfo[CommandError]
    with pytest.raises(CommandError) as info:
        call_command("check_radar_contract", stdout=out, stderr=err)
    assert str(info.value) == "radar contract v1 in alerts_v1: 2 problem(s)"
    assert "alerts_v1.steam_players:" in err.getvalue()
    assert "alerts_v1.steam_chart:" in err.getvalue()
    assert PASSWORD not in out.getvalue() + err.getvalue() + str(info.value)
    assert "clickhouse:8123" not in out.getvalue() + err.getvalue()


def test_worker_ready_logs_ok(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Check 10."""
    settings.CLICKHOUSE_DATABASE = "alerts_v1"
    monkeypatch.setattr("radar_alerts.alerts.radar.clickhouse_from_settings", lambda: radar_with(good))
    with caplog.at_level(logging.INFO):
        worker_ready.send(sender=None)
    records: list[logging.LogRecord] = [
        r for r in caplog.records if r.name == "radar_alerts.alerts.tasks" and r.levelno == logging.INFO
    ]
    assert any("radar contract v1 in alerts_v1: ok" in r.getMessage() for r in records)


def test_worker_ready_survives_a_dead_radar(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Check 10: ERROR records, no exception out of the signal; the worker is not blocked."""
    settings.CLICKHOUSE_DATABASE = "alerts_v1"

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("cannot connect", request=request)

    monkeypatch.setattr("radar_alerts.alerts.radar.clickhouse_from_settings", lambda: radar_with(down))
    with caplog.at_level(logging.INFO):
        worker_ready.send(sender=None)
    errors: list[str] = [
        r.getMessage()
        for r in caplog.records
        if r.name == "radar_alerts.alerts.tasks" and r.levelno == logging.ERROR
    ]
    assert any("alerts_v1.steam_players" in m for m in errors)
    assert PASSWORD not in caplog.text


def test_clickhouse_from_settings(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """Interface: url, user, password and database come from the CLICKHOUSE_* settings."""
    from radar_alerts.alerts.radar import clickhouse_from_settings

    settings.CLICKHOUSE_URL = "http://ch.test:8123"
    settings.CLICKHOUSE_USER = "reader"
    settings.CLICKHOUSE_PASSWORD = PASSWORD
    settings.CLICKHOUSE_DATABASE = "alerts_v1"
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="")

    real_client: type[httpx.Client] = httpx.Client

    def make_client(timeout: float) -> httpx.Client:
        return real_client(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr("httpx.Client", make_client)
    assert clickhouse_from_settings().tag_counts(["godot"], SINCE, SINCE) == {}
    assert seen[0].url.host == "ch.test"
    assert seen[0].headers["X-ClickHouse-User"] == "reader"
    assert seen[0].headers["X-ClickHouse-Key"] == PASSWORD
    assert "alerts_v1.bluesky_tag_hourly" in seen[0].url.params["query"]


def test_get_radar_delegates_to_the_settings_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Interface: tasks.get_radar() returns radar.clickhouse_from_settings()."""
    sentinel: ClickHouseRadar = radar_with(good)
    monkeypatch.setattr("radar_alerts.alerts.radar.clickhouse_from_settings", lambda: sentinel)
    assert radar_alerts.alerts.tasks.get_radar() is sentinel
