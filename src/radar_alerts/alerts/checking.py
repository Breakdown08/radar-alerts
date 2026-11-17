import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.db import IntegrityError, transaction

from radar_alerts.alerts.evaluation import (
    CHART_MAX_AGE,
    Hit,
    evaluation_hour,
    render_message,
    steam_growth_hit,
    steam_top_hit,
    tag_growth_hit,
    tag_top_hit,
)
from radar_alerts.alerts.models import Firing, Rule
from radar_alerts.alerts.radar import RadarError, RadarSource, SteamChart

logger: logging.Logger = logging.getLogger(__name__)

PENDING_GRACE: timedelta = timedelta(minutes=10)
PENDING_MAX_AGE: timedelta = timedelta(hours=24)


def _window(rule: Rule) -> int:
    return rule.window_hours if rule.window_hours is not None else 1


def _record(rule: Rule, hit: Hit | None, created: list[int]) -> None:
    if hit is None:
        return
    try:
        with transaction.atomic():
            firing: Firing = Firing.objects.create(
                rule=rule,
                period=hit.period,
                text=render_message(rule.subscription, rule, hit),
                status="pending",
            )
    except IntegrityError:
        return  # already fired for this period: a repeated or a parallel run
    created.append(firing.pk)


def _steam_growth(radar: RadarSource, rules: list[Rule], now: datetime, created: list[int]) -> None:
    if not rules:
        return
    hour: datetime = evaluation_hour(now)
    appids: list[str] = sorted({rule.subscription.entity_id for rule in rules})
    widest: int = max(_window(rule) for rule in rules)
    try:
        players: dict[str, dict[datetime, int]] = radar.steam_players(
            appids, since=hour - timedelta(hours=widest + 2)
        )
    except RadarError as exc:
        logger.warning("steam players could not be read: %s", exc)
        return
    rule: Rule
    for rule in rules:
        _record(
            rule,
            steam_growth_hit(
                players.get(rule.subscription.entity_id, {}), rule.threshold, _window(rule), now
            ),
            created,
        )


def _steam_top(radar: RadarSource, rules: list[Rule], now: datetime, created: list[int]) -> None:
    if not rules:
        return
    appids: list[str] = sorted({rule.subscription.entity_id for rule in rules})
    try:
        chart: SteamChart = radar.steam_chart(appids, since=now - CHART_MAX_AGE - timedelta(days=1))
    except RadarError as exc:
        logger.warning("steam chart could not be read: %s", exc)
        return
    rule: Rule
    for rule in rules:
        _record(rule, steam_top_hit(chart, rule.subscription.entity_id, rule.threshold, now), created)


def _bluesky(radar: RadarSource, rules: list[Rule], now: datetime, created: list[int]) -> None:
    if not rules:
        return
    hour: datetime = evaluation_hour(now)
    widest: int = max(_window(rule) for rule in rules)
    tags: tuple[str, ...] = settings.RADAR_BLUESKY_TAGS
    try:
        counts: dict[str, dict[datetime, int]] = radar.tag_counts(
            tags, since=hour - timedelta(hours=2 * widest + 1), until=hour
        )
    except RadarError as exc:
        logger.warning("bluesky tag counts could not be read: %s", exc)
        return
    rule: Rule
    for rule in rules:
        tag: str = rule.subscription.entity_id
        if rule.kind == "growth_pct":
            hit: Hit | None = tag_growth_hit(counts, tag, rule.threshold, _window(rule), now)
        else:
            hit = tag_top_hit(counts, tags, tag, rule.threshold, _window(rule), now)
        _record(rule, hit, created)


def run_check(radar: RadarSource, now: datetime) -> list[int]:
    rules: list[Rule] = list(
        Rule.objects.filter(
            is_active=True,
            subscription__is_active=True,
            subscription__user__is_active=True,
            subscription__user__telegram_chat_id__isnull=False,
        )
        .select_related("subscription")
        .order_by("id")
    )
    created: list[int] = []
    _steam_growth(
        radar,
        [r for r in rules if r.subscription.source == "steam" and r.kind == "growth_pct"],
        now,
        created,
    )
    _steam_top(
        radar,
        [r for r in rules if r.subscription.source == "steam" and r.kind == "top_n"],
        now,
        created,
    )
    _bluesky(radar, [r for r in rules if r.subscription.source == "bluesky"], now, created)
    return created


def stale_pending(now: datetime) -> list[int]:
    return list(
        Firing.objects.filter(
            status="pending", created_at__gte=now - PENDING_MAX_AGE, created_at__lt=now - PENDING_GRACE
        )
        .order_by("id")
        .values_list("id", flat=True)
    )
