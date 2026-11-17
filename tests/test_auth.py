import pytest
from factories import PASSWORD, Resp, api_for, make_rule, make_sub, make_user
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from radar_alerts.alerts.models import Rule, Subscription, User

pytestmark: list[pytest.MarkDecorator] = [pytest.mark.django_db, pytest.mark.usefixtures("default_tags")]

TOKEN_URL: str = "/api/v1/auth/token/"


def test_token_obtain(user_a: User) -> None:
    client: APIClient = APIClient()
    first: Resp = client.post(TOKEN_URL, {"username": "alice", "password": PASSWORD}, format="json")
    assert first.status_code == 200
    assert first.json()["token"] == Token.objects.get(user=user_a).key
    second: Resp = client.post(TOKEN_URL, {"username": "alice", "password": PASSWORD}, format="json")
    assert second.json()["token"] == first.json()["token"]


def test_token_wrong_password(user_a: User) -> None:
    client: APIClient = APIClient()
    resp: Resp = client.post(TOKEN_URL, {"username": "alice", "password": "nope"}, format="json")
    assert resp.status_code == 400


def test_token_inactive_user() -> None:
    make_user("dormant", is_active=False)
    resp: Resp = APIClient().post(TOKEN_URL, {"username": "dormant", "password": PASSWORD}, format="json")
    assert resp.status_code == 400


@pytest.mark.parametrize("url", ["/api/v1/subscriptions/", "/api/v1/rules/", "/api/v1/me/"])
def test_requires_token(url: str) -> None:
    anon: APIClient = APIClient()
    assert anon.get(url).status_code == 401
    bad: APIClient = APIClient()
    bad.credentials(HTTP_AUTHORIZATION="Token wrong")
    assert bad.get(url).status_code == 401


def test_me(user_a: User, client_a: APIClient) -> None:
    resp: Resp = client_a.get("/api/v1/me/")
    assert resp.status_code == 200
    assert resp.json() == {"id": user_a.pk, "username": "alice", "telegram_chat_id": None}


def test_me_shows_telegram_chat_id() -> None:
    user: User = make_user("tg", telegram_chat_id=-100123)
    assert api_for(user).get("/api/v1/me/").json()["telegram_chat_id"] == -100123


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_me_is_read_only(client_a: APIClient, method: str) -> None:
    resp: Resp = getattr(client_a, method)("/api/v1/me/", {"telegram_chat_id": 5}, format="json")
    assert resp.status_code == 405


def test_other_users_objects_are_404_and_untouched(user_a: User, client_b: APIClient) -> None:
    sub: Subscription = make_sub(user_a)
    rule: Rule = make_rule(sub)
    sub_url: str = f"/api/v1/subscriptions/{sub.pk}/"
    rule_url: str = f"/api/v1/rules/{rule.pk}/"
    assert client_b.get(sub_url).status_code == 404
    assert client_b.patch(sub_url, {"label": "hacked"}, format="json").status_code == 404
    assert client_b.get(rule_url).status_code == 404
    assert client_b.patch(rule_url, {"is_active": False}, format="json").status_code == 404
    assert client_b.delete(sub_url).status_code == 404
    assert client_b.delete(rule_url).status_code == 404
    sub.refresh_from_db()
    rule.refresh_from_db()
    assert sub.label == ""
    assert rule.is_active is True


def test_lists_exclude_other_users(user_a: User, user_b: User, client_b: APIClient) -> None:
    sub_a: Subscription = make_sub(user_a)
    make_rule(sub_a)
    sub_b: Subscription = make_sub(user_b, entity_id="730")
    rule_b: Rule = make_rule(sub_b)
    subs: Resp = client_b.get("/api/v1/subscriptions/").json()
    rules: Resp = client_b.get("/api/v1/rules/").json()
    assert subs["count"] == 1
    assert [s["id"] for s in subs["results"]] == [sub_b.pk]
    assert rules["count"] == 1
    assert [r["id"] for r in rules["results"]] == [rule_b.pk]
