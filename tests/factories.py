"""Helpers that build objects through the ORM, bypassing API validation."""

from collections.abc import Callable
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from radar_alerts.alerts.models import Rule, Subscription, User

type Resp = Any

PASSWORD: str = "pw-12345-secret"
DEFAULT_TAGS: tuple[str, ...] = (
    "gamedev",
    "indiedev",
    "indiegame",
    "godot",
    "pixelart",
    "screenshotsaturday",
    "mobilegame",
)


def make_user(username: str, telegram_chat_id: int | None = None, is_active: bool = True) -> User:
    return User.objects.create_user(
        username=username, password=PASSWORD, telegram_chat_id=telegram_chat_id, is_active=is_active
    )


def make_sub(
    user: User,
    source: str = "steam",
    entity_type: str = "game",
    entity_id: str = "570",
    **extra: object,
) -> Subscription:
    return Subscription.objects.create(
        user=user, source=source, entity_type=entity_type, entity_id=entity_id, **extra
    )


def make_rule(
    subscription: Subscription,
    kind: str = "growth_pct",
    threshold: Decimal = Decimal("30"),
    window_hours: int | None = 24,
    **extra: object,
) -> Rule:
    return Rule.objects.create(
        subscription=subscription, kind=kind, threshold=threshold, window_hours=window_hours, **extra
    )


def api_for(user: User) -> APIClient:
    token: Token = Token.objects.create(user=user)
    client: APIClient = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    return client


def error_keys(call: Callable[[], object]) -> set[str]:
    """Keys of the ValidationError raised by call(); empty set if nothing was raised."""
    try:
        call()
    except ValidationError as exc:
        return set(exc.error_dict)
    return set()
