from decimal import Decimal

import pytest
from factories import Resp, make_rule, make_sub
from rest_framework.test import APIClient

from radar_alerts.alerts.models import Rule, Subscription, User

pytestmark: list[pytest.MarkDecorator] = [pytest.mark.django_db, pytest.mark.usefixtures("default_tags")]

SUBS: str = "/api/v1/subscriptions/"
RULES: str = "/api/v1/rules/"


def make_many(user: User, n: int) -> list[Subscription]:
    return [make_sub(user, entity_id=str(1000 + i)) for i in range(n)]


def test_pagination(user_a: User, client_a: APIClient) -> None:
    make_many(user_a, 25)
    first: Resp = client_a.get(SUBS).json()
    assert first["count"] == 25
    assert len(first["results"]) == 20
    assert first["next"] is not None
    assert first["previous"] is None
    assert len(client_a.get(SUBS, {"page": 2}).json()["results"]) == 5
    assert len(client_a.get(SUBS, {"page_size": 5}).json()["results"]) == 5
    assert len(client_a.get(SUBS, {"page_size": 500}).json()["results"]) == 25
    assert client_a.get(SUBS, {"page": 9}).status_code == 404


def test_order_is_descending_id(user_a: User, client_a: APIClient) -> None:
    make_many(user_a, 5)
    ids: list[int] = [s["id"] for s in client_a.get(SUBS).json()["results"]]
    assert len(ids) == 5
    assert ids == sorted(ids, reverse=True)


def test_subscription_filters(user_a: User, client_a: APIClient) -> None:
    make_sub(user_a, "steam", "game", "570")
    make_sub(user_a, "steam", "game", "730", is_active=False)
    make_sub(user_a, "bluesky", "tag", "gamedev")
    make_sub(user_a, "bluesky", "tag", "godot", is_active=False)

    def ids(**params: str) -> list[str]:
        results: Resp = client_a.get(SUBS, params).json()["results"]
        return sorted(s["entity_id"] for s in results)

    assert ids(source="steam") == ["570", "730"]
    assert ids(entity_type="tag") == ["gamedev", "godot"]
    assert ids(entity_id="570") == ["570"]
    assert ids(is_active="false") == ["730", "godot"]
    assert ids(is_active="true") == ["570", "gamedev"]
    assert ids(source="bluesky", is_active="false") == ["godot"]


@pytest.mark.parametrize("param", ["source", "entity_type", "is_active"])
def test_subscription_filter_errors(client_a: APIClient, param: str) -> None:
    bad: dict[str, str] = {"source": "epic", "entity_type": "post", "is_active": "yes"}
    resp: Resp = client_a.get(SUBS, {param: bad[param]})
    assert resp.status_code == 400
    assert param in resp.json()


def test_rule_filters(user_a: User, user_b: User, client_a: APIClient) -> None:
    sub1: Subscription = make_sub(user_a, entity_id="570")
    sub2: Subscription = make_sub(user_a, entity_id="730")
    foreign: Subscription = make_sub(user_b)
    r1: Rule = make_rule(sub1, "growth_pct")
    r2: Rule = make_rule(sub1, "top_n", Decimal("10"), None, is_active=False)
    r3: Rule = make_rule(sub2, "growth_pct")
    make_rule(foreign)

    def ids(**params: str | int) -> list[int]:
        return sorted(r["id"] for r in client_a.get(RULES, params).json()["results"])

    assert ids(subscription=sub1.pk) == sorted([r1.pk, r2.pk])
    assert ids(kind="top_n") == [r2.pk]
    assert ids(is_active="true") == sorted([r1.pk, r3.pk])
    assert ids(subscription=sub1.pk, kind="growth_pct") == [r1.pk]
    bad: Resp = client_a.get(RULES, {"subscription": "abc"})
    assert bad.status_code == 400
    assert "subscription" in bad.json()
    foreign_resp: Resp = client_a.get(RULES, {"subscription": foreign.pk})
    assert foreign_resp.status_code == 200
    assert foreign_resp.json()["count"] == 0
