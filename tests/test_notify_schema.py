"""Checks 2 (migration 0001), 4 to 7: Firing model, broker settings, beat schedule, settings validation."""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.conf import settings
from django.db import IntegrityError, transaction
from factories import make_rule
from fakes import H, World, at

import radar_alerts
from radar_alerts.alerts.models import Firing, Rule
from radar_alerts.settings import amqp_url

ROOT: Path = Path(__file__).resolve().parents[1]
INITIAL_SHA256: str = "0c8979278a3acca9bbda5ec3811103ce197ead1ca27f19b73729e363e73608e6"


def test_initial_migration_is_unchanged() -> None:
    """Check 2: 0001_initial is not edited, 0002 only adds."""
    path: Path = ROOT / "src/radar_alerts/alerts/migrations/0001_initial.py"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == INITIAL_SHA256
    assert list(path.parent.glob("0002_*.py")) != []


@pytest.mark.django_db
def test_firing_unique_per_rule_and_period() -> None:
    """Check 4."""
    world: World = World()
    world.firing(H)
    with pytest.raises(IntegrityError), transaction.atomic():
        world.firing(H)
    other: Firing = world.firing(at(13))
    assert other.pk is not None
    assert Firing.objects.count() == 2


@pytest.mark.django_db
def test_firing_defaults() -> None:
    """Check 4: the defaults of the model."""
    world: World = World()
    firing: Firing = world.firing()
    firing.refresh_from_db()
    assert (firing.status, firing.attempts, firing.last_error) == ("pending", 0, "")
    assert firing.telegram_message_id is None
    assert firing.sent_at is None
    assert firing.created_at is not None


@pytest.mark.django_db
def test_same_period_allowed_for_other_rule() -> None:
    """Check 4: uniqueness is per rule."""
    world: World = World()
    second: Rule = make_rule(world.subscription)
    world.firing(H)
    Firing.objects.create(rule=second, period=H, text="x")
    assert Firing.objects.count() == 2


@pytest.mark.django_db
def test_deleting_rule_deletes_firings() -> None:
    """Check 4."""
    world: World = World()
    world.firing(H)
    world.firing(at(13))
    world.rule.delete()
    assert Firing.objects.count() == 0


def test_amqp_url_encodes_credentials() -> None:
    """Check 5."""
    assert (
        amqp_url("radar_alerts", "p@ss/w:rd", "localhost", "5672")
        == "amqp://radar_alerts:p%40ss%2Fw%3Ard@localhost:5672//"
    )


def test_broker_url_is_amqp() -> None:
    """Check 5."""
    assert settings.CELERY_BROKER_URL.startswith("amqp://")


def test_celery_flags() -> None:
    """The fixed Celery settings from the spec."""
    assert settings.CELERY_TASK_IGNORE_RESULT is True
    assert settings.CELERY_TASK_ACKS_LATE is True
    assert settings.CELERY_WORKER_PREFETCH_MULTIPLIER == 1
    assert settings.CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP is True
    assert settings.CELERY_TIMEZONE == "UTC"


def test_beat_schedule() -> None:
    """Check 6."""
    schedule: dict[str, dict[str, object]] = settings.CELERY_BEAT_SCHEDULE
    assert schedule["check-rules"]["task"] == "radar_alerts.alerts.tasks.check_rules"
    assert schedule["check-rules"]["schedule"] == 300.0
    assert schedule["poll-telegram"]["task"] == "radar_alerts.alerts.tasks.poll_telegram"
    assert schedule["poll-telegram"]["schedule"] == 10.0
    options: object = schedule["poll-telegram"]["options"]
    assert isinstance(options, dict)
    assert options["expires"] == 9


def test_celery_app_knows_the_tasks() -> None:
    """Check 6: the app is configured from Django settings and discovers all three tasks."""
    radar_alerts.celery_app.loader.import_default_modules()
    names: set[str] = set(radar_alerts.celery_app.tasks)
    assert {
        "radar_alerts.alerts.tasks.check_rules",
        "radar_alerts.alerts.tasks.deliver_firing",
        "radar_alerts.alerts.tasks.poll_telegram",
    } <= names
    assert radar_alerts.celery_app.conf.broker_url == settings.CELERY_BROKER_URL
    assert radar_alerts.celery_app.conf.task_ignore_result is True


def run_settings(code: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.mark.parametrize("name", ["bad-name", "Radar", "radar; DROP TABLE x", "1radar"])
def test_clickhouse_database_name_is_validated(name: str) -> None:
    """Check 7: the name goes into SQL, so a bad one stops the import of settings."""
    result: subprocess.CompletedProcess[str] = run_settings(
        "import radar_alerts.settings", CLICKHOUSE_DATABASE=name
    )
    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr


def test_clickhouse_database_name_accepts_valid() -> None:
    result: subprocess.CompletedProcess[str] = run_settings(
        "import radar_alerts.settings", CLICKHOUSE_DATABASE="radar_2"
    )
    assert result.returncode == 0, result.stderr


def test_clickhouse_url_loses_trailing_slash() -> None:
    result: subprocess.CompletedProcess[str] = run_settings(
        "import radar_alerts.settings as s; print(s.CLICKHOUSE_URL)", CLICKHOUSE_URL="http://ch:8123/"
    )
    assert result.stdout.strip() == "http://ch:8123"


def test_telegram_username_loses_at_sign() -> None:
    result: subprocess.CompletedProcess[str] = run_settings(
        "import radar_alerts.settings as s; print(s.TELEGRAM_BOT_USERNAME)",
        TELEGRAM_BOT_USERNAME="@radar_alerts_bot",
    )
    assert result.stdout.strip() == "radar_alerts_bot"
