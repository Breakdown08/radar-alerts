# radar-alerts

Subscriptions and alert rules for the gamedev-trends radar. A registered user subscribes to a Steam game
or a Bluesky tag and attaches rules to it ("players grew 30% in 24 hours", "the tag entered the top 10").
Django 6.1 + Django REST framework, PostgreSQL (or SQLite for development and tests).

A separate service with its own database, a RabbitMQ broker, a Celery worker and a Celery beat scheduler.
It is tied to the radar only by reading its ClickHouse.

## What is not there yet

- **Missed checks are not caught up.** Every check looks only at the latest period. If the worker was down for
  three hours, transitions inside those hours are not reported.
- **Steam growth assumes the collector polls hourly** (the radar's default `STEAM_POLL_INTERVAL_SECONDS=3600`).
  With another interval a `growth_pct` rule for Steam finds no point `window` hours back and stays silent.
- **Gaps in the Bluesky collection can fake growth.** If the collector stood for part of a window, the tag
  count of that window is low and the next one looks like growth. Collector coverage is not checked.
- **Firing history has no API**, only the admin (`/admin/alerts/firing/`, read only).
- **No open registration.** Users are created by an administrator. This is a decision, not a gap.
- The radar collects only the Steam top-100 chart, so a subscription to a game outside it will get no data.
- The tag whitelist `RADAR_BLUESKY_TAGS` must be kept in sync with the radar's `BLUESKY_TAGS` by hand.
  A `top_n` threshold for a tag is below the number of tags in the list (with 7 tags, "top 7" is always true).

## Run

```
cp .env.example .env    # set POSTGRES_PASSWORD, RABBITMQ_PASSWORD, POSTGRES_HOST=localhost, DJANGO_SECRET_KEY
docker compose up -d db rabbitmq
uv run --env-file .env python manage.py migrate
uv run --env-file .env python manage.py createsuperuser
uv run --env-file .env python manage.py runserver
```

Without `POSTGRES_HOST` the app uses SQLite (`db.sqlite3`), no Docker needed.

## Alerts and the worker

```
uv run --env-file .env celery -A radar_alerts worker -l info
uv run --env-file .env celery -A radar_alerts beat -l info
```

- `web` (Django) stores users, subscriptions and rules in its own PostgreSQL.
- `beat` queues two periodic tasks in RabbitMQ: `check_rules` every 5 minutes and `poll_telegram` every 10 seconds.
- `worker` runs them. `check_rules` takes all active rules, reads the radar's ClickHouse in batches (one query
  per kind of data, not per rule; `GET` requests only, which ClickHouse runs with `readonly=1`) and creates
  a firing per hit. A separate `deliver_firing` task per firing sends one Telegram message.
- Results are not stored in the broker; all state lives in PostgreSQL.

**A firing is a transition.** A rule fires when its condition holds in the current period and did not hold in
the previous one (an hour earlier; for the chart, the previous chart day). "The tag entered the top 10" is
reported once, on entry; "players +30% in 24 hours" is reported once while the growth lasts. A new rule whose
condition already holds stays silent until the next transition. A `(rule, period)` pair fires at most once.

**Delivery.** A temporary failure (connection refused, HTTP 429 or 5xx) is retried with backoff 30 s, 1 min,
2 min, 4 min, 8 min (or Telegram's `retry_after`): at most 6 attempts, then the firing is `failed`. A permanent
failure (chat not found, bot blocked) is `failed` at once. The rule is **at most one message per firing**:
a firing taken by a worker is `sending`, and if the worker dies between sending and recording the result, or
the read of the answer times out, the message is not sent again. Such a firing stays `sending`; look at it
in the admin.

**Linking a chat.** `POST /api/v1/me/telegram-link/` returns a one-time `code` (15 minutes) and a `deep_link`
(if `TELEGRAM_BOT_USERNAME` is set). Send `/start <code>` to the bot; within about 10 seconds the bot answers
and `GET /api/v1/me/` shows `telegram_chat_id`. `DELETE /api/v1/me/telegram-link/` unlinks the chat.
Without `TELEGRAM_BOT_TOKEN` nothing is sent and bot polling does nothing.

## API

All paths except the token need `Authorization: Token <key>`. Lists are paginated
(`page`, `page_size` up to 100, default 20) and ordered by `-id`. You only see your own objects; others give 404.

| Method and path | Purpose |
|---|---|
| `POST /api/v1/auth/token/` | `{"username", "password"}` -> `{"token"}` |
| `GET /api/v1/me/` | current user: `id`, `username`, `telegram_chat_id` |
| `GET, POST /api/v1/subscriptions/` | list (filters `source`, `entity_type`, `entity_id`, `is_active`) and create |
| `GET, PUT, PATCH, DELETE /api/v1/subscriptions/{id}/` | one subscription; the target cannot change |
| `GET, POST /api/v1/rules/` | list (filters `subscription`, `kind`, `is_active`) and create |
| `GET, PUT, PATCH, DELETE /api/v1/rules/{id}/` | one rule; it cannot move to another subscription |
| `POST, DELETE /api/v1/me/telegram-link/` | issue a link code for the bot; unlink the chat |
| `/admin/` | Django admin |

Targets: `steam`/`game` with an appid, `bluesky`/`tag` with a tag from the whitelist (`#GameDev` becomes `gamedev`).
Rules: `growth_pct` (threshold 0..10000, `window_hours` 1..168) and `top_n` (whole threshold; Steam: 1..100 and no
window; Bluesky: 1..number of tags minus one and `window_hours` 1..168).

```
curl -s -X POST localhost:8000/api/v1/auth/token/ -d username=alice -d password=...
curl -s -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
  -d '{"source": "bluesky", "entity_type": "tag", "entity_id": "#GameDev"}' \
  localhost:8000/api/v1/subscriptions/
```

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `DJANGO_SECRET_KEY` | `django-insecure-dev-only` | set your own outside development |
| `DJANGO_DEBUG` | empty | `1` enables debug |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | comma-separated |
| `POSTGRES_HOST` | empty | empty means SQLite; set means PostgreSQL |
| `POSTGRES_PORT` | `5432` | |
| `POSTGRES_DB` | `radar_alerts` | |
| `POSTGRES_USER` | `radar_alerts` | |
| `POSTGRES_PASSWORD` | empty | required by `compose.yaml` |
| `RABBITMQ_HOST` | `localhost` | |
| `RABBITMQ_PORT` | `5672` | |
| `RABBITMQ_USER` | `radar_alerts` | |
| `RABBITMQ_PASSWORD` | empty | required by `compose.yaml` |
| `CLICKHOUSE_URL` | `http://localhost:8123` | the radar's ClickHouse |
| `CLICKHOUSE_USER` | `radar` | |
| `CLICKHOUSE_PASSWORD` | empty | |
| `CLICKHOUSE_DATABASE` | `radar` | letters, digits and `_`; goes into SQL |
| `TELEGRAM_BOT_TOKEN` | empty | empty means no alerts are sent |
| `TELEGRAM_BOT_USERNAME` | empty | bot name without `@`, for `deep_link` |
| `RADAR_BLUESKY_TAGS` | `gamedev,indiedev,indiegame,godot,pixelart,screenshotsaturday,mobilegame` | tags the radar collects |

## Development

```
uv sync
uv run ruff check && uv run ruff format --check && uv run ty check && uv run mypy && uv run pytest
```

Tests run on SQLite by default; with `POSTGRES_*` set the whole suite runs on PostgreSQL.
Do not set `RADAR_BLUESKY_TAGS` when running tests. Tests marked `clickhouse` need a live ClickHouse
(`CLICKHOUSE_TEST_URL`, `CLICKHOUSE_TEST_USER`, `CLICKHOUSE_TEST_PASSWORD`) and are skipped otherwise:
`uv run pytest -m clickhouse`. Tests need no broker.

## License

Apache 2.0, see `LICENSE`.
