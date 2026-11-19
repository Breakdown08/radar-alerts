"""Checks 10 to 16: Django behind the nginx proxy."""

import json
import re

import pytest
from django.conf import settings as django_settings
from django.test import Client, RequestFactory
from factories import Resp
from pytest_django.fixtures import Settings

PROXY_HOST: str = "127.0.0.1:8080"


def test_proxy_settings() -> None:
    """Check 10."""
    assert django_settings.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")
    assert getattr(django_settings, "USE_X_FORWARDED_HOST", False) is False


def test_is_secure_follows_forwarded_proto() -> None:
    """Check 11."""
    factory: RequestFactory = RequestFactory()
    assert factory.get("/", HTTP_X_FORWARDED_PROTO="https").is_secure() is True
    assert factory.get("/").is_secure() is False
    assert factory.get("/", HTTP_X_FORWARDED_PROTO="http").is_secure() is False


def test_host_keeps_port_and_ignores_forwarded_host() -> None:
    """Check 12."""
    factory: RequestFactory = RequestFactory()
    assert factory.get("/", HTTP_HOST=PROXY_HOST).get_host() == PROXY_HOST
    spoofed: str = factory.get("/", HTTP_HOST=PROXY_HOST, HTTP_X_FORWARDED_HOST="evil.example").get_host()
    assert spoofed == PROXY_HOST


@pytest.mark.django_db
def test_healthz_through_proxy() -> None:
    """Check 13."""
    response: Resp = Client().get(
        "/healthz",
        HTTP_HOST=PROXY_HOST,
        HTTP_X_FORWARDED_FOR="203.0.113.7",
        HTTP_X_FORWARDED_PROTO="http",
    )
    assert response.status_code == 200
    assert json.loads(response.content) == {"status": "ok"}


@pytest.mark.django_db
@pytest.mark.usefixtures("default_tags")
def test_webhook_through_proxy(settings: Settings) -> None:
    """Check 14."""
    settings.TELEGRAM_MODE = "webhook"
    settings.TELEGRAM_WEBHOOK_SECRET = "s3cret"
    client: Client = Client()

    def post(secret: str) -> Resp:
        return client.post(
            "/telegram/webhook/",
            data=json.dumps({"update_id": 1}),
            content_type="application/json",
            headers={"X-Telegram-Bot-Api-Secret-Token": secret},
            HTTP_HOST=PROXY_HOST,
            HTTP_X_FORWARDED_PROTO="https",
            HTTP_X_FORWARDED_FOR="149.154.167.220",
        )

    assert post("wrong").status_code == 403
    assert post("s3cret").status_code == 200


def login_post(host: str) -> Resp:
    client: Client = Client(enforce_csrf_checks=True)
    first: Resp = client.get("/admin/login/", HTTP_HOST=PROXY_HOST)
    assert first.status_code == 200
    token: str = client.cookies["csrftoken"].value
    assert re.fullmatch(r"[A-Za-z0-9]+", token)
    return client.post(
        "/admin/login/",
        {"csrfmiddlewaretoken": token, "username": "nobody", "password": "wrong"},
        HTTP_HOST=host,
        HTTP_ORIGIN=f"http://{PROXY_HOST}",
    )


@pytest.mark.django_db
def test_admin_csrf_with_port_in_host() -> None:
    """Check 15."""
    assert login_post(PROXY_HOST).status_code != 403


@pytest.mark.django_db
def test_admin_csrf_fails_when_port_is_dropped() -> None:
    """Check 16: why nginx passes $http_host, not $host."""
    assert login_post("127.0.0.1").status_code == 403
