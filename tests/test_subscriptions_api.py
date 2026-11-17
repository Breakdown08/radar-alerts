import pytest
from factories import Resp, make_rule, make_sub
from rest_framework.test import APIClient

from radar_alerts.alerts.models import Rule, Subscription, User

pytestmark: list[pytest.MarkDecorator] = [pytest.mark.django_db, pytest.mark.usefixtures("default_tags")]

URL: str = "/api/v1/subscriptions/"
FIELDS: set[str] = {
    "id",
    "source",
    "entity_type",
    "entity_id",
    "label",
    "is_active",
    "created_at",
    "updated_at",
}
STEAM: dict[str, object] = {"source": "steam", "entity_type": "game", "entity_id": "570"}
TAG: dict[str, object] = {"source": "bluesky", "entity_type": "tag", "entity_id": "#GameDev"}


def test_create(user_a: User, client_a: APIClient) -> None:
    resp: Resp = client_a.post(URL, {**STEAM, "label": "Dota 2"}, format="json")
    assert resp.status_code == 201
    body: Resp = resp.json()
    assert set(body) == FIELDS
    assert body["is_active"] is True
    assert body["entity_id"] == "570"
    assert body["label"] == "Dota 2"
    assert Subscription.objects.get(pk=body["id"]).user == user_a


def test_create_normalizes_and_rejects_duplicate(client_a: APIClient) -> None:
    resp: Resp = client_a.post(URL, TAG, format="json")
    assert resp.status_code == 201
    assert resp.json()["entity_id"] == "gamedev"
    dup: Resp = client_a.post(URL, {**TAG, "entity_id": "gamedev"}, format="json")
    assert dup.status_code == 400
    assert "non_field_errors" in dup.json()


def test_same_target_allowed_for_other_user(client_a: APIClient, client_b: APIClient) -> None:
    assert client_a.post(URL, STEAM, format="json").status_code == 201
    assert client_b.post(URL, STEAM, format="json").status_code == 201


def test_user_field_is_ignored(user_a: User, user_b: User, client_a: APIClient) -> None:
    resp: Resp = client_a.post(URL, {**STEAM, "user": user_b.pk}, format="json")
    assert resp.status_code == 201
    assert Subscription.objects.get(pk=resp.json()["id"]).user == user_a


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"source": "epic", "entity_type": "game", "entity_id": "570"}, "source"),
        ({"source": "steam", "entity_type": "tag", "entity_id": "570"}, "entity_type"),
        ({"source": "steam", "entity_type": "game", "entity_id": "abc"}, "entity_id"),
        ({"source": "steam", "entity_type": "game"}, "entity_id"),
    ],
)
def test_create_errors(client_a: APIClient, payload: dict[str, object], field: str) -> None:
    resp: Resp = client_a.post(URL, payload, format="json")
    assert resp.status_code == 400
    assert field in resp.json()


def test_patch_mutable_fields(user_a: User, client_a: APIClient) -> None:
    sub: Subscription = make_sub(user_a)
    resp: Resp = client_a.patch(f"{URL}{sub.pk}/", {"label": "x", "is_active": False}, format="json")
    assert resp.status_code == 200
    sub.refresh_from_db()
    assert sub.label == "x"
    assert sub.is_active is False


def test_patch_target_is_immutable(user_a: User, client_a: APIClient) -> None:
    sub: Subscription = make_sub(user_a)
    resp: Resp = client_a.patch(f"{URL}{sub.pk}/", {"entity_id": "730"}, format="json")
    assert resp.status_code == 400
    assert "entity_id" in resp.json()
    sub.refresh_from_db()
    assert sub.entity_id == "570"
    resp = client_a.patch(f"{URL}{sub.pk}/", {"source": "bluesky"}, format="json")
    assert resp.status_code == 400
    assert "source" in resp.json()


@pytest.mark.parametrize("entity_id", ["570", "0570"])
def test_put_with_same_target(user_a: User, client_a: APIClient, entity_id: str) -> None:
    sub: Subscription = make_sub(user_a)
    body: dict[str, object] = {**STEAM, "entity_id": entity_id, "label": "Dota", "is_active": True}
    resp: Resp = client_a.put(f"{URL}{sub.pk}/", body, format="json")
    assert resp.status_code == 200
    sub.refresh_from_db()
    assert sub.label == "Dota"
    assert sub.entity_id == "570"


def test_delete_removes_rules(user_a: User, client_a: APIClient) -> None:
    sub: Subscription = make_sub(user_a)
    make_rule(sub)
    resp: Resp = client_a.delete(f"{URL}{sub.pk}/")
    assert resp.status_code == 204
    assert not Subscription.objects.filter(pk=sub.pk).exists()
    assert Rule.objects.count() == 0
