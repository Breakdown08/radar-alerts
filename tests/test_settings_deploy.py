"""Checks 1 to 3: telegram_mode, telegram_api_url, http_telegram_from_settings, the stub, beat, middleware."""

import threading
import time
from http.server import HTTPServer

import httpx
import pytest
from django.core.exceptions import ImproperlyConfigured
from pytest_django.fixtures import Settings
from telegram_stub import StubHandler

from radar_alerts.alerts.telegram import HttpTelegram, http_telegram_from_settings
from radar_alerts.settings import beat_schedule, telegram_api_url, telegram_mode


@pytest.mark.parametrize(
    ("mode", "secret", "expected"),
    [("", "", "polling"), ("polling", "", "polling"), ("webhook", "abc_DEF-1", "webhook")],
)
def test_telegram_mode_valid(mode: str, secret: str, expected: str) -> None:
    """Check 1."""
    assert telegram_mode(mode, secret) == expected


@pytest.mark.parametrize(
    ("mode", "secret"),
    [("webhook", ""), ("webhook", "a b"), ("webhook", "x" * 257), ("hook", "s")],
)
def test_telegram_mode_invalid(mode: str, secret: str) -> None:
    """Check 1: bad mode or bad secret; the message never carries the secret."""
    info: pytest.ExceptionInfo[ImproperlyConfigured]
    with pytest.raises(ImproperlyConfigured) as info:
        telegram_mode(mode, secret)
    if secret:
        assert secret not in str(info.value)


def test_telegram_mode_accepts_longest_secret() -> None:
    """Check 1: the boundary of the 1..256 rule."""
    assert telegram_mode("webhook", "x" * 256) == "webhook"
    assert telegram_mode("webhook", "x") == "webhook"


def test_telegram_api_url() -> None:
    """Check 1."""
    assert telegram_api_url("") == "https://api.telegram.org"
    assert telegram_api_url("http://telegram-stub:8081/") == "http://telegram-stub:8081"
    assert telegram_api_url("https://example.org") == "https://example.org"
    bad: str
    for bad in ("telegram-stub:8081", "ftp://x"):
        with pytest.raises(ImproperlyConfigured):
            telegram_api_url(bad)


def test_client_from_settings_uses_api_url(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """Check 1: TELEGRAM_API_URL and the token decide where get_updates goes."""
    settings.TELEGRAM_API_URL = "http://stub:1"
    settings.TELEGRAM_BOT_TOKEN = "123:x"
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "result": []})

    real_client: type[httpx.Client] = httpx.Client

    def make_client(timeout: float) -> httpx.Client:
        return real_client(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr("httpx.Client", make_client)
    client: HttpTelegram | None = http_telegram_from_settings()
    assert client is not None
    assert client.get_updates(0, 0) == []
    assert str(seen[0].url) == "http://stub:1/bot123:x/getUpdates"


def test_client_from_settings_without_token(settings: Settings) -> None:
    """Check 1 (interface): an empty token means no client."""
    settings.TELEGRAM_BOT_TOKEN = ""
    assert http_telegram_from_settings() is None


def test_telegram_stub_answers_401_and_hides_the_token(capsys: pytest.CaptureFixture[str]) -> None:
    """Check 1: the fake Bot API answers 401 with JSON; the logged line holds no token."""
    server: HTTPServer = HTTPServer(("127.0.0.1", 0), StubHandler)
    thread: threading.Thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    out: str = ""
    try:
        port: int = server.server_address[1]
        response: httpx.Response = httpx.post(f"http://127.0.0.1:{port}/bot123:x/getUpdates", json={})
        get_response: httpx.Response = httpx.get(f"http://127.0.0.1:{port}/bot123:x/getMe")
        deadline: float = time.monotonic() + 2.0
        while "getMe" not in out and time.monotonic() < deadline:
            out += capsys.readouterr().out
            time.sleep(0.01)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert response.status_code == 401
    assert response.headers["Content-Type"].startswith("application/json")
    assert response.json() == {"ok": False, "error_code": 401, "description": "Unauthorized"}
    assert get_response.status_code == 401
    assert "/bot<token>/getUpdates" in out
    assert "123:x" not in out
    assert len([line for line in out.splitlines() if "getUpdates" in line]) == 1


def test_beat_schedule_polling() -> None:
    """Check 2: both entries, exactly those of task 2."""
    assert beat_schedule("polling") == {
        "check-rules": {"task": "radar_alerts.alerts.tasks.check_rules", "schedule": 300.0},
        "poll-telegram": {
            "task": "radar_alerts.alerts.tasks.poll_telegram",
            "schedule": 10.0,
            "options": {"expires": 9},
        },
    }


def test_beat_schedule_webhook_has_no_polling() -> None:
    """Check 2."""
    assert set(beat_schedule("webhook")) == {"check-rules"}


def test_settings_schedule_follows_mode(settings: Settings) -> None:
    """Check 2: the module-level schedule is the function applied to the module-level mode."""
    assert settings.CELERY_BEAT_SCHEDULE == beat_schedule(settings.TELEGRAM_MODE)


def test_middleware_and_static_root(settings: Settings) -> None:
    """Check 3."""
    assert settings.MIDDLEWARE[0] == "django.middleware.security.SecurityMiddleware"
    assert settings.MIDDLEWARE[1] == "whitenoise.middleware.WhiteNoiseMiddleware"
    assert settings.STATIC_ROOT == settings.BASE_DIR / "staticfiles"
