import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import httpx
from django.conf import settings


class RadarError(Exception):
    """ClickHouse did not answer 200, the request failed, or the answer did not parse."""


@dataclass(frozen=True)
class SteamChart:
    days: tuple[datetime, ...]
    ranks: dict[datetime, dict[str, int]]


CONTRACT_VERSION: int = 1
CONTRACT_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "steam_players": (("appid", "String"), ("observed_at", "DateTime64(6, 'UTC')"), ("players", "UInt32")),
    "steam_chart": (("appid", "String"), ("chart_day", "DateTime64(6, 'UTC')"), ("rank", "UInt16")),
    "bluesky_tag_hourly": (
        ("tag", "LowCardinality(String)"),
        ("hour", "DateTime('UTC')"),
        ("posts", "UInt64"),
    ),
}


class RadarSource(Protocol):
    def steam_players(self, appids: Sequence[str], since: datetime) -> dict[str, dict[datetime, int]]: ...
    def steam_chart(self, appids: Sequence[str], since: datetime) -> SteamChart: ...
    def tag_counts(
        self, tags: Sequence[str], since: datetime, until: datetime
    ) -> dict[str, dict[datetime, int]]: ...


PLAYERS_SQL: str = """SELECT appid, observed_at, players FROM {db}.steam_players
WHERE has({{appids:Array(String)}}, appid) AND observed_at >= {{since:DateTime64(6, 'UTC')}}"""
CHART_DAYS_SQL: str = """SELECT DISTINCT chart_day FROM {db}.steam_chart
WHERE chart_day >= {{since:DateTime64(6, 'UTC')}}
ORDER BY chart_day DESC LIMIT 2"""
CHART_RANKS_SQL: str = """SELECT appid, chart_day, rank FROM {db}.steam_chart
WHERE has({{days:Array(DateTime64(6, 'UTC'))}}, chart_day) AND has({{appids:Array(String)}}, appid)"""
TAGS_SQL: str = """SELECT tag, hour, posts FROM {db}.bluesky_tag_hourly
WHERE has({{tags:Array(String)}}, tag)
  AND hour >= {{since:DateTime64(6, 'UTC')}} AND hour < {{until:DateTime64(6, 'UTC')}}"""


def _columns(columns: Sequence[tuple[str, str]]) -> str:
    return ", ".join(f"{name} {kind}" for name, kind in columns)


def _moment(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _quoted(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _array(items: Sequence[str]) -> str:
    return "[" + ",".join(_quoted(item) for item in items) + "]"


def _parse_moment(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("not a timestamp")
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


class ClickHouseRadar:
    def __init__(self, http: httpx.Client, *, url: str, user: str, password: str, database: str) -> None:
        self._http: httpx.Client = http
        self._url: str = url.rstrip("/")
        self._user: str = user
        self._password: str = password
        self._database: str = database

    def select(self, sql: str, params: Mapping[str, str]) -> list[dict[str, object]]:
        query: dict[str, str] = {"query": f"{sql}\nFORMAT JSONEachRow"}
        name: str
        value: str
        for name, value in params.items():
            query[f"param_{name}"] = value
        try:
            response: httpx.Response = self._http.get(
                f"{self._url}/",
                params=query,
                headers={"X-ClickHouse-User": self._user, "X-ClickHouse-Key": self._password},
            )
        except httpx.HTTPError as exc:
            raise RadarError(f"request to ClickHouse failed: {type(exc).__name__}") from None
        if response.status_code != 200:
            raise RadarError(f"ClickHouse answered {response.status_code}: {response.text[:200]}")
        rows: list[dict[str, object]] = []
        line: str
        for line in response.text.splitlines():
            if not line.strip():
                continue
            try:
                parsed: object = json.loads(line)
            except ValueError:
                raise RadarError("ClickHouse answered with a line that is not JSON") from None
            if not isinstance(parsed, dict):
                raise RadarError("ClickHouse answered with a line that is not an object")
            rows.append(parsed)
        return rows

    def contract_problems(self) -> list[str]:
        """Compare DESCRIBE TABLE of each view with CONTRACT_COLUMNS; an empty list means it matches."""
        problems: list[str] = []
        view: str
        expected: tuple[tuple[str, str], ...]
        for view, expected in CONTRACT_COLUMNS.items():
            name: str = f"{self._database}.{view}"
            try:
                rows: list[dict[str, object]] = self.select(f"DESCRIBE TABLE {name}", {})
            except RadarError as exc:
                problems.append(f"{name}: {exc}")
                continue
            actual: list[tuple[str, str]] = []
            row: dict[str, object]
            for row in rows:
                column: object = row.get("name")
                kind: object = row.get("type")
                if isinstance(column, str) and isinstance(kind, str):
                    actual.append((column, kind))
                else:
                    break
            else:
                if actual != list(expected):
                    problems.append(f"{name}: expected {_columns(expected)}, got {_columns(actual)}")
                continue
            problems.append(f"{name}: unexpected DESCRIBE answer")
        return problems

    def _read(self, sql: str, params: Mapping[str, str]) -> list[dict[str, object]]:
        return self.select(sql.format(db=self._database), params)

    def steam_players(self, appids: Sequence[str], since: datetime) -> dict[str, dict[datetime, int]]:
        if not appids:
            return {}
        rows: list[dict[str, object]] = self._read(
            PLAYERS_SQL, {"appids": _array(appids), "since": _moment(since)}
        )
        result: dict[str, dict[datetime, int]] = {}
        row: dict[str, object]
        try:
            for row in rows:
                appid: object = row["appid"]
                if not isinstance(appid, str):
                    raise ValueError("appid")
                result.setdefault(appid, {})[_parse_moment(row["observed_at"])] = int(str(row["players"]))
        except KeyError, ValueError, TypeError:
            raise RadarError("ClickHouse answer did not parse") from None
        return result

    def steam_chart(self, appids: Sequence[str], since: datetime) -> SteamChart:
        day_rows: list[dict[str, object]] = self._read(CHART_DAYS_SQL, {"since": _moment(since)})
        try:
            days: tuple[datetime, ...] = tuple(
                sorted((_parse_moment(row["chart_day"]) for row in day_rows), reverse=True)
            )
        except KeyError, ValueError, TypeError:
            raise RadarError("ClickHouse answer did not parse") from None
        if not days or not appids:
            return SteamChart(days=days, ranks={day: {} for day in days})
        rows: list[dict[str, object]] = self._read(
            CHART_RANKS_SQL,
            {
                "days": _array([day.strftime("%Y-%m-%d %H:%M:%S.%f") for day in days]),
                "appids": _array(appids),
            },
        )
        ranks: dict[datetime, dict[str, int]] = {day: {} for day in days}
        row: dict[str, object]
        try:
            for row in rows:
                appid: object = row["appid"]
                if not isinstance(appid, str):
                    raise ValueError("appid")
                ranks[_parse_moment(row["chart_day"])][appid] = int(str(row["rank"]))
        except KeyError, ValueError, TypeError:
            raise RadarError("ClickHouse answer did not parse") from None
        return SteamChart(days=days, ranks=ranks)

    def tag_counts(
        self, tags: Sequence[str], since: datetime, until: datetime
    ) -> dict[str, dict[datetime, int]]:
        if not tags:
            return {}
        rows: list[dict[str, object]] = self._read(
            TAGS_SQL,
            {
                "tags": _array(tags),
                "since": _moment(since),
                "until": _moment(until),
            },
        )
        result: dict[str, dict[datetime, int]] = {}
        row: dict[str, object]
        try:
            for row in rows:
                tag: object = row["tag"]
                if not isinstance(tag, str):
                    raise ValueError("tag")
                result.setdefault(tag, {})[_parse_moment(row["hour"])] = int(str(row["posts"]))
        except KeyError, ValueError, TypeError:
            raise RadarError("ClickHouse answer did not parse") from None
        return result


def clickhouse_from_settings() -> ClickHouseRadar:
    return ClickHouseRadar(
        httpx.Client(timeout=30.0),
        url=settings.CLICKHOUSE_URL,
        user=settings.CLICKHOUSE_USER,
        password=settings.CLICKHOUSE_PASSWORD,
        database=settings.CLICKHOUSE_DATABASE,
    )
