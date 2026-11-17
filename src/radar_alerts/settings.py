import os
import re
from pathlib import Path
from urllib.parse import quote

import django_stubs_ext
from django.core.exceptions import ImproperlyConfigured

django_stubs_ext.monkeypatch()

BASE_DIR: Path = Path(__file__).resolve().parents[2]

SECRET_KEY: str = os.environ.get("DJANGO_SECRET_KEY", "django-insecure-dev-only")
DEBUG: bool = os.environ.get("DJANGO_DEBUG", "") == "1"
ALLOWED_HOSTS: list[str] = [
    host.strip() for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
]

DEFAULT_BLUESKY_TAGS: str = "gamedev,indiedev,indiegame,godot,pixelart,screenshotsaturday,mobilegame"
_TAG_RE: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_]{0,27}$")


def _parse_tags(raw: str) -> tuple[str, ...]:
    tags: tuple[str, ...] = tuple(tag for tag in (part.strip().lower() for part in raw.split(",")) if tag)
    tag: str
    for tag in tags:
        if not _TAG_RE.match(tag):
            raise ImproperlyConfigured(f"RADAR_BLUESKY_TAGS: invalid tag {tag!r}")
    return tags


RADAR_BLUESKY_TAGS: tuple[str, ...] = _parse_tags(os.environ.get("RADAR_BLUESKY_TAGS", DEFAULT_BLUESKY_TAGS))

INSTALLED_APPS: list[str] = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "radar_alerts.alerts",
]

MIDDLEWARE: list[str] = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF: str = "radar_alerts.urls"
WSGI_APPLICATION: str = "radar_alerts.wsgi.application"

TEMPLATES: list[dict[str, object]] = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

POSTGRES_HOST: str = os.environ.get("POSTGRES_HOST", "")
DATABASES: dict[str, dict[str, object]]
if POSTGRES_HOST:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": POSTGRES_HOST,
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
            "NAME": os.environ.get("POSTGRES_DB", "radar_alerts"),
            "USER": os.environ.get("POSTGRES_USER", "radar_alerts"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        }
    }
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

AUTH_USER_MODEL: str = "alerts.User"
AUTH_PASSWORD_VALIDATORS: list[dict[str, str]] = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

USE_TZ: bool = True
TIME_ZONE: str = "UTC"
LANGUAGE_CODE: str = "en-us"
STATIC_URL: str = "static/"
DEFAULT_AUTO_FIELD: str = "django.db.models.BigAutoField"

REST_FRAMEWORK: dict[str, object] = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.TokenAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PAGINATION_CLASS": "radar_alerts.alerts.pagination.Pagination",
}


def amqp_url(user: str, password: str, host: str, port: str) -> str:
    """amqp://user:password@host:port// with user and password percent-encoded."""
    return f"amqp://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}//"


RABBITMQ_HOST: str = os.environ.get("RABBITMQ_HOST", "localhost")
RABBITMQ_PORT: str = os.environ.get("RABBITMQ_PORT", "5672")
RABBITMQ_USER: str = os.environ.get("RABBITMQ_USER", "radar_alerts")
RABBITMQ_PASSWORD: str = os.environ.get("RABBITMQ_PASSWORD", "")

CELERY_BROKER_URL: str = amqp_url(RABBITMQ_USER, RABBITMQ_PASSWORD, RABBITMQ_HOST, RABBITMQ_PORT)
CELERY_TASK_IGNORE_RESULT: bool = True
CELERY_TASK_ACKS_LATE: bool = True
CELERY_WORKER_PREFETCH_MULTIPLIER: int = 1
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP: bool = True
CELERY_TIMEZONE: str = "UTC"
CELERY_BEAT_SCHEDULE: dict[str, dict[str, object]] = {
    "check-rules": {"task": "radar_alerts.alerts.tasks.check_rules", "schedule": 300.0},
    "poll-telegram": {
        "task": "radar_alerts.alerts.tasks.poll_telegram",
        "schedule": 10.0,
        "options": {"expires": 9},
    },
}

TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_BOT_USERNAME: str = os.environ.get("TELEGRAM_BOT_USERNAME", "").lstrip("@")

CLICKHOUSE_URL: str = os.environ.get("CLICKHOUSE_URL", "http://localhost:8123").rstrip("/")
CLICKHOUSE_USER: str = os.environ.get("CLICKHOUSE_USER", "radar")
CLICKHOUSE_PASSWORD: str = os.environ.get("CLICKHOUSE_PASSWORD", "")
CLICKHOUSE_DATABASE: str = os.environ.get("CLICKHOUSE_DATABASE", "radar")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,63}", CLICKHOUSE_DATABASE):
    raise ImproperlyConfigured(f"CLICKHOUSE_DATABASE: invalid name {CLICKHOUSE_DATABASE!r}")
