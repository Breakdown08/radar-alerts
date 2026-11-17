"""Check 48: Firing in the admin is read-only; LinkCode and BotCursor are not there."""

import pytest
from django.test import Client
from factories import Resp
from fakes import World

from radar_alerts.alerts.models import Firing

pytestmark: list[pytest.MarkDecorator] = [pytest.mark.django_db, pytest.mark.usefixtures("default_tags")]


def test_firing_pages(admin_client: Client) -> None:
    world: World = World()
    firing: Firing = world.firing()
    assert admin_client.get("/admin/alerts/firing/").status_code == 200
    assert admin_client.get("/admin/alerts/firing/?status=pending").status_code == 200
    assert admin_client.get(f"/admin/alerts/firing/{firing.pk}/change/").status_code == 200


def test_firing_cannot_be_added(admin_client: Client) -> None:
    assert admin_client.get("/admin/alerts/firing/add/").status_code == 403


def test_firing_cannot_be_changed(admin_client: Client) -> None:
    world: World = World()
    firing: Firing = world.firing(text="original")
    resp: Resp = admin_client.post(
        f"/admin/alerts/firing/{firing.pk}/change/", {"text": "edited", "status": "sent"}
    )
    assert resp.status_code == 403
    firing.refresh_from_db()
    assert (firing.text, firing.status) == ("original", "pending")


def test_firing_can_be_deleted(admin_client: Client) -> None:
    world: World = World()
    firing: Firing = world.firing()
    resp: Resp = admin_client.post(f"/admin/alerts/firing/{firing.pk}/delete/", {"post": "yes"})
    assert resp.status_code == 302
    assert Firing.objects.count() == 0


@pytest.mark.parametrize("model", ["linkcode", "botcursor"])
def test_helper_models_are_not_in_the_admin(admin_client: Client, model: str) -> None:
    assert admin_client.get(f"/admin/alerts/{model}/").status_code == 404
