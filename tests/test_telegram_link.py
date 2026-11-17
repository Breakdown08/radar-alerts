"""Checks 38, 39, 45: the telegram-link API."""

import re
from datetime import datetime, timedelta

import pytest
from django.utils import timezone
from factories import Resp, make_user
from pytest_django.fixtures import Settings
from rest_framework.test import APIClient

from radar_alerts.alerts.bot import consume_link_code
from radar_alerts.alerts.models import LinkCode, User

pytestmark: list[pytest.MarkDecorator] = [pytest.mark.django_db, pytest.mark.usefixtures("default_tags")]

URL: str = "/api/v1/me/telegram-link/"
CODE_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_-]{22}$")


def test_issue_code(client_a: APIClient, user_a: User) -> None:
    """Check 38."""
    before: datetime = timezone.now()
    resp: Resp = client_a.post(URL, format="json")
    after: datetime = timezone.now()
    assert resp.status_code == 201
    body: Resp = resp.json()
    assert set(body) == {"code", "expires_at", "deep_link"}
    assert CODE_RE.match(body["code"])
    expires: datetime = datetime.fromisoformat(body["expires_at"])
    assert before + timedelta(minutes=15) - timedelta(seconds=5) <= expires
    assert expires <= after + timedelta(minutes=15) + timedelta(seconds=5)
    assert LinkCode.objects.get(user=user_a).code == body["code"]


def test_deep_link_with_bot_username(client_a: APIClient, settings: Settings) -> None:
    """Check 38."""
    settings.TELEGRAM_BOT_USERNAME = "radar_alerts_bot"
    body: Resp = client_a.post(URL, format="json").json()
    assert body["deep_link"] == f"https://t.me/radar_alerts_bot?start={body['code']}"


def test_deep_link_is_null_without_bot_username(client_a: APIClient, settings: Settings) -> None:
    """Check 38."""
    settings.TELEGRAM_BOT_USERNAME = ""
    resp: Resp = client_a.post(URL, format="json")
    assert resp.status_code == 201
    assert resp.json()["deep_link"] is None


def test_requires_token() -> None:
    """Check 38."""
    assert APIClient().post(URL, format="json").status_code == 401
    assert APIClient().delete(URL).status_code == 401


@pytest.mark.parametrize("method", ["get", "put", "patch"])
def test_other_methods_are_not_allowed(client_a: APIClient, method: str) -> None:
    """Check 38."""
    resp: Resp = getattr(client_a, method)(URL)
    assert resp.status_code == 405


def test_second_code_replaces_the_first(client_a: APIClient, user_a: User) -> None:
    """Check 39."""
    first: str = client_a.post(URL, format="json").json()["code"]
    second: str = client_a.post(URL, format="json").json()["code"]
    assert first != second
    assert list(LinkCode.objects.filter(user=user_a).values_list("code", flat=True)) == [second]
    now: datetime = timezone.now()
    assert consume_link_code(first, now) is None
    assert consume_link_code(second, now) == user_a


def test_codes_of_other_users_are_untouched(client_a: APIClient, client_b: APIClient, user_b: User) -> None:
    """Check 39: issuing deletes only the caller's codes."""
    client_b.post(URL, format="json")
    client_a.post(URL, format="json")
    assert LinkCode.objects.filter(user=user_b).count() == 1


def test_delete_unlinks(client_a: APIClient, user_a: User) -> None:
    """Check 45."""
    User.objects.filter(pk=user_a.pk).update(telegram_chat_id=555)
    other: User = make_user("carol")
    LinkCode.objects.create(user=other, code="keep-me", expires_at=timezone.now() + timedelta(hours=1))
    client_a.post(URL, format="json")
    assert client_a.get("/api/v1/me/").json()["telegram_chat_id"] == 555
    resp: Resp = client_a.delete(URL)
    assert resp.status_code == 204
    user_a.refresh_from_db()
    assert user_a.telegram_chat_id is None
    assert LinkCode.objects.filter(user=user_a).count() == 0
    assert LinkCode.objects.filter(user=other).count() == 1
    assert client_a.get("/api/v1/me/").json()["telegram_chat_id"] is None
