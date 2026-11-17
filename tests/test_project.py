import pytest
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from factories import DEFAULT_TAGS


@pytest.mark.django_db
def test_migrations_match_models() -> None:
    """Check 3: the alerts app has models and a committed migration, and nothing is pending."""
    assert {m.__name__ for m in apps.get_app_config("alerts").get_models()} == {
        "User",
        "Subscription",
        "Rule",
    }
    call_command("makemigrations", "--check", "--dry-run")


def test_django_check_passes() -> None:
    """Check 4: the project is wired up (admin and auth apps included) and `check` is clean."""
    assert {"django.contrib.admin", "rest_framework", "rest_framework.authtoken"} <= set(
        settings.INSTALLED_APPS
    )
    assert "radar_alerts.alerts" in settings.INSTALLED_APPS
    call_command("check")


def test_auth_user_model() -> None:
    """Check 5."""
    assert settings.AUTH_USER_MODEL == "alerts.User"
    model: type = get_user_model()
    assert f"{model.__module__}.{model.__name__}" == "radar_alerts.alerts.models.User"


@pytest.mark.postgres
def test_database_is_postgres() -> None:
    """Check 6: proves the postgres CI job really runs on PostgreSQL."""
    if connection.vendor != "postgresql":
        pytest.skip("needs PostgreSQL")
    assert connection.vendor == "postgresql"


def test_default_bluesky_tags() -> None:
    """Check 16: the radar's seven tags, in order."""
    assert settings.RADAR_BLUESKY_TAGS == DEFAULT_TAGS
    assert isinstance(settings.RADAR_BLUESKY_TAGS, tuple)
