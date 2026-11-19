import math
from collections.abc import Mapping
from dataclasses import dataclass

import sentry_sdk
from django.core.exceptions import ImproperlyConfigured
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.httpx import HttpxIntegration

DEFAULT_ENVIRONMENT: str = "production"


@dataclass(frozen=True)
class SentryOptions:
    dsn: str
    environment: str
    release: str | None
    traces_sample_rate: float


def traces_sample_rate(raw: str) -> float:
    """'' -> 0.0; a finite number from 0 to 1 -> it; anything else raises."""
    if not raw:
        return 0.0
    message: str = "SENTRY_TRACES_SAMPLE_RATE must be a number from 0 to 1"
    try:
        value: float = float(raw)
    except ValueError:
        raise ImproperlyConfigured(message) from None
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ImproperlyConfigured(message)
    return value


def sentry_options(env: Mapping[str, str]) -> SentryOptions | None:
    """None without SENTRY_DSN; otherwise the options read from SENTRY_* variables."""
    dsn: str = env.get("SENTRY_DSN", "").strip()
    if not dsn:
        return None
    return SentryOptions(
        dsn=dsn,
        environment=env.get("SENTRY_ENVIRONMENT", "").strip() or DEFAULT_ENVIRONMENT,
        release=env.get("SENTRY_RELEASE", "").strip() or None,
        traces_sample_rate=traces_sample_rate(env.get("SENTRY_TRACES_SAMPLE_RATE", "").strip()),
    )


def init_sentry(env: Mapping[str, str]) -> bool:
    """Start the SDK if SENTRY_DSN is set. The httpx integration is off: Bot API URLs hold the token."""
    options: SentryOptions | None = sentry_options(env)
    if options is None:
        return False
    sentry_sdk.init(
        dsn=options.dsn,
        environment=options.environment,
        release=options.release,
        traces_sample_rate=options.traces_sample_rate,
        send_default_pii=False,
        include_local_variables=False,
        integrations=[DjangoIntegration(), CeleryIntegration()],
        disabled_integrations=[HttpxIntegration()],
    )
    return True
