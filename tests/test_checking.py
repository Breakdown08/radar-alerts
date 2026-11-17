"""Checks 16 to 22: run_check against a fake radar, stale_pending."""

from collections.abc import Callable
from datetime import timedelta

import pytest
from django.conf import settings
from factories import make_user
from fakes import NOW, FakeRadar, H, at, steam_growth_series, steam_rule, tag_growth_counts, tag_rule

from radar_alerts.alerts.checking import run_check, stale_pending
from radar_alerts.alerts.evaluation import Hit, render_message
from radar_alerts.alerts.models import Firing, Rule, Subscription, User
from radar_alerts.alerts.radar import SteamChart

pytestmark: list[pytest.MarkDecorator] = [pytest.mark.django_db, pytest.mark.usefixtures("default_tags")]


def test_steam_growth_is_one_batched_call() -> None:
    """Check 16."""
    alice: User = make_user("alice", telegram_chat_id=1)
    bob: User = make_user("bob", telegram_chat_id=2)
    steam_rule(alice, "730", window=24)
    steam_rule(alice, "570", window=48)
    steam_rule(bob, "570", window=24)
    radar: FakeRadar = FakeRadar()
    assert run_check(radar, NOW) == []
    assert radar.players_calls == [(["570", "730"], H - timedelta(hours=50))]
    assert radar.chart_calls == []
    assert radar.tag_calls == []


def test_steam_top_is_one_chart_call() -> None:
    """The chart is read once for all top_n rules, looking CHART_MAX_AGE + 1 day back."""
    alice: User = make_user("alice", telegram_chat_id=1)
    steam_rule(alice, "730", kind="top_n", window=None)
    steam_rule(alice, "570", kind="top_n", window=None)
    radar: FakeRadar = FakeRadar()
    run_check(radar, NOW)
    assert radar.chart_calls == [(["570", "730"], NOW - timedelta(days=4))]
    assert radar.players_calls == []
    assert radar.tag_calls == []


def test_bluesky_rules_are_one_batched_call() -> None:
    """Check 17."""
    alice: User = make_user("alice", telegram_chat_id=1)
    tag_rule(alice, "godot", "growth_pct", "50", 6)
    tag_rule(alice, "pixelart", "top_n", "3", 24)
    radar: FakeRadar = FakeRadar()
    run_check(radar, NOW)
    assert radar.tag_calls == [(list(settings.RADAR_BLUESKY_TAGS), H - timedelta(hours=49), H)]
    assert radar.players_calls == []
    assert radar.chart_calls == []


def deactivate_rule(rule: Rule, user: User) -> None:
    Rule.objects.filter(pk=rule.pk).update(is_active=False)


def deactivate_sub(rule: Rule, user: User) -> None:
    Subscription.objects.filter(pk=rule.subscription.pk).update(is_active=False)


def deactivate_user(rule: Rule, user: User) -> None:
    User.objects.filter(pk=user.pk).update(is_active=False)


def unlink_chat(rule: Rule, user: User) -> None:
    User.objects.filter(pk=user.pk).update(telegram_chat_id=None)


@pytest.mark.parametrize("disable", [deactivate_rule, deactivate_sub, deactivate_user, unlink_chat])
def test_inactive_things_are_not_checked(disable: Callable[[Rule, User], None]) -> None:
    """Check 18."""
    alice: User = make_user("alice", telegram_chat_id=1)
    rule: Rule = steam_rule(alice, "570")
    disable(rule, alice)
    radar: FakeRadar = FakeRadar(players=steam_growth_series())
    assert run_check(radar, NOW) == []
    assert radar.total_calls == 0
    assert Firing.objects.count() == 0


def test_firing_is_created() -> None:
    """Check 19."""
    alice: User = make_user("alice", telegram_chat_id=1)
    rule: Rule = steam_rule(alice, "570")
    radar: FakeRadar = FakeRadar(players=steam_growth_series())
    ids: list[int] = run_check(radar, NOW)
    firing: Firing = Firing.objects.get()
    assert ids == [firing.pk]
    assert (firing.rule, firing.period, firing.status, firing.attempts) == (rule, H, "pending", 0)
    assert firing.text == render_message(rule.subscription, rule, Hit(period=H, current=200, base=100))


def test_repeat_check_is_idempotent() -> None:
    """Check 20."""
    alice: User = make_user("alice", telegram_chat_id=1)
    steam_rule(alice, "570")
    radar: FakeRadar = FakeRadar(players=steam_growth_series())
    assert len(run_check(radar, NOW)) == 1
    assert run_check(radar, NOW) == []
    assert Firing.objects.count() == 1


def test_one_failing_source_does_not_stop_the_others() -> None:
    """Check 21."""
    alice: User = make_user("alice", telegram_chat_id=1)
    steam_rule(alice, "570")
    bsky: Rule = tag_rule(alice, "godot", "growth_pct", "50", 2)
    radar: FakeRadar = FakeRadar(
        players=steam_growth_series(), tags=tag_growth_counts(), failing=frozenset({"players"})
    )
    ids: list[int] = run_check(radar, NOW)
    firing: Firing = Firing.objects.get()
    assert ids == [firing.pk]
    assert firing.rule == bsky
    assert firing.period == H


def test_failing_tags_keep_steam_rules() -> None:
    """Check 21, the other way round."""
    alice: User = make_user("alice", telegram_chat_id=1)
    steam: Rule = steam_rule(alice, "570")
    tag_rule(alice, "godot", "growth_pct", "50", 2)
    radar: FakeRadar = FakeRadar(
        players=steam_growth_series(), tags=tag_growth_counts(), failing=frozenset({"tags"})
    )
    run_check(radar, NOW)
    assert Firing.objects.get().rule == steam


def test_steam_top_creates_firing() -> None:
    """The chart path end to end: period is the chart day."""
    alice: User = make_user("alice", telegram_chat_id=1)
    rule: Rule = steam_rule(alice, "570", kind="top_n", window=None)
    chart: SteamChart = SteamChart(
        days=(at(0, 16), at(0, 15)), ranks={at(0, 16): {"570": 7}, at(0, 15): {"570": 12}}
    )
    run_check(FakeRadar(chart=chart), NOW)
    firing: Firing = Firing.objects.get()
    assert (firing.rule, firing.period) == (rule, at(0, 16))


def test_stale_pending() -> None:
    """Check 22."""
    alice: User = make_user("alice", telegram_chat_id=1)
    rule: Rule = steam_rule(alice, "570")
    cases: dict[str, tuple[str, timedelta]] = {
        "pending 11 min": ("pending", timedelta(minutes=11)),
        "pending 23 h": ("pending", timedelta(hours=23)),
        "pending 5 min": ("pending", timedelta(minutes=5)),
        "pending 25 h": ("pending", timedelta(hours=25)),
        "retry": ("retry", timedelta(minutes=30)),
        "sending": ("sending", timedelta(minutes=30)),
        "sent": ("sent", timedelta(minutes=30)),
        "failed": ("failed", timedelta(minutes=30)),
    }
    ids: dict[str, int] = {}
    index: int = 0
    name: str
    status: str
    age: timedelta
    for name, (status, age) in cases.items():
        index += 1
        firing: Firing = Firing.objects.create(
            rule=rule, period=at(0, 1) + timedelta(hours=index), text="t", status=status
        )
        Firing.objects.filter(pk=firing.pk).update(created_at=NOW - age)
        ids[name] = firing.pk
    assert set(stale_pending(NOW)) == {ids["pending 11 min"], ids["pending 23 h"]}
