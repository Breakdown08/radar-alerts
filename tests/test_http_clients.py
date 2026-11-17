"""Checks 11-14 (task 3), 34-36 (task 2): ClickHouseRadar and HttpTelegram over httpx.MockTransport."""

import json
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest

from radar_alerts.alerts.radar import ClickHouseRadar, RadarError, SteamChart
from radar_alerts.alerts.telegram import HttpTelegram, TelegramError

type Handler = Callable[[httpx.Request], httpx.Response]
PASSWORD: str = "s3cret-pw"
TOKEN: str = "123:SECRET"
SINCE: datetime = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)
UNTIL: datetime = datetime(2026, 11, 17, 14, 0, tzinfo=UTC)


def param_values(request: httpx.Request) -> list[str]:
    """Values of the param_* query parameters (ClickHouse query parameters)."""
    return [v for k, v in request.url.params.multi_items() if k.startswith("param_")]


def radar_with(handler: Handler) -> ClickHouseRadar:
    return ClickHouseRadar(
        httpx.Client(transport=httpx.MockTransport(handler)),
        url="http://clickhouse:8123",
        user="radar_alerts",
        password=PASSWORD,
        database="alerts_v1",
    )


def test_steam_players_request_and_parsing() -> None:
    """Check 11."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        rows: list[str] = [
            '{"appid":"570","observed_at":"2026-11-17 12:00:00.000000","players":1234}',
            '{"appid":"570","observed_at":"2026-11-17 13:00:00.000000","players":"1300"}',
        ]
        return httpx.Response(200, text="\n".join(rows) + "\n")

    result: dict[str, dict[datetime, int]] = radar_with(handler).steam_players(["570"], SINCE)
    assert result == {
        "570": {
            datetime(2026, 11, 17, 12, tzinfo=UTC): 1234,
            datetime(2026, 11, 17, 13, tzinfo=UTC): 1300,
        }
    }
    assert len(seen) == 1
    request: httpx.Request = seen[0]
    assert request.method == "GET"
    assert request.url.host == "clickhouse"
    assert request.url.path == "/"
    assert request.headers["X-ClickHouse-User"] == "radar_alerts"
    assert request.headers["X-ClickHouse-Key"] == PASSWORD
    query: str = request.url.params["query"]
    assert query.rstrip().endswith("FORMAT JSONEachRow")
    assert "alerts_v1.steam_players" in query
    assert "observations" not in query
    assert "FINAL" not in query
    assert "570" not in query
    assert "2026-11-17" not in query
    values: list[str] = param_values(request)
    assert any("570" in v for v in values)
    assert "2026-11-17 10:00:00" in values


def test_steam_chart_reads_the_view() -> None:
    """Check 12: both requests go to alerts_v1.steam_chart; the answer becomes a SteamChart."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "param_days" not in request.url.params:
            return httpx.Response(
                200,
                text=(
                    '{"chart_day":"2026-11-16 00:00:00.000000"}\n{"chart_day":"2026-11-15 00:00:00.000000"}\n'
                ),
            )
        rows: list[str] = [
            '{"appid":"570","chart_day":"2026-11-16 00:00:00.000000","rank":7}',
            '{"appid":"570","chart_day":"2026-11-15 00:00:00.000000","rank":"12"}',
            '{"appid":"730","chart_day":"2026-11-15 00:00:00.000000","rank":3}',
        ]
        return httpx.Response(200, text="\n".join(rows) + "\n")

    chart: SteamChart = radar_with(handler).steam_chart(["570", "730"], SINCE)
    day16: datetime = datetime(2026, 11, 16, tzinfo=UTC)
    day15: datetime = datetime(2026, 11, 15, tzinfo=UTC)
    assert chart == SteamChart(days=(day16, day15), ranks={day16: {"570": 7}, day15: {"570": 12, "730": 3}})
    assert len(seen) == 2
    request: httpx.Request
    for request in seen:
        assert request.method == "GET"
        query: str = request.url.params["query"]
        assert "alerts_v1.steam_chart" in query
        assert "observations" not in query
        assert "FINAL" not in query
        assert "570" not in query


def test_empty_inputs_make_no_request() -> None:
    """Check 14: nothing to look for, nothing to ask."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, text="")

    radar: ClickHouseRadar = radar_with(handler)
    assert radar.tag_counts([], SINCE, UNTIL) == {}
    assert radar.steam_players([], SINCE) == {}
    assert calls == []


@pytest.mark.parametrize("hour", ["2026-11-17 11:00:00", "2026-11-17 11:00:00.000000"])
@pytest.mark.parametrize("posts", ['"15"', "15"])
def test_tag_counts_parses_string_and_number(hour: str, posts: str) -> None:
    """Check 13: UInt64 may come as a string or as a number; both forms parse."""
    body: str = '{"tag":"godot","hour":"' + hour + '","posts":' + posts + "}\n"
    radar: ClickHouseRadar = radar_with(lambda request: httpx.Response(200, text=body))
    assert radar.tag_counts(["godot"], SINCE, UNTIL) == {
        "godot": {datetime(2026, 11, 17, 11, tzinfo=UTC): 15}
    }


def test_tag_counts_request_shape() -> None:
    """Check 13: the view, tag names (not metrics) in the parameters, no values in the SQL."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="")

    assert radar_with(handler).tag_counts(["godot"], SINCE, UNTIL) == {}
    request: httpx.Request = seen[0]
    assert request.method == "GET"
    query: str = request.url.params["query"]
    assert "alerts_v1.bluesky_tag_hourly" in query
    assert "observations" not in query
    assert "FINAL" not in query
    assert "godot" not in query
    values: list[str] = param_values(request)
    assert any("godot" in v for v in values)
    assert not any("tag_godot" in v for v in values)
    assert {"2026-11-17 10:00:00", "2026-11-17 14:00:00"} <= set(values)


def failing_500(request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, text="boom")


def failing_connect(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("cannot connect", request=request)


def failing_garbage(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text="this is not json\n")


def failing_missing_field(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text='{"tag":"godot"}\n')


@pytest.mark.parametrize("handler", [failing_500, failing_connect, failing_garbage, failing_missing_field])
def test_radar_errors(handler: Handler) -> None:
    """Check 14: every failure is a RadarError and never shows the password."""
    info: pytest.ExceptionInfo[RadarError]
    with pytest.raises(RadarError) as info:
        radar_with(handler).tag_counts(["godot"], SINCE, UNTIL)
    assert PASSWORD not in str(info.value)


def telegram_with(handler: Handler) -> HttpTelegram:
    return HttpTelegram(httpx.Client(transport=httpx.MockTransport(handler)), TOKEN)


def test_send_message_request_and_result() -> None:
    """Check 36."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    assert telegram_with(handler).send_message(-100500, "hello <b>") == 42
    request: httpx.Request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert json.loads(request.content) == {
        "chat_id": -100500,
        "text": "hello <b>",
        "link_preview_options": {"is_disabled": True},
    }


def test_get_updates_request_and_result() -> None:
    """Check 36: getUpdates posts offset, timeout and the message filter."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "result": [{"update_id": 9}]})

    assert telegram_with(handler).get_updates(9, 0) == [{"update_id": 9}]
    request: httpx.Request = seen[0]
    assert request.method == "POST"
    assert request.url.path == f"/bot{TOKEN}/getUpdates"
    assert json.loads(request.content) == {"offset": 9, "timeout": 0, "allowed_updates": ["message"]}


def raises(exc: Callable[[httpx.Request], Exception]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc(request)

    return handler


def reply(status: int, body: object) -> Handler:
    return lambda request: httpx.Response(status, json=body)


def url_in_message(cls: Callable[..., Exception]) -> Callable[[httpx.Request], Exception]:
    """An httpx error whose own text contains the request URL, and so the token."""
    return lambda request: cls(f"failure for {request.url}", request=request)


CASES: list[tuple[str, Handler, bool, float | None]] = [
    ("connect error", raises(url_in_message(httpx.ConnectError)), True, None),
    ("connect timeout", raises(url_in_message(httpx.ConnectTimeout)), True, None),
    ("pool timeout", raises(url_in_message(httpx.PoolTimeout)), True, None),
    ("read timeout", raises(url_in_message(httpx.ReadTimeout)), False, None),
    ("write timeout", raises(url_in_message(httpx.WriteTimeout)), False, None),
    ("remote protocol error", raises(url_in_message(httpx.RemoteProtocolError)), False, None),
    (
        "429 with retry_after",
        reply(429, {"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 5}}),
        True,
        5,
    ),
    ("429 without retry_after", reply(429, {"ok": False, "description": "Too Many Requests"}), True, None),
    ("502", reply(502, {"ok": False, "description": "Bad Gateway"}), True, None),
    ("403 bot blocked", reply(403, {"ok": False, "description": "Forbidden: bot was blocked"}), False, None),
    ("400 chat not found", reply(400, {"ok": False, "description": "chat not found"}), False, None),
    ("200 ok false", reply(200, {"ok": False, "description": "nope"}), False, None),
    ("200 without message_id", reply(200, {"ok": True, "result": {}}), False, None),
    ("200 message_id not an int", reply(200, {"ok": True, "result": {"message_id": "x"}}), False, None),
    ("200 not json", lambda request: httpx.Response(200, text="<html>"), False, None),
]


@pytest.mark.parametrize(
    ("handler", "retryable", "retry_after"), [c[1:] for c in CASES], ids=[c[0] for c in CASES]
)
def test_send_message_errors(handler: Handler, retryable: bool, retry_after: float | None) -> None:
    """Check 36: one row of the error table per case; the token never appears."""
    info: pytest.ExceptionInfo[TelegramError]
    with pytest.raises(TelegramError) as info:
        telegram_with(handler).send_message(1, "hi")
    assert info.value.retryable is retryable
    assert info.value.retry_after == retry_after
    assert TOKEN not in str(info.value)
    assert "SECRET" not in str(info.value)
