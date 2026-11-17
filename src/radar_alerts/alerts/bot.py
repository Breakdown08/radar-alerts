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


def poll_once(telegram: Telegram, now: datetime) -> int:
    with transaction.atomic():
        BotCursor.objects.get_or_create(pk=1)
        cursor: BotCursor = BotCursor.objects.select_for_update().get(pk=1)
        updates: list[dict[str, object]] = telegram.get_updates(cursor.offset, timeout=0)
        fresh: list[dict[str, object]] = sorted(
            (u for u in updates if _update_id(u) >= cursor.offset), key=_update_id
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
            cursor.offset = max(_update_id(u) for u in fresh) + 1
            cursor.save(update_fields=["offset"])
        return len(fresh)
