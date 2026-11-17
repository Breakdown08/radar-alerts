"""Checks 46, 47: a double POST of one subscription is a 400, not a 500."""

import threading

import pytest
from django.db import connection, connections
from factories import Resp, make_sub, make_user
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from radar_alerts.alerts.models import Subscription, User
from radar_alerts.alerts.serializers import DUPLICATE_MESSAGE

URL: str = "/api/v1/subscriptions/"
STEAM: dict[str, object] = {"source": "steam", "entity_type": "game", "entity_id": "570"}


@pytest.mark.django_db
@pytest.mark.usefixtures("default_tags")
def test_duplicate_that_slips_past_the_precheck(
    monkeypatch: pytest.MonkeyPatch, user_a: User, client_a: APIClient
) -> None:
    """Check 46: the pre-check is blind, the unique constraint decides, the answer is still 400."""
    make_sub(user_a)
    asked: list[tuple[object, ...]] = []

    def blind(*args: object) -> bool:
        asked.append(args)
        return False

    monkeypatch.setattr("radar_alerts.alerts.serializers.target_taken", blind)
    resp: Resp = client_a.post(URL, STEAM, format="json")
    assert len(asked) == 1
    assert resp.status_code == 400
    assert resp.json() == {"non_field_errors": [DUPLICATE_MESSAGE]}
    assert Subscription.objects.filter(user=user_a).count() == 1
    assert client_a.get(URL).status_code == 200


@pytest.mark.django_db
@pytest.mark.usefixtures("default_tags")
def test_precheck_still_rejects_duplicates(user_a: User, client_a: APIClient) -> None:
    """Check 46: behaviour of task 1 is unchanged, with the shared message."""
    assert client_a.post(URL, STEAM, format="json").status_code == 201
    resp: Resp = client_a.post(URL, STEAM, format="json")
    assert resp.status_code == 400
    assert resp.json() == {"non_field_errors": [DUPLICATE_MESSAGE]}


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
@pytest.mark.usefixtures("default_tags")
def test_two_simultaneous_posts() -> None:
    """Check 47: one 201, one 400, never a 500."""
    if connection.vendor != "postgresql":
        pytest.skip("needs PostgreSQL")
    user: User = make_user("alice")
    token: str = Token.objects.create(user=user).key
    barrier: threading.Barrier = threading.Barrier(2)
    codes: list[int] = []

    def worker() -> None:
        client: APIClient = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        try:
            barrier.wait(timeout=10)
            codes.append(client.post(URL, STEAM, format="json").status_code)
        finally:
            connections.close_all()

    threads: list[threading.Thread] = [threading.Thread(target=worker) for _ in range(2)]
    thread: threading.Thread
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(codes) == [201, 400]
    assert Subscription.objects.filter(user=user).count() == 1
