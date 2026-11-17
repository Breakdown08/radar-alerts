"""Checks 23 to 31: backoff, deliver_once and the deliver_firing task with a fake Telegram."""

import pytest
from celery.result import EagerResult
from fakes import NOW, FakeTelegram, World, permanent, temporary

from radar_alerts.alerts.delivery import MAX_DELIVERY_RETRIES, backoff_seconds, deliver_once
from radar_alerts.alerts.models import Firing, User
from radar_alerts.alerts.tasks import deliver_firing
from radar_alerts.alerts.telegram import TelegramError

pytestmark: list[pytest.MarkDecorator] = [pytest.mark.django_db, pytest.mark.usefixtures("default_tags")]


@pytest.mark.parametrize(
    ("retries", "retry_after", "expected"),
    [(0, None, 30), (1, None, 60), (2, None, 120), (3, None, 240), (4, None, 480), (5, None, 900)]
    + [(10, None, 900), (0, 2.2, 3), (3, 0.0, 1), (0, 5.0, 5)],
)
def test_backoff_seconds(retries: int, retry_after: float | None, expected: int) -> None:
    """Check 23."""
    assert backoff_seconds(retries, retry_after) == expected


def use(monkeypatch: pytest.MonkeyPatch, telegram: FakeTelegram | None) -> None:
    monkeypatch.setattr("radar_alerts.alerts.tasks.get_telegram", lambda: telegram)


def run(firing: Firing) -> EagerResult[str]:
    return deliver_firing.apply(args=(firing.pk,))


def test_success() -> None:
    """Check 24."""
    world: World = World(chat_id=111)
    firing: Firing = world.firing(text="hello")
    telegram: FakeTelegram = FakeTelegram()
    assert deliver_once(firing.pk, telegram, NOW) == "sent"
    assert telegram.sent == [(111, "hello")]
    firing.refresh_from_db()
    assert (firing.status, firing.attempts, firing.sent_at) == ("sent", 1, NOW)
    assert firing.telegram_message_id == 777
    assert firing.last_error == ""


def test_second_delivery_is_skipped() -> None:
    """Check 25."""
    world: World = World()
    firing: Firing = world.firing()
    telegram: FakeTelegram = FakeTelegram()
    assert deliver_once(firing.pk, telegram, NOW) == "sent"
    assert deliver_once(firing.pk, telegram, NOW) == "skipped"
    assert len(telegram.sent) == 1
    firing.refresh_from_db()
    assert firing.attempts == 1


@pytest.mark.parametrize("status", ["sending", "failed", "sent"])
def test_taken_or_finished_firing_is_skipped(status: str) -> None:
    """Check 26."""
    world: World = World()
    firing: Firing = world.firing(status=status)
    telegram: FakeTelegram = FakeTelegram()
    assert deliver_once(firing.pk, telegram, NOW) == "skipped"
    assert telegram.send_calls == 0
    firing.refresh_from_db()
    assert (firing.status, firing.attempts) == (status, 0)


def test_missing_firing_is_skipped() -> None:
    """Check 26."""
    telegram: FakeTelegram = FakeTelegram()
    assert deliver_once(999999, telegram, NOW) == "skipped"
    assert telegram.send_calls == 0


def test_retry_status_is_taken_again() -> None:
    """A firing in retry is picked up by the next attempt."""
    world: World = World()
    firing: Firing = world.firing(status="retry")
    telegram: FakeTelegram = FakeTelegram()
    assert deliver_once(firing.pk, telegram, NOW) == "sent"
    assert len(telegram.sent) == 1


def test_two_task_runs_send_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check 27."""
    world: World = World()
    firing: Firing = world.firing()
    telegram: FakeTelegram = FakeTelegram()
    use(monkeypatch, telegram)
    assert run(firing).get() == "sent"
    assert run(firing).get() == "skipped"
    assert len(telegram.sent) == 1


def test_temporary_errors_are_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check 28."""
    world: World = World()
    firing: Firing = world.firing()
    telegram: FakeTelegram = FakeTelegram()
    telegram.errors = [temporary(), temporary(retry_after=1.0)]
    use(monkeypatch, telegram)
    result: EagerResult[str] = run(firing)
    assert result.get() == "sent"
    assert len(telegram.sent) == 1
    assert telegram.send_calls == 3
    firing.refresh_from_db()
    assert (firing.status, firing.attempts) == ("sent", 3)


def test_retries_are_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check 29: 1 + MAX_DELIVERY_RETRIES attempts, then failed, and the task itself succeeds."""
    world: World = World()
    firing: Firing = world.firing()
    telegram: FakeTelegram = FakeTelegram()
    telegram.always = temporary()
    use(monkeypatch, telegram)
    result: EagerResult[str] = run(firing)
    assert result.state == "SUCCESS"
    assert result.get() == "failed"
    assert telegram.send_calls == 1 + MAX_DELIVERY_RETRIES == 6
    firing.refresh_from_db()
    assert (firing.status, firing.attempts) == ("failed", 6)
    assert firing.last_error != ""


def test_permanent_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check 30."""
    world: World = World()
    firing: Firing = world.firing()
    telegram: FakeTelegram = FakeTelegram()
    telegram.always = permanent()
    use(monkeypatch, telegram)
    assert run(firing).get() == "failed"
    assert telegram.send_calls == 1
    firing.refresh_from_db()
    assert (firing.status, firing.attempts) == ("failed", 1)
    assert "403" in firing.last_error


def test_long_error_is_cut_to_1000_characters() -> None:
    """Model rule: last_error is not longer than 1000 characters."""
    world: World = World()
    firing: Firing = world.firing()
    telegram: FakeTelegram = FakeTelegram()
    telegram.always = TelegramError("x" * 5000, retryable=False)
    assert deliver_once(firing.pk, telegram, NOW) == "failed"
    firing.refresh_from_db()
    assert 0 < len(firing.last_error) <= 1000


def test_no_token_fails_the_firing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check 31."""
    world: World = World()
    firing: Firing = world.firing()
    use(monkeypatch, None)
    assert run(firing).get() == "failed"
    firing.refresh_from_db()
    assert (firing.status, firing.last_error) == ("failed", "TELEGRAM_BOT_TOKEN is not set")


def test_chat_unlinked_before_sending(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check 31: the chat is read at the moment of sending."""
    world: World = World()
    firing: Firing = world.firing()
    User.objects.filter(pk=world.user.pk).update(telegram_chat_id=None)
    telegram: FakeTelegram = FakeTelegram()
    use(monkeypatch, telegram)
    assert run(firing).get() == "failed"
    assert telegram.send_calls == 0
    firing.refresh_from_db()
    assert (firing.status, firing.last_error) == ("failed", "Telegram chat is not linked")
