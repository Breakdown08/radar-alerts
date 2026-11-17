import math
from datetime import datetime

from django.db.models import F

from radar_alerts.alerts.models import Firing
from radar_alerts.alerts.telegram import Telegram, TelegramError

MAX_DELIVERY_RETRIES: int = 5


class DeliveryRetry(Exception):
    """Temporary failure; the Celery task retries. Carries the TelegramError."""

    error: TelegramError

    def __init__(self, error: TelegramError) -> None:
        super().__init__(str(error))
        self.error: TelegramError = error


def backoff_seconds(retries: int, retry_after: float | None) -> int:
    if retry_after is not None:
        return max(1, math.ceil(retry_after))
    return min(30 * (1 << retries), 900)


def _fail(firing_id: int, error: str) -> None:
    Firing.objects.filter(pk=firing_id).update(status="failed", last_error=error[:1000])


def deliver_once(firing_id: int, telegram: Telegram | None, now: datetime) -> str:
    taken: int = Firing.objects.filter(pk=firing_id, status__in=["pending", "retry"]).update(
        status="sending", attempts=F("attempts") + 1
    )
    if taken == 0:
        return "skipped"
    if telegram is None:
        _fail(firing_id, "TELEGRAM_BOT_TOKEN is not set")
        return "failed"
    firing: Firing = Firing.objects.select_related("rule__subscription__user").get(pk=firing_id)
    chat_id: int | None = firing.rule.subscription.user.telegram_chat_id
    if chat_id is None:
        _fail(firing_id, "Telegram chat is not linked")
        return "failed"
    try:
        message_id: int = telegram.send_message(chat_id, firing.text)
    except TelegramError as exc:
        if not exc.retryable:
            _fail(firing_id, str(exc))
            return "failed"
        Firing.objects.filter(pk=firing_id).update(status="retry", last_error=str(exc)[:1000])
        raise DeliveryRetry(exc) from exc
    Firing.objects.filter(pk=firing_id).update(
        status="sent", sent_at=now, telegram_message_id=message_id, last_error=""
    )
    return "sent"


def mark_failed(firing_id: int, error: str) -> None:
    _fail(firing_id, error)
