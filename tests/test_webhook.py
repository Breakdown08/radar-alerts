"""Checks 17 to 24: POST /telegram/webhook/ and accept_update."""

import json
import logging
from datetime import datetime, timedelta

import pytest
from django.test import Client
from django.utils import timezone
from factories import Resp, make_user
from fakes import NOW, FakeTelegram
from pytest_django.fixtures import Settings

from radar_alerts.alerts.bot import (
    CURSOR_STALE_AFTER,
    INVALID_CODE_TEXT,
    LINKED_TEXT,
    START_HELP_TEXT,
    Reply,
    accept_update,
    issue_link_code,
)
from radar_alerts.alerts.models import BotCursor, LinkCode, User
from radar_alerts.alerts.tasks import poll_telegram

pytestmark: list[pytest.MarkDecorator] = [pytest.mark.django_db, pytest.mark.usefixtures("default_tags")]

SECRET: str = "s3cret_hook"
HEADER: str = "X-Telegram-Bot-Api-Secret-Token"
URL: str = "/telegram/webhook/"


class Boom(Exception):
    pass


@pytest.fixture
def webhook_mode(settings: Settings) -> None:
    settings.TELEGRAM_MODE = "webhook"
    settings.TELEGRAM_WEBHOOK_SECRET = SECRET


def update(text: str, update_id: int = 500, chat: int = 777) -> dict[str, object]:
    return {"update_id": update_id, "message": {"text": text, "chat": {"id": chat}}}


def post(body: object, secret: str | None = SECRET, client: Client | None = None, raw: bool = False) -> Resp:
    data: str = body if isinstance(body, str) and raw else json.dumps(body)
    headers: dict[str, str] = {} if secret is None else {HEADER: secret}
    return (client or Client()).post(URL, data=data, content_type="application/json", headers=headers)


def issued() -> tuple[User, str]:
    user: User = make_user("alice")
    return user, issue_link_code(user, timezone.now()).code


def test_polling_mode_hides_the_endpoint(settings: Settings) -> None:
    """Check 17: 404 in polling mode and nothing changes; the same request works in webhook mode."""
    settings.TELEGRAM_MODE = "polling"
    settings.TELEGRAM_WEBHOOK_SECRET = SECRET
    user: User
    code: str
    user, code = issued()
    response: Resp = post(update(f"/start {code}"))
    assert response.status_code == 404
    user.refresh_from_db()
    assert user.telegram_chat_id is None
    assert LinkCode.objects.filter(code=code).exists()
    assert not BotCursor.objects.exists()
    settings.TELEGRAM_MODE = "webhook"
    assert post(update(f"/start {code}")).status_code == 200
    user.refresh_from_db()
    assert user.telegram_chat_id == 777


@pytest.mark.usefixtures("webhook_mode")
def test_bad_secret_and_bad_bodies(caplog: pytest.LogCaptureFixture) -> None:
    """Check 18."""
    with caplog.at_level(logging.WARNING):
        missing: Resp = post(update("hi"), secret=None)
        wrong: Resp = post(update("hi"), secret="wrong")
    assert missing.status_code == 403
    assert wrong.status_code == 403
    assert missing.content == b""
    assert wrong.content == b""
    assert "telegram webhook: bad secret" in caplog.text
    assert "wrong" not in caplog.text
    assert post("not json", raw=True).status_code == 400
    assert post([1]).status_code == 400
    assert Client().get(URL, headers={HEADER: SECRET}).status_code == 405
    assert not BotCursor.objects.exists()


@pytest.mark.usefixtures("webhook_mode")
def test_valid_update_links_the_chat_and_replies() -> None:
    """Check 19."""
    user: User
    code: str
    user, code = issued()
    response: Resp = post(update(f"/start {code}"))
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/json")
    assert response.json() == {
        "method": "sendMessage",
        "chat_id": 777,
        "text": LINKED_TEXT.format(username="alice"),
    }
    user.refresh_from_db()
    assert user.telegram_chat_id == 777
    assert BotCursor.objects.get(pk=1).offset == 501


@pytest.mark.usefixtures("webhook_mode")
def test_repeated_update_is_ignored() -> None:
    """Check 20: Telegram retries the same update; no second reply, no INVALID_CODE_TEXT."""
    code: str = issued()[1]
    first: Resp = post(update(f"/start {code}"))
    again: Resp = post(update(f"/start {code}"))
    assert first.status_code == 200
    assert first.content != b""
    assert again.status_code == 200
    assert again.content == b""
    assert INVALID_CODE_TEXT.encode() not in again.content
    assert BotCursor.objects.get(pk=1).offset == 501


@pytest.mark.usefixtures("webhook_mode")
def test_updates_without_a_reply_and_without_an_id() -> None:
    """Check 21."""
    response: Resp = post(update("hello", update_id=600))
    assert response.status_code == 200
    assert response.content == b""
    assert BotCursor.objects.get(pk=1).offset == 601
    no_id: Resp = post({"message": {"text": "/start", "chat": {"id": 1}}})
    assert no_id.status_code == 200
    assert no_id.content == b""
    assert BotCursor.objects.get(pk=1).offset == 601
    bool_id: Resp = post({"update_id": True, "message": {"text": "/start", "chat": {"id": 1}}})
    assert bool_id.status_code == 200
    assert bool_id.content == b""
    assert BotCursor.objects.get(pk=1).offset == 601


def test_accept_update_skips_old_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check 22: update_id below the offset is a repeat; handle_update is not called."""
    BotCursor.objects.create(pk=1, offset=10)
    code: str = issued()[1]
    calls: list[object] = []

    def spy(update: object, now: object) -> None:
        calls.append(update)

    monkeypatch.setattr("radar_alerts.alerts.bot.handle_update", spy)
    assert accept_update(update(f"/start {code}", update_id=9), NOW) is None
    assert calls == []
    assert LinkCode.objects.filter(code=code).exists()
    assert BotCursor.objects.get(pk=1).offset == 10


def test_accept_update_takes_the_current_offset() -> None:
    """Check 22 (boundary): update_id == offset is new."""
    BotCursor.objects.create(pk=1, offset=10)
    user: User = make_user("alice")
    code: str = issue_link_code(user, NOW).code
    reply: Reply | None = accept_update(update(f"/start {code}", update_id=10, chat=5), NOW)
    assert reply == Reply(5, LINKED_TEXT.format(username="alice"))
    assert BotCursor.objects.get(pk=1).offset == 11


def test_accept_update_takes_a_lower_id_after_a_stale_cursor() -> None:
    """Check 22: after a week of silence Telegram may restart ids below the cursor; they are new."""
    BotCursor.objects.create(pk=1, offset=900, advanced_at=NOW - CURSOR_STALE_AFTER)
    assert accept_update(update("/start", update_id=42, chat=5), NOW) == Reply(5, START_HELP_TEXT)
    cursor: BotCursor = BotCursor.objects.get(pk=1)
    assert (cursor.offset, cursor.advanced_at) == (43, NOW)


def test_accept_update_drops_a_lower_id_while_the_cursor_is_recent() -> None:
    """Check 22 (boundary): just under CURSOR_STALE_AFTER a lower id is still a repeat."""
    BotCursor.objects.create(pk=1, offset=900, advanced_at=NOW - CURSOR_STALE_AFTER + timedelta(seconds=1))
    assert accept_update(update("/start", update_id=42, chat=5), NOW) is None
    assert BotCursor.objects.get(pk=1).offset == 900


def test_accept_update_creates_the_cursor() -> None:
    """Interface: the cursor row is created when missing."""
    assert accept_update(update("hello", update_id=3), NOW) is None
    assert BotCursor.objects.get(pk=1).offset == 4


def test_accept_update_without_a_usable_id_changes_nothing() -> None:
    """Interface: no update_id, or a bool one, means None and no cursor."""
    assert accept_update({"message": {"text": "/start", "chat": {"id": 1}}}, NOW) is None
    assert accept_update({"update_id": True}, NOW) is None
    assert accept_update({"update_id": "5"}, NOW) is None
    assert not BotCursor.objects.exists()


def test_accept_update_failure_rolls_everything_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """Interface: an exception undoes the link and the cursor, so a retry is handled again."""
    from radar_alerts.alerts import bot

    BotCursor.objects.create(pk=1, offset=3)
    user: User = make_user("alice")
    code: str = issue_link_code(user, NOW).code
    real: object = bot.handle_update

    def failing(update_: dict[str, object], now: datetime) -> Reply | None:
        assert callable(real)
        real(update_, now)
        raise Boom("boom")

    monkeypatch.setattr("radar_alerts.alerts.bot.handle_update", failing)
    with pytest.raises(Boom):
        accept_update(update(f"/start {code}", update_id=5), NOW)
    user.refresh_from_db()
    assert user.telegram_chat_id is None
    assert LinkCode.objects.filter(code=code).exists()
    assert BotCursor.objects.get(pk=1).offset == 3


@pytest.mark.usefixtures("webhook_mode")
def test_csrf_is_not_enforced() -> None:
    """Check 23."""
    response: Resp = post(update("hello", update_id=700), client=Client(enforce_csrf_checks=True))
    assert response.status_code == 200
    assert BotCursor.objects.get(pk=1).offset == 701


def test_poll_telegram_is_silent_in_webhook_mode(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """Check 24."""
    settings.TELEGRAM_MODE = "webhook"
    calls: list[int] = []
    telegram: FakeTelegram = FakeTelegram()

    def get_telegram() -> FakeTelegram:
        calls.append(1)
        return telegram

    monkeypatch.setattr("radar_alerts.alerts.tasks.get_telegram", get_telegram)
    assert poll_telegram() == 0
    assert calls == []
    assert telegram.updates_calls == []
