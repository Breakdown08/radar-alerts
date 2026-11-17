import pytest
from factories import DEFAULT_TAGS, api_for, make_user
from pytest_django.fixtures import Settings
from rest_framework.test import APIClient

from radar_alerts.alerts.models import User


@pytest.fixture
def default_tags(settings: Settings) -> None:
    """Pin the whitelist to the radar defaults, whatever the environment says."""
    settings.RADAR_BLUESKY_TAGS = DEFAULT_TAGS


@pytest.fixture
def user_a() -> User:
    return make_user("alice")


@pytest.fixture
def user_b() -> User:
    return make_user("bob")


@pytest.fixture
def client_a(user_a: User) -> APIClient:
    return api_for(user_a)


@pytest.fixture
def client_b(user_b: User) -> APIClient:
    return api_for(user_b)
