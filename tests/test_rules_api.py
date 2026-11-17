import pytest
from factories import Resp, make_rule, make_sub
from rest_framework.test import APIClient

from radar_alerts.alerts.models import Rule, Subscription, User

pytestmark: list[pytest.MarkDecorator] = [pytest.mark.django_db, pytest.mark.usefixtures("default_tags")]

URL: str = "/api/v1/rules/"
FIELDS: set[str] = {
    "id",
    "subscription",
    "kind",
    "threshold",
    "window_hours",
    "is_active",
    "created_at",
    "updated_at",
}


def test_create_growth_pct(user_a: User, client_a: APIClient) -> None:
    sub: Subscription = make_sub(user_a)
    payload: dict[str, object] = {
        "subscription": sub.pk,
        "kind": "growth_pct",
        "threshold": 30,
        "window_hours": 24,
    }
    resp: Resp = client_a.post(URL, payload, format="json")
    assert resp.status_code == 201
    body: Resp = resp.json()
    assert set(body) == FIELDS
    assert body["threshold"] == "30.00"
    assert body["window_hours"] == 24
    assert body["subscription"] == sub.pk


def test_steam_top_n(user_a: User, client_a: APIClient) -> None:
    sub: Subscription = make_sub(user_a)
    base: dict[str, object] = {"subscription": sub.pk, "kind": "top_n"}
    ok: Resp = client_a.post(URL, {**base, "threshold": 10, "window_hours": None}, format="json")
    assert ok.status_code == 201
    assert ok.json()["window_hours"] is None
    window: Resp = client_a.post(URL, {**base, "threshold": 10, "window_hours": 24}, format="json")
    assert window.status_code == 400
    assert "window_hours" in window.json()
    frac: Resp = client_a.post(URL, {**base, "threshold": "10.5", "window_hours": None}, format="json")
    assert frac.status_code == 400
    assert "threshold" in frac.json()


def test_bluesky_top_n(user_a: User, client_a: APIClient) -> None:
    sub: Subscription = make_sub(user_a, "bluesky", "tag", "gamedev")
    base: dict[str, object] = {"subscription": sub.pk, "kind": "top_n"}
    no_window: Resp = client_a.post(URL, {**base, "threshold": 3}, format="json")
    assert no_window.status_code == 400
    assert "window_hours" in no_window.json()
    ok: Resp = client_a.post(URL, {**base, "threshold": 3, "window_hours": 24}, format="json")
    assert ok.status_code == 201
    too_big: Resp = client_a.post(URL, {**base, "threshold": 7, "window_hours": 24}, format="json")
    assert too_big.status_code == 400
    assert "threshold" in too_big.json()


def test_foreign_and_missing_subscription_look_the_same(user_b: User, client_a: APIClient) -> None:
    foreign: Subscription = make_sub(user_b)
    base: dict[str, object] = {"kind": "growth_pct", "threshold": 30, "window_hours": 24}
    first: Resp = client_a.post(URL, {**base, "subscription": foreign.pk}, format="json")
    second: Resp = client_a.post(URL, {**base, "subscription": 999999}, format="json")
    assert first.status_code == 400
    assert second.status_code == 400
    assert "subscription" in first.json()
    assert first.json()["subscription"] == second.json()["subscription"]


def test_unknown_kind(user_a: User, client_a: APIClient) -> None:
    sub: Subscription = make_sub(user_a)
    resp: Resp = client_a.post(
        URL, {"subscription": sub.pk, "kind": "above", "threshold": 1, "window_hours": 1}, format="json"
    )
    assert resp.status_code == 400
    assert "kind" in resp.json()


def test_patch_validates_merged_state(user_a: User, client_a: APIClient) -> None:
    rule: Rule = make_rule(make_sub(user_a))
    url: str = f"{URL}{rule.pk}/"
    bad: Resp = client_a.patch(url, {"kind": "top_n"}, format="json")
    assert bad.status_code == 400
    assert "window_hours" in bad.json()
    rule.refresh_from_db()
    assert rule.kind == "growth_pct"
    good: Resp = client_a.patch(url, {"kind": "top_n", "threshold": 10, "window_hours": None}, format="json")
    assert good.status_code == 200
    rule.refresh_from_db()
    assert rule.kind == "top_n"
    assert rule.window_hours is None


def test_rule_cannot_move_between_subscriptions(user_a: User, client_a: APIClient) -> None:
    sub: Subscription = make_sub(user_a)
    other: Subscription = make_sub(user_a, entity_id="730")
    rule: Rule = make_rule(sub)
    url: str = f"{URL}{rule.pk}/"
    moved: Resp = client_a.patch(url, {"subscription": other.pk}, format="json")
    assert moved.status_code == 400
    assert "subscription" in moved.json()
    rule.refresh_from_db()
    assert rule.subscription_id == sub.pk
    assert client_a.patch(url, {"is_active": False}, format="json").status_code == 200
