import pytest
from django.test import Client
from factories import Resp, make_rule, make_sub, make_user

from radar_alerts.alerts.models import Rule, Subscription, User

pytestmark: list[pytest.MarkDecorator] = [pytest.mark.django_db, pytest.mark.usefixtures("default_tags")]


@pytest.mark.parametrize("model", ["user", "subscription", "rule"])
@pytest.mark.parametrize("suffix", ["", "add/"])
def test_admin_pages_open(admin_client: Client, model: str, suffix: str) -> None:
    assert admin_client.get(f"/admin/alerts/{model}/{suffix}").status_code == 200


def test_admin_change_subscription_with_inline_rules(admin_client: Client) -> None:
    owner: User = make_user("owner")
    sub: Subscription = make_sub(owner)
    make_rule(sub)
    resp: Resp = admin_client.get(f"/admin/alerts/subscription/{sub.pk}/change/")
    assert resp.status_code == 200
    assert "rules" in resp.content.decode()


def test_admin_rejects_invalid_rule(admin_client: Client) -> None:
    owner: User = make_user("owner")
    sub: Subscription = make_sub(owner)
    resp: Resp = admin_client.post(
        "/admin/alerts/rule/add/",
        {"subscription": sub.pk, "kind": "top_n", "threshold": "10", "window_hours": "24", "is_active": "on"},
    )
    assert resp.status_code == 200
    assert Rule.objects.count() == 0
    assert "window_hours" in resp.context["adminform"].form.errors
