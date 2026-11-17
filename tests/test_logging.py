"""The bot token is part of every Bot API URL; no log record at INFO may carry it."""

import logging

import httpx
import pytest

from radar_alerts.alerts.telegram import HttpTelegram

TOKEN: str = "123456:secret-token-for-logs"


def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})


def test_token_not_logged_at_info(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    telegram: HttpTelegram = HttpTelegram(httpx.Client(transport=httpx.MockTransport(ok)), TOKEN)
    telegram.send_message(1, "hello")
    assert TOKEN not in caplog.text
