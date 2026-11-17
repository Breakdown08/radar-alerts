"""Checks 4 and 5: GET /healthz."""

import pytest
from django.test import Client
from factories import Resp

from radar_alerts import health

pytestmark: list[pytest.MarkDecorator] = [pytest.mark.django_db]


def test_healthz_ok_without_credentials() -> None:
    """Check 4."""
    response: Resp = Client().get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_rejects_other_methods() -> None:
    """Check 4."""
    assert Client().post("/healthz").status_code == 405


def test_healthz_reports_unavailable_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check 5: the view looks database_ok up on the module at call time."""
    monkeypatch.setattr("radar_alerts.health.database_ok", lambda: False)
    response: Resp = Client().get("/healthz")
    assert response.status_code == 503
    assert response.json() == {"status": "database unavailable"}


def test_database_ok_on_the_test_database() -> None:
    """Check 5."""
    assert health.database_ok() is True
