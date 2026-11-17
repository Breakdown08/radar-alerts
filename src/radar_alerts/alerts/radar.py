import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import httpx


class RadarError(Exception):
    """ClickHouse did not answer 200, the request failed, or the answer did not parse."""


@dataclass(frozen=True)
class SteamChart:
    days: tuple[datetime, ...]
    ranks: dict[datetime, dict[str, int]]


class RadarSource(Protocol):
    def steam_players(self, appids: Sequence[str], since: datetime) -> dict[str, dict[datetime, int]]: ...
    def steam_chart(self, appids: Sequence[str], since: datetime) -> SteamChart: ...
    def tag_counts(
        self, tags: Sequence[str], since: datetime, until: datetime
    ) -> dict[str, dict[datetime, int]]: ...


PLAYERS_SQL: str = """SELECT entity_id, observed_at, value FROM {db}.observations FINAL
WHERE source = 'steam' AND entity_type = 'game' AND metric = 'players_current'
  AND has({{appids:Array(String)}}, entity_id) AND observed_at >= {{since:DateTime64(6, 'UTC')}}"""
CHART_DAYS_SQL: str = """SELECT DISTINCT observed_at FROM {db}.observations
WHERE source = 'steam' AND entity_type = 'game' AND metric = 'chart_rank'
  AND observed_at >= {{since:DateTime64(6, 'UTC')}}
ORDER BY observed_at DESC LIMIT 2"""
CHART_RANKS_SQL: str = """SELECT entity_id, observed_at, toInt32(value) AS rank FROM {db}.observations FINAL
WHERE source = 'steam' AND entity_type = 'game' AND metric = 'chart_rank'
  AND has({{days:Array(DateTime64(6, 'UTC'))}}, observed_at) AND has({{appids:Array(String)}}, entity_id)"""
TAGS_SQL: str = """SELECT substring(metric, 5) AS tag, toStartOfHour(window_start) AS hour, count() AS posts
FROM {db}.observations FINAL
WHERE source = 'bluesky' AND entity_type = 'post' AND has({{metrics:Array(String)}}, metric)
  AND window_start >= {{since:DateTime64(6, 'UTC')}} AND window_start < {{until:DateTime64(6, 'UTC')}}
GROUP BY tag, hour"""


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
                entity_id: object = row["entity_id"]
                if not isinstance(entity_id, str):
                    raise ValueError("entity_id")
                result.setdefault(entity_id, {})[_parse_moment(row["observed_at"])] = int(
                    float(str(row["value"]))
                )
        except KeyError, ValueError, TypeError:
            raise RadarError("ClickHouse answer did not parse") from None
        return result

    def steam_chart(self, appids: Sequence[str], since: datetime) -> SteamChart:
        day_rows: list[dict[str, object]] = self._read(CHART_DAYS_SQL, {"since": _moment(since)})
        try:
            days: tuple[datetime, ...] = tuple(
                sorted((_parse_moment(row["observed_at"]) for row in day_rows), reverse=True)
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
                entity_id: object = row["entity_id"]
                if not isinstance(entity_id, str):
                    raise ValueError("entity_id")
                ranks[_parse_moment(row["observed_at"])][entity_id] = int(str(row["rank"]))
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
                "metrics": _array([f"tag_{tag}" for tag in tags]),
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
