"""Checks 32 to 33, 44 (poll_telegram): Celery tasks in eager mode with a fake radar and Telegram."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from celery.app.utils import Settings as CelerySettings
from django.utils import timezone
from fakes import FakeRadar, FakeTelegram, World, permanent

import radar_alerts
from radar_alerts.alerts.models import Firing
from radar_alerts.alerts.radar import RadarSource
from radar_alerts.alerts.tasks import check_rules, poll_telegram

pytestmark: list[pytest.MarkDecorator] = [pytest.mark.django_db, pytest.mark.usefixtures("default_tags")]


@pytest.fixture
def eager() -> Iterator[None]:
    conf: CelerySettings = radar_alerts.celery_app.conf
    old_eager: object = conf.task_always_eager
    old_propagates: object = conf.task_eager_propagates
    conf.task_always_eager = True
    conf.task_eager_propagates = True
    yield
    conf.task_always_eager = old_eager
    conf.task_eager_propagates = old_propagates


def live_growth_radar() -> FakeRadar:
    """Data that fires steam growth 30% / 24h for 570 at the real evaluation hour (now - 5 min, floored)."""
    moment: datetime = timezone.now().astimezone(UTC) - timedelta(minutes=5)
    hour: datetime = moment.replace(minute=0, second=0, microsecond=0)
    series: dict[datetime, int] = {hour - timedelta(hours=back): 100 for back in range(1, 73)}
    series[hour] = 200
    return FakeRadar(players={"570": series})


def wire(monkeypatch: pytest.MonkeyPatch, radar: RadarSource, telegram: FakeTelegram | None) -> None:
    monkeypatch.setattr("radar_alerts.alerts.tasks.get_radar", lambda: radar)
    monkeypatch.setattr("radar_alerts.alerts.tasks.get_telegram", lambda: telegram)


@pytest.mark.usefixtures("eager")
def test_check_rules_creates_and_delivers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check 32."""
    world: World = World(chat_id=111)
    telegram: FakeTelegram = FakeTelegram()
    wire(monkeypatch, live_growth_radar(), telegram)
    assert check_rules() == 1
    firing: Firing = Firing.objects.get()
    assert firing.rule == world.rule
    assert firing.status == "sent"
    assert telegram.sent == [(111, firing.text)]
    assert check_rules() == 0
    assert len(telegram.sent) == 1
    assert Firing.objects.count() == 1


@pytest.mark.usefixtures("eager")
def test_check_rules_requeues_stale_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check 33."""
    world: World = World(chat_id=111)
    firing: Firing = world.firing(text="stuck")
    Firing.objects.filter(pk=firing.pk).update(created_at=timezone.now() - timedelta(minutes=30))
    telegram: FakeTelegram = FakeTelegram()
    wire(monkeypatch, FakeRadar(), telegram)
    assert check_rules() == 1
    assert telegram.sent == [(111, "stuck")]
    firing.refresh_from_db()
    assert firing.status == "sent"


def test_poll_telegram_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check 44: no token, no work."""
    monkeypatch.setattr("radar_alerts.alerts.tasks.get_telegram", lambda: None)
    assert poll_telegram() == 0


def test_poll_telegram_swallows_telegram_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check 44."""
    telegram: FakeTelegram = FakeTelegram()
    telegram.updates_error = permanent()
    monkeypatch.setattr("radar_alerts.alerts.tasks.get_telegram", lambda: telegram)
    assert poll_telegram() == 0
    assert len(telegram.updates_calls) == 1


def test_poll_telegram_returns_processed_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """The task returns what poll_once processed."""
    telegram: FakeTelegram = FakeTelegram()
    telegram.batches = [[{"update_id": 1, "message": {"text": "hi", "chat": {"id": 5}}}]]
    monkeypatch.setattr("radar_alerts.alerts.tasks.get_telegram", lambda: telegram)
    assert poll_telegram() == 1
