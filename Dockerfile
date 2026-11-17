FROM ghcr.io/astral-sh/uv:0.12.11 AS uv
FROM python:3.14.7-slim-trixie
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH DJANGO_SETTINGS_MODULE=radar_alerts.settings
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
COPY manage.py ./
RUN uv sync --locked --no-dev && python manage.py collectstatic --noinput
RUN useradd --system --uid 10001 --no-create-home app
USER app
EXPOSE 8000
CMD ["gunicorn", "radar_alerts.wsgi:application", "--bind", "0.0.0.0:8000", "--access-logfile", "-"]
