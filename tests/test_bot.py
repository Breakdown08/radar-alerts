"""Checks 40 to 44: link codes, handle_update, poll_once."""

from datetime import timedelta

import pytest
from factories import make_user
from fakes import NOW, FakeTelegram, permanent

from radar_alerts.alerts.bot import (
    CURSOR_STALE_AFTER,
    INVALID_CODE_TEXT,
    LINKED_TEXT,
    START_HELP_TEXT,
    Reply,
    handle_update,
    issue_link_code,
    poll_once,
)
from radar_alerts.alerts.models import BotCursor, LinkCode, User
from radar_alerts.alerts.telegram import TelegramError

pytestmark: list[pytest.MarkDecorator] = [pytest.mark.django_db, pytest.mark.usefixtures("default_tags")]

GROUP: int = -1001234567890


def message(text: str, chat: int = GROUP, update_id: int = 1) -> dict[str, object]:
    return {"update_id": update_id, "message": {"text": text, "chat": {"id": chat}}}


def issue(username: str = "alice") -> tuple[User, str]:
    user: User = make_user(username)
    return user, issue_link_code(user, NOW).code


def test_issue_link_code() -> None:
    """Issuing: 22 URL-safe characters, TTL 15 minutes, one code per user."""
    user: User = make_user("alice")
    first: LinkCode = issue_link_code(user, NOW)
    second: LinkCode = issue_link_code(user, NOW)
    assert len(second.code) == 22
    assert second.expires_at == NOW + timedelta(minutes=15)
    assert list(LinkCode.objects.filter(user=user).values_list("code", flat=True)) == [second.code]
    assert first.code != second.code


def test_start_with_code_links_the_chat() -> None:
    """Check 40."""
    user: User
    code: str
    user, code = issue()
    reply: Reply | None = handle_update(message(f"/start {code}"), NOW)
    assert reply == Reply(GROUP, LINKED_TEXT.format(username="alice"))
    user.refresh_from_db()
    assert user.telegram_chat_id == GROUP
    assert LinkCode.objects.count() == 0


def test_code_is_single_use() -> None:
    """Check 40."""
    user: User
    code: str
    user, code = issue()
    handle_update(message(f"/start {code}"), NOW)
    again: Reply | None = handle_update(message(f"/start {code}", chat=42), NOW)
    assert again == Reply(42, INVALID_CODE_TEXT)
    user.refresh_from_db()
    assert user.telegram_chat_id == GROUP


def test_unknown_code() -> None:
    assert handle_update(message("/start nonsense"), NOW) == Reply(GROUP, INVALID_CODE_TEXT)


@pytest.mark.parametrize("expires_delta", [timedelta(0), timedelta(seconds=-1)])
def test_expired_code(expires_delta: timedelta) -> None:
    """Check 41: expires_at <= now."""
    user: User = make_user("alice")
    LinkCode.objects.create(user=user, code="old-code", expires_at=NOW + expires_delta)
    assert handle_update(message("/start old-code"), NOW) == Reply(GROUP, INVALID_CODE_TEXT)
    user.refresh_from_db()
    assert user.telegram_chat_id is None


def test_code_of_inactive_user() -> None:
    """Check 41."""
    user: User = make_user("carol", is_active=False)
    code: str = issue_link_code(user, NOW).code
    assert handle_update(message(f"/start {code}"), NOW) == Reply(GROUP, INVALID_CODE_TEXT)
    user.refresh_from_db()
    assert user.telegram_chat_id is None


def test_start_without_code() -> None:
    """Check 42."""
    assert handle_update(message("/start"), NOW) == Reply(GROUP, START_HELP_TEXT)


def test_start_addressed_to_the_bot() -> None:
    """Check 42."""
    user: User
    code: str
    user, code = issue()
    reply: Reply | None = handle_update(message(f"/start@radar_alerts_bot {code}", chat=7), NOW)
    assert reply == Reply(7, LINKED_TEXT.format(username="alice"))
    user.refresh_from_db()
    assert user.telegram_chat_id == 7


@pytest.mark.parametrize(
    "update",
    [
        message("hello"),
        message("/startx"),
        message("/start two words"),
        {"update_id": 1},
        {"update_id": 1, "message": {"chat": {"id": 5}}},
        {"update_id": 1, "message": {"text": "/start"}},
        {"update_id": 1, "message": {"text": "/start", "chat": {"id": "5"}}},
        {"update_id": 1, "message": {"text": "/start", "chat": {}}},
    ],
)
def test_updates_without_an_answer(update: dict[str, object]) -> None:
    """Check 42."""
    assert handle_update(update, NOW) is None


def test_poll_once_processes_and_advances_the_cursor() -> None:
    """Check 43."""
    user: User
    code: str
    user, code = issue()
    telegram: FakeTelegram = FakeTelegram()
    telegram.batches = [[message(f"/start {code}", chat=111, update_id=5), message("hello", update_id=6)]]
    assert poll_once(telegram, NOW) == 2
    assert telegram.updates_calls == [(0, 0)]
    assert telegram.sent == [(111, LINKED_TEXT.format(username="alice"))]
    assert BotCursor.objects.get(pk=1).offset == 7
    user.refresh_from_db()
    assert user.telegram_chat_id == 111


def test_poll_once_uses_the_cursor_and_skips_old_updates() -> None:
    """Check 43."""
    user: User
    code: str
    user, code = issue()
    telegram: FakeTelegram = FakeTelegram()
    telegram.batches = [[message("hello", update_id=6)], [message(f"/start {code}", chat=222, update_id=3)]]
    BotCursor.objects.create(pk=1, offset=0)
    poll_once(telegram, NOW)
    assert poll_once(telegram, NOW) == 0
    assert telegram.updates_calls == [(0, 0), (7, 0)]
    assert telegram.sent == []
    user.refresh_from_db()
    assert user.telegram_chat_id is None
    assert BotCursor.objects.get(pk=1).offset == 7


def test_poll_once_handles_updates_in_ascending_order() -> None:
    """Check 43: the lower update_id uses the code first."""
    user: User
    code: str
    user, code = issue()
    telegram: FakeTelegram = FakeTelegram()
    telegram.batches = [
        [message(f"/start {code}", chat=222, update_id=6), message(f"/start {code}", chat=111, update_id=5)]
    ]
    assert poll_once(telegram, NOW) == 2
    assert telegram.sent == [(111, LINKED_TEXT.format(username="alice")), (222, INVALID_CODE_TEXT)]
    user.refresh_from_db()
    assert user.telegram_chat_id == 111
    assert BotCursor.objects.get(pk=1).offset == 7


def test_reply_failure_does_not_stop_linking_or_the_cursor() -> None:
    """Check 44."""
    user: User
    code: str
    user, code = issue()
    telegram: FakeTelegram = FakeTelegram()
    telegram.always = permanent()
    telegram.batches = [[message(f"/start {code}", chat=111, update_id=5)]]
    assert poll_once(telegram, NOW) == 1
    user.refresh_from_db()
    assert user.telegram_chat_id == 111
    assert BotCursor.objects.get(pk=1).offset == 6


def test_get_updates_error_propagates_and_keeps_the_cursor() -> None:
    """Check 44."""
    BotCursor.objects.create(pk=1, offset=4)
    telegram: FakeTelegram = FakeTelegram()
    telegram.updates_error = permanent()
    with pytest.raises(TelegramError):
        poll_once(telegram, NOW)
    assert BotCursor.objects.get(pk=1).offset == 4


def test_poll_once_restarts_from_zero_after_a_stale_cursor() -> None:
    """Check 43: after a week of silence Telegram may restart ids below the cursor; they are new."""
    BotCursor.objects.create(pk=1, offset=900, advanced_at=NOW - CURSOR_STALE_AFTER)
    telegram: FakeTelegram = FakeTelegram()
    telegram.batches = [[message("/start", chat=111, update_id=42)]]
    assert poll_once(telegram, NOW) == 1
    assert telegram.updates_calls == [(0, 0)]
    assert telegram.sent == [(111, START_HELP_TEXT)]
    cursor: BotCursor = BotCursor.objects.get(pk=1)
    assert (cursor.offset, cursor.advanced_at) == (43, NOW)


def test_poll_once_trusts_a_recent_cursor() -> None:
    """Check 43 (boundary): a cursor that moved less than CURSOR_STALE_AFTER ago keeps its offset."""
    BotCursor.objects.create(pk=1, offset=900, advanced_at=NOW - CURSOR_STALE_AFTER + timedelta(seconds=1))
    telegram: FakeTelegram = FakeTelegram()
    telegram.batches = [[message("/start", chat=111, update_id=42)]]
    assert poll_once(telegram, NOW) == 0
    assert telegram.updates_calls == [(900, 0)]
    assert telegram.sent == []
    assert BotCursor.objects.get(pk=1).offset == 900
