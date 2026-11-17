# radar-alerts

Subscriptions and alert rules for the gamedev-trends radar. A registered user subscribes to a Steam game
or a Bluesky tag and attaches rules to it ("players grew 30% in 24 hours", "the tag entered the top 10").
Django 6.1 + Django REST framework, PostgreSQL (or SQLite for development and tests).

A separately deployed service with its own PostgreSQL, a RabbitMQ broker, a Celery worker and a Celery beat
scheduler. Its only dependency on the radar is a versioned read-only SQL interface in the radar's ClickHouse
(database `alerts_v1`).

## What is not there yet

- **No TLS and no reverse proxy.** The service listens on `127.0.0.1:8000`. The Telegram webhook is checked by tests
  and locally with a forged update, not live through Telegram.
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

## Deploy

```
cp .env.example .env    # set POSTGRES_PASSWORD, RABBITMQ_PASSWORD, DJANGO_SECRET_KEY, TELEGRAM_BOT_TOKEN,
                        # CLICKHOUSE_PASSWORD (the radar_alerts password from the radar's .env)
docker compose up -d --build --wait
docker compose exec web python manage.py createsuperuser
docker compose exec worker python manage.py check_radar_contract
```

`docker compose up -d --build --wait` starts `db`, `rabbitmq`, `migrate`, `web`, `worker` and `beat` in their
own network. `migrate` is a one-shot container: it applies migrations and exits, and `web`, `worker` and `beat`
start only after it finished successfully, so no two processes migrate at once. `web` is gunicorn
(`WEB_CONCURRENCY` sets the number of its workers) with static files served by whitenoise.

- Only `worker` talks to the radar's ClickHouse. It joins the radar's external docker network, named by
  `RADAR_DOCKER_NETWORK` (default `gamedev-trends_default`), and reaches ClickHouse at `DOCKER_CLICKHOUSE_URL`
  (default `http://clickhouse:8123`). If the radar was never started, the network does not exist and
  `docker compose up` refuses: create it with `docker network create gamedev-trends_default`. The stack then
  runs without the radar and `check_rules` logs `RadarError`.
- Health checks: `db`, `rabbitmq`, `web` (`/healthz`, which checks only the database) and `worker`
  (`celery inspect ping`). `beat` has none: it listens on nothing, and a check by the age of its schedule file
  is fragile. `restart: unless-stopped` revives it; look for `Scheduler: Sending due task` in its log.
- `beat` must run as exactly one instance. `worker` can be scaled: `docker compose up -d --scale worker=2`.
  Each worker runs `WORKER_CONCURRENCY` processes (default 2): the load is a few queries every 5 minutes.
- `DJANGO_ALLOWED_HOSTS` must contain `localhost`, otherwise the `web` health check gets 400.
- `db` and `rabbitmq` are also published on `127.0.0.1`, so development on the host with `uv run` (see Run)
  still works.

## Radar interface

The service reads the radar only through the read-only SQL interface `alerts_v1` in the radar's ClickHouse,
three views with fixed columns:

| View | Columns |
|---|---|
| `steam_players` | `appid String`, `observed_at DateTime64(6, 'UTC')`, `players UInt32` |
| `steam_chart` | `appid String`, `chart_day DateTime64(6, 'UTC')`, `rank UInt16` |
| `bluesky_tag_hourly` | `tag LowCardinality(String)`, `hour DateTime('UTC')`, `posts UInt64` |

It reads as the user `radar_alerts`, which has only `SELECT ON alerts_v1.*`. The views and the user are created
by the radar, not by this service. The expected schema is in the code (`CONTRACT_COLUMNS`) and is compared with
the real one by `python manage.py check_radar_contract` (exit code 1 and one line per problem on a mismatch) and
by a log line at worker start (`radar contract v1 in alerts_v1: ok`, or one ERROR per problem). A mismatch
does not stop the worker. A new version of the interface is a new database `alerts_v2` next to the old one.

## Telegram webhook

By default the bot uses long polling (`TELEGRAM_MODE=polling`). Webhook mode needs a public HTTPS address
(Telegram accepts ports 443, 80, 88 and 8443). Telegram then posts updates to `POST /telegram/webhook/` with the
header `X-Telegram-Bot-Api-Secret-Token`, which must equal `TELEGRAM_WEBHOOK_SECRET` (1-256 characters of
`[A-Za-z0-9_-]`). Polling by `beat` is off in this mode. While a webhook is set, Telegram answers `getUpdates`
with 409.

- Switch to webhook: set `TELEGRAM_MODE=webhook`, `TELEGRAM_WEBHOOK_SECRET` and `TELEGRAM_WEBHOOK_URL`
  (`https://...`) in `.env`, recreate `web`, `worker` and `beat`, then
  `docker compose exec web python manage.py telegram_webhook set`.
- Switch back: `telegram_webhook delete`, set `TELEGRAM_MODE=polling`, recreate the containers.
- `telegram_webhook info` prints the URL, the pending update count and the last error.

An update is handled once: the offset cursor is shared by both modes, so a repeat from Telegram is dropped.

## Measured

One run on the author's machine (22 CPUs, Docker Compose 5.7.0) on 2026-11-17, next to a running radar
(ClickHouse 26.8.36.3), with one active rule.

| What | Value | How |
|---|---|---|
| Cold start after `docker compose down`, image cached | 21.5 s | `time docker compose up -d --build --wait` |
| Image build without cache (base images pulled) | 19.2 s | `time docker compose build --no-cache` |
| Start with a ready image, `db` and `rabbitmq` running | 10.5 s | `time docker compose up -d --wait` |
| Image size | 485 MB | `docker image ls radar-alerts:local` |
| `check_rules`, three queries to the views | 0.04 s | `succeeded in ...s` in `docker compose logs worker` |
| Firing created after the end of its hour | 10 min | `created_at - period`: 5 min data lag + the 5 min `check_rules` step |
| Firing queued to sent | 0.02 s | `sent_at - created_at` in the admin |
| RabbitMQ restart to worker reconnected | 4 s | `docker compose restart rabbitmq`, `Connected to amqp://` in the worker log |

Memory (`docker stats --no-stream`): `worker` 157 MiB with `WORKER_CONCURRENCY=2`, `rabbitmq` 103 MiB, `web`
76 MiB, `beat` 72 MiB, `db` 26 MiB. Before the limit the worker ran Celery's default of one prefork process per
CPU, 22 here, and took 1009 MiB.

Checked by hand in the same run: the `radar_alerts` user gets `ACCESS_DENIED` on `radar.observations` and on
`CREATE TABLE`; with a wrong `CLICKHOUSE_DATABASE` `check_radar_contract` exits with 1, the worker logs ERROR
and the bot still answers `/start`; no value of `TELEGRAM_BOT_TOKEN`, `CLICKHOUSE_PASSWORD`,
`POSTGRES_PASSWORD`, `RABBITMQ_PASSWORD` or `DJANGO_SECRET_KEY` from `.env` appears in `docker compose logs`;
the webhook answers 200 with a `sendMessage` to a forged `/start` with the right secret and 403 with a wrong one.

- A RabbitMQ restart in the middle of a task logs `CRITICAL Couldn't ack`. The message is delivered again and
  the tasks are idempotent, so nothing is sent twice.
- `restart: unless-stopped` does not revive a container after `docker compose kill`: Docker treats it as a
  manual stop. A worker whose main process exits is restarted and turns healthy again.

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
| `CLICKHOUSE_USER` | `radar_alerts` | read-only user of `alerts_v1` |
| `CLICKHOUSE_PASSWORD` | empty | |
| `CLICKHOUSE_DATABASE` | `alerts_v1` | the interface database; letters, digits and `_`; goes into SQL |
| `TELEGRAM_BOT_TOKEN` | empty | empty means no alerts are sent |
| `TELEGRAM_BOT_USERNAME` | empty | bot name without `@`, for `deep_link` |
| `TELEGRAM_MODE` | `polling` | `polling` or `webhook` |
| `TELEGRAM_WEBHOOK_SECRET` | empty | required for `webhook`: 1-256 characters of `[A-Za-z0-9_-]` |
| `TELEGRAM_WEBHOOK_URL` | empty | public `https://` base URL, used by `telegram_webhook set` |
| `TELEGRAM_API_URL` | `https://api.telegram.org` | Bot API base URL; changed only in CI, to a fake Telegram |
| `WEB_PORT` | `8000` | compose only: web port on `127.0.0.1` |
| `WORKER_CONCURRENCY` | `2` | compose only: Celery worker processes (Celery's own default is one per CPU) |
| `DOCKER_CLICKHOUSE_URL` | `http://clickhouse:8123` | compose only: `CLICKHOUSE_URL` inside `worker` |
| `RADAR_DOCKER_NETWORK` | `gamedev-trends_default` | compose only: the radar's external docker network |
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
