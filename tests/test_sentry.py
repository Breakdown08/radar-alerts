"""Checks 1 to 9: sentry_options and init_sentry."""

import os

import pytest
import sentry_sdk
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.httpx import HttpxIntegration

from radar_alerts.sentry import SentryOptions, init_sentry, sentry_options

DSN: str = "https://k@o1.ingest.sentry.io/1"


def test_no_dsn_gives_none() -> None:
    """Check 1."""
    assert sentry_options({}) is None
    assert sentry_options({"SENTRY_DSN": "  "}) is None


def test_no_dsn_skips_validation() -> None:
    """Check 2."""
    assert sentry_options({"SENTRY_DSN": "", "SENTRY_TRACES_SAMPLE_RATE": "abc"}) is None


def test_only_dsn_uses_defaults() -> None:
    """Check 3."""
    expected: SentryOptions = SentryOptions(
        dsn=DSN, environment="production", release=None, traces_sample_rate=0.0
    )
    assert sentry_options({"SENTRY_DSN": DSN}) == expected
    assert sentry_options({"SENTRY_DSN": f"  {DSN} "}) == expected


def test_all_variables() -> None:
    """Check 4."""
    env: dict[str, str] = {
        "SENTRY_DSN": f" {DSN} ",
        "SENTRY_ENVIRONMENT": "staging",
        "SENTRY_RELEASE": "radar-alerts@0.1.0",
        "SENTRY_TRACES_SAMPLE_RATE": "0.25",
    }
    assert sentry_options(env) == SentryOptions(DSN, "staging", "radar-alerts@0.1.0", 0.25)
    blank: SentryOptions | None = sentry_options(
        {"SENTRY_DSN": DSN, "SENTRY_ENVIRONMENT": "  ", "SENTRY_RELEASE": "  "}
    )
    assert blank is not None
    assert blank.environment == "production"
    assert blank.release is None


@pytest.mark.parametrize(("raw", "rate"), [("0", 0.0), ("1", 1.0), ("1.0", 1.0), ("0.25", 0.25), ("", 0.0)])
def test_valid_rates(raw: str, rate: float) -> None:
    """Check 5."""
    options: SentryOptions | None = sentry_options({"SENTRY_DSN": DSN, "SENTRY_TRACES_SAMPLE_RATE": raw})
    assert options is not None
    assert options.traces_sample_rate == rate


@pytest.mark.parametrize("raw", ["-0.1", "1.5", "abc", "nan", "inf"])
def test_invalid_rates(raw: str) -> None:
    """Check 6."""
    caught: pytest.ExceptionInfo[ImproperlyConfigured]
    with pytest.raises(ImproperlyConfigured) as caught:
        sentry_options({"SENTRY_DSN": DSN, "SENTRY_TRACES_SAMPLE_RATE": raw})
    message: str = str(caught.value)
    assert "SENTRY_TRACES_SAMPLE_RATE must be a number from 0 to 1" in message
    assert DSN not in message
    assert raw not in message.replace("SENTRY_TRACES_SAMPLE_RATE must be a number from 0 to 1", "")


def test_init_without_dsn_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check 7."""
    calls: list[dict[str, object]] = []

    def fake_init(*args: object, **kwargs: object) -> None:
        calls.append({"args": args, **kwargs})

    monkeypatch.setattr("radar_alerts.sentry.sentry_sdk.init", fake_init)
    assert init_sentry({}) is False
    assert calls == []


def test_init_calls_sdk_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check 8."""
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_init(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr("radar_alerts.sentry.sentry_sdk.init", fake_init)
    assert init_sentry({"SENTRY_DSN": DSN, "SENTRY_TRACES_SAMPLE_RATE": "0.5"}) is True
    assert len(calls) == 1
    args: tuple[object, ...] = calls[0][0]
    kwargs: dict[str, object] = calls[0][1]
    assert args == ()
    assert kwargs["dsn"] == DSN
    assert kwargs["environment"] == "production"
    assert kwargs["release"] is None
    assert kwargs["traces_sample_rate"] == 0.5
    assert kwargs["send_default_pii"] is False
    assert kwargs["include_local_variables"] is False
    integrations: object = kwargs["integrations"]
    assert isinstance(integrations, list)
    assert len(integrations) == 2
    assert sum(isinstance(i, DjangoIntegration) for i in integrations) == 1
    assert sum(isinstance(i, CeleryIntegration) for i in integrations) == 1
    disabled: object = kwargs["disabled_integrations"]
    assert isinstance(disabled, list)
    assert len(disabled) == 1
    assert isinstance(disabled[0], HttpxIntegration)


def test_sdk_is_off_in_test_environment() -> None:
    """Check 9."""
    if os.environ.get("SENTRY_DSN", "").strip():
        pytest.skip("SENTRY_DSN is set in the environment")
    assert getattr(settings, "SENTRY_ENABLED", None) is False
    assert sentry_sdk.get_client().is_active() is False
