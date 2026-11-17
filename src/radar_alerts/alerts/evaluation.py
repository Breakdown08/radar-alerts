from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings

from radar_alerts.alerts.models import Rule, Subscription
from radar_alerts.alerts.radar import SteamChart

DATA_LAG: timedelta = timedelta(minutes=5)
CHART_MAX_AGE: timedelta = timedelta(days=3)
HOUR: timedelta = timedelta(hours=1)


@dataclass(frozen=True)
class Hit:
    period: datetime
    current: int
    base: int | None


def floor_hour(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


def evaluation_hour(now: datetime) -> datetime:
    return floor_hour(now - DATA_LAG)


def grew(current: int, base: int, threshold: Decimal) -> bool:
    return base > 0 and (current - base) * 100 >= threshold * base


def steam_growth_hit(
    series: Mapping[datetime, int], threshold: Decimal, window_hours: int, now: datetime
) -> Hit | None:
    hour: datetime = evaluation_hour(now)
    window: timedelta = timedelta(hours=window_hours)
    latest: list[datetime] = [moment for moment in series if hour - HOUR <= moment <= hour]
    if not latest:
        return None

    def met(moment: datetime) -> bool:
        return (
            moment in series
            and moment - window in series
            and grew(series[moment], series[moment - window], threshold)
        )

    t: datetime = max(latest)
    if met(t) and not met(t - HOUR):
        return Hit(period=t, current=series[t], base=series[t - window])
    return None


def steam_top_hit(chart: SteamChart, appid: str, threshold: Decimal, now: datetime) -> Hit | None:
    if len(chart.days) < 2 or chart.days[0] < now - CHART_MAX_AGE:
        return None
    limit: int = int(threshold)
    now_rank: int | None = chart.ranks.get(chart.days[0], {}).get(appid)
    before_rank: int | None = chart.ranks.get(chart.days[1], {}).get(appid)
    if now_rank is not None and now_rank <= limit and (before_rank is None or before_rank > limit):
        return Hit(period=chart.days[0], current=now_rank, base=before_rank)
    return None


def _total(counts: Mapping[str, Mapping[datetime, int]], tag: str, start: datetime, end: datetime) -> int:
    per_hour: Mapping[datetime, int] = counts.get(tag, {})
    total: int = 0
    moment: datetime = start
    while moment < end:
        total += per_hour.get(moment, 0)
        moment += HOUR
    return total


def tag_growth_hit(
    counts: Mapping[str, Mapping[datetime, int]],
    tag: str,
    threshold: Decimal,
    window_hours: int,
    now: datetime,
) -> Hit | None:
    hour: datetime = evaluation_hour(now)
    window: timedelta = timedelta(hours=window_hours)

    def current(end: datetime) -> int:
        return _total(counts, tag, end - window, end)

    def base(end: datetime) -> int:
        return _total(counts, tag, end - 2 * window, end - window)

    def met(end: datetime) -> bool:
        return grew(current(end), base(end), threshold)

    if met(hour) and not met(hour - HOUR):
        return Hit(period=hour, current=current(hour), base=base(hour))
    return None


def tag_top_hit(
    counts: Mapping[str, Mapping[datetime, int]],
    tags: Sequence[str],
    tag: str,
    threshold: Decimal,
    window_hours: int,
    now: datetime,
) -> Hit | None:
    hour: datetime = evaluation_hour(now)
    window: timedelta = timedelta(hours=window_hours)
    limit: int = int(threshold)

    def rank(end: datetime) -> int:
        own: int = _total(counts, tag, end - window, end)
        return 1 + sum(1 for other in tags if _total(counts, other, end - window, end) > own)

    def met(end: datetime) -> bool:
        return _total(counts, tag, end - window, end) > 0 and rank(end) <= limit

    if met(hour) and not met(hour - HOUR):
        return Hit(period=hour, current=rank(hour), base=rank(hour - HOUR))
    return None


def _percent(hit: Hit) -> str:
    base: int = hit.base if hit.base is not None else 0
    value: Decimal = (Decimal(hit.current - base) * 100 / Decimal(base)).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )
    return format(value, "f")


def render_message(subscription: Subscription, rule: Rule, hit: Hit) -> str:
    window: int | None = rule.window_hours
    limit: int = int(rule.threshold)
    if subscription.source == "steam":
        appid: str = subscription.entity_id
        name: str = (
            f"{subscription.label} (Steam app {appid})" if subscription.label else f"Steam app {appid}"
        )
        link: str = f"https://store.steampowered.com/app/{appid}/"
        if rule.kind == "growth_pct":
            grown: str = f"players grew {_percent(hit)}% in {window} h, {hit.base} \u2192 {hit.current}."
            return f"{name}: {grown}\n{link}"
        was: str = f"(was {hit.base})" if hit.base is not None else "(new in the chart)"
        return f"{name} entered the Steam top {limit}: rank {hit.current} {was}.\n{link}"
    tag: str = subscription.entity_id
    link = f"https://bsky.app/hashtag/{tag}"
    if rule.kind == "growth_pct":
        return f"#{tag}: posts grew {_percent(hit)}% in {window} h, {hit.base} \u2192 {hit.current}.\n{link}"
    total: int = len(settings.RADAR_BLUESKY_TAGS)
    return (
        f"#{tag} entered the top {limit} of {total} tags over {window} h: "
        f"rank {hit.current} (was {hit.base}).\n{link}"
    )
