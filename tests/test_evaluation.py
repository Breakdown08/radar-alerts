"""Checks 8 to 15: pure rule evaluation. now = 17 Nov 2026 12:10 UTC, so H = 12:00."""

from datetime import UTC, datetime
from decimal import Decimal as D

import pytest
from django.conf import settings
from fakes import NOW, at

from radar_alerts.alerts.evaluation import (
    Hit,
    evaluation_hour,
    floor_hour,
    render_message,
    steam_growth_hit,
    steam_top_hit,
    tag_growth_hit,
    tag_top_hit,
)
from radar_alerts.alerts.models import Rule, Subscription
from radar_alerts.alerts.radar import SteamChart

type Counts = dict[str, dict[datetime, int]]


def test_floor_hour_and_evaluation_hour() -> None:
    """Check 8."""
    assert floor_hour(datetime(2026, 11, 17, 12, 59, 59, 900000, tzinfo=UTC)) == at(12)
    assert floor_hour(at(12, minute=59)).tzinfo == UTC
    assert evaluation_hour(at(12, minute=4)) == at(11)
    assert evaluation_hour(at(12, minute=5)) == at(12)


def growth(series: dict[datetime, int]) -> Hit | None:
    return steam_growth_hit(series, D("30"), 24, NOW)


def test_steam_growth_fires_on_transition() -> None:
    """Check 9."""
    series: dict[datetime, int] = {at(11, 16): 100, at(12, 16): 100, at(11): 120, at(12): 130}
    assert growth(series) == Hit(period=at(12), current=130, base=100)


def test_steam_growth_silent_while_condition_lasts() -> None:
    """Check 9: already met an hour ago."""
    series: dict[datetime, int] = {at(11, 16): 100, at(12, 16): 100, at(11): 130, at(12): 130}
    assert growth(series) is None


def test_steam_growth_threshold_is_inclusive() -> None:
    """Check 9."""
    assert growth({at(12, 16): 100, at(12): 130}) == Hit(period=at(12), current=130, base=100)
    assert growth({at(12, 16): 100, at(12): 129}) is None


def test_steam_growth_stale_data() -> None:
    """Check 10: newest point older than H - 1h."""
    assert growth({at(10, 16): 100, at(10): 500}) is None


def test_steam_growth_uses_point_one_hour_before_h() -> None:
    """Check 10: t is the newest key in [H - 1h, H], here 11:00."""
    assert growth({at(11, 16): 100, at(11): 200}) == Hit(period=at(11), current=200, base=100)


def test_steam_growth_without_base_point() -> None:
    """Check 10."""
    assert growth({at(12): 500}) is None


def test_steam_growth_zero_base() -> None:
    """Check 10."""
    assert growth({at(12, 16): 0, at(12): 50}) is None


def test_steam_growth_previous_period_without_data_counts_as_unmet() -> None:
    """Check 10: no point at t - 1h - 24h."""
    assert growth({at(12, 16): 100, at(12): 200}) == Hit(period=at(12), current=200, base=100)


def chart(ranks_16: dict[str, int], ranks_15: dict[str, int]) -> SteamChart:
    return SteamChart(days=(at(0, 16), at(0, 15)), ranks={at(0, 16): ranks_16, at(0, 15): ranks_15})


def test_steam_top_fires_on_entry() -> None:
    """Check 11."""
    hit: Hit | None = steam_top_hit(chart({"570": 7}, {"570": 12}), "570", D("10"), NOW)
    assert hit == Hit(period=at(0, 16), current=7, base=12)


def test_steam_top_silent_if_already_inside() -> None:
    """Check 11."""
    assert steam_top_hit(chart({"570": 7}, {"570": 9}), "570", D("10"), NOW) is None


def test_steam_top_new_in_chart() -> None:
    """Check 11."""
    hit: Hit | None = steam_top_hit(chart({"570": 7}, {}), "570", D("10"), NOW)
    assert hit == Hit(period=at(0, 16), current=7, base=None)


def test_steam_top_outside_threshold() -> None:
    """Check 11."""
    assert steam_top_hit(chart({"570": 11}, {"570": 30}), "570", D("10"), NOW) is None
    assert steam_top_hit(chart({}, {"570": 30}), "570", D("10"), NOW) is None


def test_steam_top_needs_two_days() -> None:
    """Check 12."""
    single: SteamChart = SteamChart(days=(at(0, 16),), ranks={at(0, 16): {"570": 7}})
    assert steam_top_hit(single, "570", D("10"), NOW) is None
    assert steam_top_hit(SteamChart(days=(), ranks={}), "570", D("10"), NOW) is None


def test_steam_top_rejects_old_chart() -> None:
    """Check 12: days[0] older than now - 3 days."""
    old: SteamChart = SteamChart(
        days=(at(0, 13), at(0, 12)), ranks={at(0, 13): {"570": 7}, at(0, 12): {"570": 40}}
    )
    assert steam_top_hit(old, "570", D("10"), NOW) is None


@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ({at(8): 5, at(9): 5, at(10): 4, at(11): 11}, None),
        ({at(8): 5, at(9): 2, at(10): 4, at(11): 11}, Hit(period=at(12), current=15, base=7)),
        ({at(8): 5, at(10): 4, at(11): 11}, Hit(period=at(12), current=15, base=5)),
        ({at(8): 0, at(9): 0, at(10): 0, at(11): 0}, None),
        ({}, None),
    ],
)
def test_tag_growth(counts: dict[datetime, int], expected: Hit | None) -> None:
    """Check 13."""
    assert tag_growth_hit({"godot": counts}, "godot", D("50"), 2, NOW) == expected


def test_tag_growth_missing_tag_is_zero() -> None:
    """Check 13: a tag without any row."""
    assert tag_growth_hit({}, "godot", D("50"), 2, NOW) is None


TOP_COUNTS: Counts = {
    "a": {at(11): 10, at(10): 10},
    "b": {at(11): 8, at(10): 1},
    "c": {at(11): 8, at(10): 9},
    "d": {at(11): 1},
}
TAGS: tuple[str, ...] = ("a", "b", "c", "d")


def test_tag_top_enters() -> None:
    """Check 14: b is second now and was third an hour ago."""
    assert tag_top_hit(TOP_COUNTS, TAGS, "b", D("2"), 1, NOW) == Hit(period=at(12), current=2, base=3)


def test_tag_top_silent_when_already_inside() -> None:
    """Check 14: c was second an hour ago too, a is first all along."""
    assert tag_top_hit(TOP_COUNTS, TAGS, "c", D("2"), 1, NOW) is None
    assert tag_top_hit(TOP_COUNTS, TAGS, "a", D("2"), 1, NOW) is None


def test_tag_top_outside() -> None:
    """Check 14: d is fourth."""
    assert tag_top_hit(TOP_COUNTS, TAGS, "d", D("2"), 1, NOW) is None


def test_tag_top_needs_posts() -> None:
    """Check 14: rank 1, but zero posts."""
    assert tag_top_hit({}, ("a", "b"), "a", D("1"), 1, NOW) is None
    assert tag_top_hit({}, ("a", "b"), "b", D("1"), 1, NOW) is None


def test_tag_top_ties_share_the_rank() -> None:
    """Check 14: equal counts do not push each other down."""
    counts: Counts = {"a": {at(11): 5}, "b": {at(11): 5}}
    assert tag_top_hit(counts, ("a", "b"), "b", D("1"), 1, NOW) == Hit(period=at(12), current=1, base=1)


def make(
    source: str, entity_id: str, label: str, kind: str, threshold: str, window: int | None
) -> tuple[Subscription, Rule]:
    entity_type: str = "game" if source == "steam" else "tag"
    sub: Subscription = Subscription(source=source, entity_type=entity_type, entity_id=entity_id, label=label)
    rule: Rule = Rule(subscription=sub, kind=kind, threshold=D(threshold), window_hours=window)
    return sub, rule


STEAM_URL: str = "https://store.steampowered.com/app/570/"


@pytest.mark.usefixtures("default_tags")
def test_render_four_kinds() -> None:
    """Check 15."""
    sub: Subscription
    rule: Rule
    sub, rule = make("steam", "570", "Dota 2", "growth_pct", "30", 24)
    assert render_message(sub, rule, Hit(at(12), 135, 100)) == (
        f"Dota 2 (Steam app 570): players grew 35.0% in 24 h, 100 → 135.\n{STEAM_URL}"
    )
    sub, rule = make("steam", "570", "Dota 2", "top_n", "10", None)
    assert render_message(sub, rule, Hit(at(0, 16), 7, 12)) == (
        f"Dota 2 (Steam app 570) entered the Steam top 10: rank 7 (was 12).\n{STEAM_URL}"
    )
    sub, rule = make("bluesky", "godot", "", "growth_pct", "50", 6)
    assert render_message(sub, rule, Hit(at(12), 15, 10)) == (
        "#godot: posts grew 50.0% in 6 h, 10 → 15.\nhttps://bsky.app/hashtag/godot"
    )
    assert len(settings.RADAR_BLUESKY_TAGS) == 7
    sub, rule = make("bluesky", "godot", "", "top_n", "3", 6)
    assert render_message(sub, rule, Hit(at(12), 2, 5)) == (
        "#godot entered the top 3 of 7 tags over 6 h: rank 2 (was 5).\nhttps://bsky.app/hashtag/godot"
    )


@pytest.mark.usefixtures("default_tags")
def test_render_without_label() -> None:
    """Check 15."""
    sub: Subscription
    rule: Rule
    sub, rule = make("steam", "570", "", "growth_pct", "30", 24)
    assert render_message(sub, rule, Hit(at(12), 135, 100)).startswith("Steam app 570: players grew 35.0%")


@pytest.mark.usefixtures("default_tags")
def test_render_new_in_chart() -> None:
    """Check 15."""
    sub: Subscription
    rule: Rule
    sub, rule = make("steam", "570", "Dota 2", "top_n", "10", None)
    assert render_message(sub, rule, Hit(at(0, 16), 7, None)) == (
        f"Dota 2 (Steam app 570) entered the Steam top 10: rank 7 (new in the chart).\n{STEAM_URL}"
    )


@pytest.mark.usefixtures("default_tags")
@pytest.mark.parametrize(("base", "current", "pct"), [(3, 4, "33.3"), (8, 9, "12.5"), (100, 135, "35.0")])
def test_render_percent_rounding(base: int, current: int, pct: str) -> None:
    """Check 15."""
    sub: Subscription
    rule: Rule
    sub, rule = make("steam", "570", "", "growth_pct", "10", 24)
    assert f"grew {pct}% in 24 h, {base} → {current}." in render_message(
        sub, rule, Hit(at(12), current, base)
    )
