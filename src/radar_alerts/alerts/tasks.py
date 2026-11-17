import logging
from datetime import datetime

import httpx
from celery import Task, shared_task
from django.conf import settings
from django.utils import timezone

from radar_alerts.alerts.bot import poll_once
from radar_alerts.alerts.checking import run_check, stale_pending
from radar_alerts.alerts.delivery import (
    MAX_DELIVERY_RETRIES,
    DeliveryRetry,
    backoff_seconds,
    deliver_once,
    mark_failed,
)
from radar_alerts.alerts.radar import ClickHouseRadar, RadarSource
from radar_alerts.alerts.telegram import HttpTelegram, Telegram, TelegramError

logger: logging.Logger = logging.getLogger(__name__)


def get_radar() -> RadarSource:
    return ClickHouseRadar(
        httpx.Client(timeout=30.0),
        url=settings.CLICKHOUSE_URL,
        user=settings.CLICKHOUSE_USER,
        password=settings.CLICKHOUSE_PASSWORD,
        database=settings.CLICKHOUSE_DATABASE,
    )


def get_telegram() -> Telegram | None:
    token: str = settings.TELEGRAM_BOT_TOKEN
    if not token:
        return None
    return HttpTelegram(httpx.Client(timeout=10.0), token)


@shared_task
def check_rules() -> int:
    now: datetime = timezone.now()
    ids: list[int] = run_check(get_radar(), now) + stale_pending(now)
    firing_id: int
    for firing_id in ids:
        deliver_firing.delay(firing_id)
    return len(ids)


@shared_task(bind=True, max_retries=MAX_DELIVERY_RETRIES, acks_late=True)
def deliver_firing(self: Task[[int], str], firing_id: int) -> str:
    try:
        return deliver_once(firing_id, get_telegram(), timezone.now())
    except DeliveryRetry as retry:
        if self.request.retries >= MAX_DELIVERY_RETRIES:
            mark_failed(firing_id, str(retry.error))
            return "failed"
        raise self.retry(
            exc=retry.error, countdown=backoff_seconds(self.request.retries, retry.error.retry_after)
        ) from retry


@shared_task
def poll_telegram() -> int:
    telegram: Telegram | None = get_telegram()
    if telegram is None:
        return 0
    try:
        return poll_once(telegram, timezone.now())
    except TelegramError as exc:
        logger.warning("polling Telegram failed: %s", exc)
        return 0
