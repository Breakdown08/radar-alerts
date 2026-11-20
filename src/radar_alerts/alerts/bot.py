import logging
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import transaction

from radar_alerts.alerts.models import BotCursor, LinkCode, User
from radar_alerts.alerts.telegram import Telegram, TelegramError

logger: logging.Logger = logging.getLogger(__name__)

LINK_CODE_TTL: timedelta = timedelta(minutes=15)
LINKED_TEXT: str = "Linked to radar-alerts user {username}. Alerts will arrive in this chat."
INVALID_CODE_TEXT: str = (
    "This code is invalid or expired. Request a new one with POST /api/v1/me/telegram-link/."
)
START_HELP_TEXT: str = "Send /start <code>. Get the code with POST /api/v1/me/telegram-link/."
START_RE: re.Pattern[str] = re.compile(r"^/start(?:@[A-Za-z0-9_]+)?(?:\s+(\S+))?\s*$")
# After a week without updates Telegram picks the next update_id at random, possibly below the cursor.
# A cursor that has not moved for this long no longer tells new from repeat.
CURSOR_STALE_AFTER: timedelta = timedelta(days=6)


@dataclass(frozen=True)
class Reply:
    chat_id: int
    text: str


def issue_link_code(user: User, now: datetime) -> LinkCode:
    with transaction.atomic():
        LinkCode.objects.filter(user=user).delete()
        return LinkCode.objects.create(
            user=user, code=secrets.token_urlsafe(16), expires_at=now + LINK_CODE_TTL
        )


def consume_link_code(code: str, now: datetime) -> User | None:
    with transaction.atomic():
        link: LinkCode | None = (
            LinkCode.objects.select_for_update()
            .select_related("user")
            .filter(code=code, expires_at__gt=now)
            .first()
        )
        if link is None:
            return None
        user: User = link.user
        link.delete()
        return user if user.is_active else None


def handle_update(update: Mapping[str, object], now: datetime) -> Reply | None:
    message: object = update.get("message")
    if not isinstance(message, dict):
        return None
    text: object = message.get("text")
    chat: object = message.get("chat")
    chat_id: object = chat.get("id") if isinstance(chat, dict) else None
    if not isinstance(text, str) or not isinstance(chat_id, int) or isinstance(chat_id, bool):
        return None
    match: re.Match[str] | None = START_RE.match(text)
    if match is None:
        return None
    code: str | None = match.group(1)
    if code is None:
        return Reply(chat_id, START_HELP_TEXT)
    user: User | None = consume_link_code(code, now)
    if user is None:
        return Reply(chat_id, INVALID_CODE_TEXT)
    user.telegram_chat_id = chat_id
    user.save(update_fields=["telegram_chat_id"])
    return Reply(chat_id, LINKED_TEXT.format(username=user.username))


def _update_id(update: Mapping[str, object]) -> int:
    value: object = update.get("update_id")
    return value if isinstance(value, int) and not isinstance(value, bool) else -1


def _is_stale(cursor: BotCursor, now: datetime) -> bool:
    return cursor.advanced_at is not None and now - cursor.advanced_at >= CURSOR_STALE_AFTER


def _advance(cursor: BotCursor, update_id: int, now: datetime) -> None:
    cursor.offset = update_id + 1
    cursor.advanced_at = now
    cursor.save(update_fields=["offset", "advanced_at"])


def poll_once(telegram: Telegram, now: datetime) -> int:
    """A stale cursor polls from 0: Telegram returns only unconfirmed updates, whatever their ids."""
    with transaction.atomic():
        BotCursor.objects.get_or_create(pk=1)
        cursor: BotCursor = BotCursor.objects.select_for_update().get(pk=1)
        offset: int = 0 if _is_stale(cursor, now) else cursor.offset
        updates: list[dict[str, object]] = telegram.get_updates(offset, timeout=0)
        fresh: list[dict[str, object]] = sorted(
            (u for u in updates if _update_id(u) >= offset), key=_update_id
        )
        update: dict[str, object]
        for update in fresh:
            reply: Reply | None = handle_update(update, now)
            if reply is None:
                continue
            try:
                telegram.send_message(reply.chat_id, reply.text)
            except TelegramError as exc:
                logger.warning("reply to Telegram update failed: %s", exc)
        if fresh:
            _advance(cursor, max(_update_id(u) for u in fresh), now)
        return len(fresh)


def accept_update(update: Mapping[str, object], now: datetime) -> Reply | None:
    """Handle one update exactly once: a repeat (update_id below a cursor that is not stale) is dropped."""
    update_id: object = update.get("update_id")
    if not isinstance(update_id, int) or isinstance(update_id, bool):
        return None
    with transaction.atomic():
        BotCursor.objects.get_or_create(pk=1)
        cursor: BotCursor = BotCursor.objects.select_for_update().get(pk=1)
        if update_id < cursor.offset and not _is_stale(cursor, now):
            return None
        reply: Reply | None = handle_update(update, now)
        _advance(cursor, update_id, now)
        return reply
