import logging
from datetime import datetime

from celery import Task, shared_task
from celery.signals import worker_ready
from django.conf import settings
from django.utils import timezone

from radar_alerts.alerts import radar, telegram
from radar_alerts.alerts.bot import poll_once
from radar_alerts.alerts.checking import run_check, stale_pending
from radar_alerts.alerts.delivery import (
    MAX_DELIVERY_RETRIES,
    DeliveryRetry,
    backoff_seconds,
    deliver_once,
    mark_failed,
)
from radar_alerts.alerts.radar import CONTRACT_VERSION, RadarError, RadarSource
from radar_alerts.alerts.telegram import Telegram, TelegramError

logger: logging.Logger = logging.getLogger(__name__)


def get_radar() -> RadarSource:
    return radar.clickhouse_from_settings()


def get_telegram() -> Telegram | None:
    return telegram.http_telegram_from_settings()


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
    if settings.TELEGRAM_MODE == "webhook":
        return 0
    client: Telegram | None = get_telegram()
    if client is None:
        return 0
    try:
        return poll_once(client, timezone.now())
    except TelegramError as exc:
        logger.warning("polling Telegram failed: %s", exc)
        return 0


def log_contract_status() -> None:
    """INFO 'radar contract v1 in <db>: ok', or one ERROR per problem. Never raises RadarError."""
    database: str = settings.CLICKHOUSE_DATABASE
    try:
        problems: list[str] = radar.clickhouse_from_settings().contract_problems()
    except RadarError as exc:
        problems = [str(exc)]
    if not problems:
        logger.info("radar contract v%d in %s: ok", CONTRACT_VERSION, database)
        return
    problem: str
    for problem in problems:
        logger.error("radar contract v%d in %s: %s", CONTRACT_VERSION, database, problem)


@worker_ready.connect
def _on_worker_ready(**kwargs: object) -> None:
    log_contract_status()
