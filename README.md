# radar-alerts

Subscriptions and alert rules for the gamedev-trends radar. A registered user subscribes to a Steam game
or a Bluesky tag and attaches rules to it ("players grew 30% in 24 hours", "the tag entered the top 10").
Django 6.1 + Django REST framework, PostgreSQL (or SQLite for development and tests).

The service will only ever read the radar's ClickHouse, and even that arrives in the next stage.

## What is not there yet

- **No alerts at all.** This stage is the skeleton: models, admin, API, migrations, CI. Rules are stored
  and validated, but nothing evaluates them and nothing is sent to Telegram.
- **Telegram is not linked.** `telegram_chat_id` is set by an administrator in `/admin/`; the API only reads it.
- **No open registration.** Users are created by an administrator. This is a decision, not a gap.
- The radar collects only the Steam top-100 chart, so a subscription to a game outside it will get no data.
- The tag whitelist `RADAR_BLUESKY_TAGS` must be kept in sync with the radar's `BLUESKY_TAGS` by hand.
  A `top_n` threshold for a tag is below the number of tags in the list (with 7 tags, "top 7" is always true).

## Run

```
cp .env.example .env    # set POSTGRES_PASSWORD, POSTGRES_HOST=localhost, DJANGO_SECRET_KEY
docker compose up -d db
uv run --env-file .env python manage.py migrate
uv run --env-file .env python manage.py createsuperuser
uv run --env-file .env python manage.py runserver
```

Without `POSTGRES_HOST` the app uses SQLite (`db.sqlite3`), no Docker needed.

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
| `RADAR_BLUESKY_TAGS` | `gamedev,indiedev,indiegame,godot,pixelart,screenshotsaturday,mobilegame` | tags the radar collects |

## Development

```
uv sync
uv run ruff check && uv run ruff format --check && uv run ty check && uv run mypy && uv run pytest
```

Tests run on SQLite by default; with `POSTGRES_*` set the whole suite runs on PostgreSQL.
Do not set `RADAR_BLUESKY_TAGS` when running tests.

## License

Apache 2.0, see `LICENSE`.
