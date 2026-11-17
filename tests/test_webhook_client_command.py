"""Checks 25 to 28: HttpTelegram webhook methods and the telegram_webhook command."""

import json
from collections.abc import Callable
from io import StringIO

import httpx
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from pytest_django.fixtures import Settings

from radar_alerts.alerts.telegram import HttpTelegram, TelegramError

type Handler = Callable[[httpx.Request], httpx.Response]
TOKEN: str = "123456:SECRET"
SECRET: str = "s3cret_hook"
HOOK: str = "https://example.org/telegram/webhook/"


def telegram_with(handler: Handler) -> HttpTelegram:
    return HttpTelegram(httpx.Client(transport=httpx.MockTransport(handler)), TOKEN)


def test_set_webhook_request() -> None:
    """Check 25."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "result": True})

    telegram_with(handler).set_webhook(HOOK, SECRET)
    assert seen[0].method == "POST"
    assert str(seen[0].url) == f"https://api.telegram.org/bot{TOKEN}/setWebhook"
    assert json.loads(seen[0].content) == {
        "url": HOOK,
        "secret_token": SECRET,
        "allowed_updates": ["message"],
    }


def test_delete_webhook_request() -> None:
    """Check 25."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "result": True})

    telegram_with(handler).delete_webhook()
    assert seen[0].method == "POST"
    assert seen[0].url.path == f"/bot{TOKEN}/deleteWebhook"
    assert json.loads(seen[0].content) == {}


def test_webhook_info_result() -> None:
    """Check 25."""
    seen: list[httpx.Request] = []
    info: dict[str, object] = {"url": HOOK, "pending_update_count": 3}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "result": info})

    assert telegram_with(handler).webhook_info() == info
    assert seen[0].url.path == f"/bot{TOKEN}/getWebhookInfo"
    assert json.loads(seen[0].content) == {}


def test_webhook_info_result_not_a_dict() -> None:
    """Check 25."""
    caught: pytest.ExceptionInfo[TelegramError]
    with pytest.raises(TelegramError) as caught:
        telegram_with(lambda request: httpx.Response(200, json={"ok": True, "result": [1]})).webhook_info()
    assert caught.value.retryable is False


@pytest.mark.parametrize(("status", "retryable"), [(401, False), (500, True)])
def test_webhook_methods_fail_like_send_message(status: int, retryable: bool) -> None:
    """Check 25: the error table of task 2; the token never appears."""
    body: dict[str, object] = {"ok": False, "description": "Unauthorized"}
    client: HttpTelegram = telegram_with(lambda request: httpx.Response(status, json=body))
    calls: list[Callable[[], object]] = [
        lambda: client.set_webhook(HOOK, SECRET),
        client.delete_webhook,
        client.webhook_info,
    ]
    call: Callable[[], object]
    for call in calls:
        caught: pytest.ExceptionInfo[TelegramError]
        with pytest.raises(TelegramError) as caught:
            call()
        assert caught.value.retryable is retryable
        assert TOKEN not in str(caught.value)
        assert "SECRET" not in str(caught.value)
        assert SECRET not in str(caught.value)


class FakeClient:
    def __init__(self, info: dict[str, object] | None = None, error: TelegramError | None = None) -> None:
        self.set_calls: list[tuple[str, str]] = []
        self.delete_calls: int = 0
        self.info_calls: int = 0
        self.info: dict[str, object] = info or {}
        self.error: TelegramError | None = error

    def set_webhook(self, url: str, secret_token: str) -> None:
        self.set_calls.append((url, secret_token))
        if self.error is not None:
            raise self.error

    def delete_webhook(self) -> None:
        self.delete_calls += 1
        if self.error is not None:
            raise self.error

    def webhook_info(self) -> dict[str, object]:
        self.info_calls += 1
        if self.error is not None:
            raise self.error
        return self.info


@pytest.fixture
def hook_settings(settings: Settings) -> None:
    settings.TELEGRAM_MODE = "webhook"
    settings.TELEGRAM_WEBHOOK_SECRET = SECRET
    settings.TELEGRAM_WEBHOOK_URL = "https://example.org"
    settings.TELEGRAM_BOT_TOKEN = TOKEN


def use(monkeypatch: pytest.MonkeyPatch, client: FakeClient | None) -> None:
    monkeypatch.setattr("radar_alerts.alerts.telegram.http_telegram_from_settings", lambda: client)


@pytest.mark.usefixtures("hook_settings")
def test_set_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check 26."""
    client: FakeClient = FakeClient()
    use(monkeypatch, client)
    out: StringIO = StringIO()
    err: StringIO = StringIO()
    call_command("telegram_webhook", "set", stdout=out, stderr=err)
    assert client.set_calls == [(HOOK, SECRET)]
    assert out.getvalue().strip() == f"webhook set to {HOOK}"
    assert SECRET not in out.getvalue() + err.getvalue()
    assert TOKEN not in out.getvalue() + err.getvalue()


@pytest.mark.usefixtures("hook_settings")
@pytest.mark.parametrize(
    ("mode", "url", "has_client"),
    [
        ("polling", "https://example.org", True),
        ("webhook", "", True),
        ("webhook", "http://example.org", True),
        ("webhook", "https://example.org", False),
    ],
    ids=["polling mode", "no url", "http url", "no token"],
)
def test_set_command_preconditions(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, mode: str, url: str, has_client: bool
) -> None:
    """Check 27."""
    settings.TELEGRAM_MODE = mode
    settings.TELEGRAM_WEBHOOK_URL = url
    client: FakeClient = FakeClient()
    use(monkeypatch, client if has_client else None)
    with pytest.raises(CommandError):
        call_command("telegram_webhook", "set", stdout=StringIO(), stderr=StringIO())
    assert client.set_calls == []


@pytest.mark.usefixtures("hook_settings")
def test_set_command_passes_the_telegram_error_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check 27."""
    error: TelegramError = TelegramError("Telegram answered 401: Unauthorized", retryable=False)
    use(monkeypatch, FakeClient(error=error))
    caught: pytest.ExceptionInfo[CommandError]
    with pytest.raises(CommandError) as caught:
        call_command("telegram_webhook", "set", stdout=StringIO(), stderr=StringIO())
    assert str(caught.value) == "Telegram answered 401: Unauthorized"


@pytest.mark.usefixtures("hook_settings")
def test_delete_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check 28."""
    client: FakeClient = FakeClient()
    use(monkeypatch, client)
    out: StringIO = StringIO()
    call_command("telegram_webhook", "delete", stdout=out, stderr=StringIO())
    assert client.delete_calls == 1
    assert out.getvalue().strip() == "webhook deleted"


@pytest.mark.usefixtures("hook_settings")
def test_info_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check 28."""
    info: dict[str, object] = {
        "url": HOOK,
        "pending_update_count": 3,
        "last_error_message": "Connection timed out",
    }
    use(monkeypatch, FakeClient(info=info))
    out: StringIO = StringIO()
    call_command("telegram_webhook", "info", stdout=out, stderr=StringIO())
    assert out.getvalue().splitlines() == [
        f"url: {HOOK}",
        "pending_update_count: 3",
        "last_error_message: Connection timed out",
    ]


@pytest.mark.usefixtures("hook_settings")
def test_info_command_without_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check 28 (interface): missing url and error print as empty."""
    use(monkeypatch, FakeClient(info={"url": "", "pending_update_count": 0}))
    out: StringIO = StringIO()
    call_command("telegram_webhook", "info", stdout=out, stderr=StringIO())
    assert [line.rstrip() for line in out.getvalue().splitlines()] == [
        "url:",
        "pending_update_count: 0",
        "last_error_message:",
    ]


@pytest.mark.usefixtures("hook_settings")
@pytest.mark.parametrize("action", ["delete", "info"])
def test_delete_and_info_need_a_token(monkeypatch: pytest.MonkeyPatch, action: str) -> None:
    """Interface: no token (no client) means CommandError."""
    use(monkeypatch, None)
    with pytest.raises(CommandError):
        call_command("telegram_webhook", action, stdout=StringIO(), stderr=StringIO())


def test_get_telegram_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Interface: tasks.get_telegram() returns telegram.http_telegram_from_settings()."""
    from radar_alerts.alerts import tasks

    sentinel: HttpTelegram = telegram_with(lambda request: httpx.Response(200))
    monkeypatch.setattr("radar_alerts.alerts.telegram.http_telegram_from_settings", lambda: sentinel)
    assert tasks.get_telegram() is sentinel
