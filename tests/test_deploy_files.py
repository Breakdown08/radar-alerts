"""Checks 29 to 39: static tests of the Dockerfile, compose.yaml, .env.example, CI, pyproject, README."""

import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT: Path = Path(__file__).resolve().parents[1]
APP_SERVICES: tuple[str, ...] = ("migrate", "web", "worker", "beat")


def read(name: str) -> str:
    return (ROOT / name).read_text()


def load_yaml(name: str) -> dict[str, object]:
    data: object = yaml.safe_load(read(name))
    assert isinstance(data, dict)
    return data


def compose() -> dict[str, object]:
    return load_yaml("compose.yaml")


def services() -> dict[str, dict[str, object]]:
    raw: object = compose()["services"]
    assert isinstance(raw, dict)
    return raw


def listed(value: object) -> list[str]:
    """Names of a compose list or mapping (networks, env_file, depends_on)."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [str(k) for k in value]
    assert isinstance(value, list)
    return [str(v) for v in value]


def command_text(service: dict[str, object]) -> str:
    command: object = service.get("command", "")
    return " ".join(command) if isinstance(command, list) else str(command)


def instructions() -> list[tuple[str, str]]:
    """Dockerfile instructions (line continuations joined), as (KEYWORD, arguments)."""
    out: list[tuple[str, str]] = []
    line: str
    for line in read("Dockerfile").replace("\\\n", " ").splitlines():
        stripped: str = line.strip()
        if stripped and not stripped.startswith("#"):
            parts: tuple[str, str, str] = stripped.partition(" ")
            out.append((parts[0].upper(), parts[2].strip()))
    return out


def test_dockerfile_properties() -> None:
    """Check 29."""
    steps: list[tuple[str, str]] = instructions()
    assert any(k == "FROM" and v.startswith("python:3.14") for k, v in steps)
    assert any(k == "RUN" and "uv sync --locked --no-dev" in v for k, v in steps)
    assert any(k == "RUN" and "collectstatic --noinput" in v for k, v in steps)
    last_user: int = max(i for i, (k, _) in enumerate(steps) if k == "USER")
    last_run: int = max(i for i, (k, _) in enumerate(steps) if k == "RUN")
    assert last_user > last_run
    assert steps[last_user][1].split(":")[0] not in {"root", "0"}


def test_dockerfile_copies_no_env_and_no_whole_context() -> None:
    """Check 29."""
    keyword: str
    rest: str
    for keyword, rest in instructions():
        if keyword != "COPY":
            continue
        args: list[str] = [a for a in rest.split() if not a.startswith("--")]
        sources: list[str] = args[:-1]
        assert ".env" not in sources
        assert "." not in sources
        assert "./" not in sources


def test_dockerfile_cmd_runs_gunicorn() -> None:
    """Check 29."""
    cmds: list[str] = [v for k, v in instructions() if k == "CMD"]
    assert cmds
    text: str = cmds[-1]
    assert "gunicorn" in text
    assert "radar_alerts.wsgi:application" in text
    assert "0.0.0.0:8000" in text


def test_ignore_files() -> None:
    """Check 30."""
    ignored: list[str] = [line.strip() for line in read(".dockerignore").splitlines()]
    assert {".env", ".git", ".venv", "db.sqlite3"} <= set(ignored)
    assert "staticfiles/" in [line.strip() for line in read(".gitignore").splitlines()]


def test_compose_services() -> None:
    """Check 31."""
    assert set(services()) == {"db", "rabbitmq", "migrate", "web", "worker", "beat", "nginx"}
    assert "include" not in compose()
    name: str
    for name in APP_SERVICES:
        assert services()[name]["image"] == "radar-alerts:local"
        assert services()[name].get("build") is not None


def test_compose_radar_network() -> None:
    """Check 32."""
    networks: object = compose()["networks"]
    assert isinstance(networks, dict)
    assert networks["radar"] == {"external": True, "name": "${RADAR_DOCKER_NETWORK:-gamedev-trends_default}"}
    name: str
    service: dict[str, object]
    for name, service in services().items():
        if name == "worker":
            assert set(listed(service.get("networks"))) == {"default", "radar"}
        else:
            assert "radar" not in listed(service.get("networks"))


def depends(name: str) -> dict[str, str]:
    raw: object = services()[name].get("depends_on")
    assert isinstance(raw, dict)
    return {str(k): str(v["condition"]) for k, v in raw.items()}


def test_compose_healthchecks_and_dependencies() -> None:
    """Check 33."""

    def test_of(name: str) -> str:
        check: object = services()[name].get("healthcheck")
        assert isinstance(check, dict)
        return " ".join(check["test"]) if isinstance(check["test"], list) else str(check["test"])

    assert test_of("db")
    assert test_of("rabbitmq")
    assert "/healthz" in test_of("web")
    assert "inspect ping" in test_of("worker")
    name: str
    for name in ("web", "worker", "beat"):
        assert depends(name)["migrate"] == "service_completed_successfully"
    for name in ("worker", "beat"):
        assert depends(name)["rabbitmq"] == "service_healthy"
    assert depends("migrate")["db"] == "service_healthy"


def test_compose_environment() -> None:
    """Check 34."""
    name: str
    for name in APP_SERVICES:
        service: dict[str, object] = services()[name]
        env: object = service["environment"]
        assert isinstance(env, dict)
        assert env["POSTGRES_HOST"] == "db"
        assert env["RABBITMQ_HOST"] == "rabbitmq"
        assert env["DJANGO_DEBUG"] == ""
        assert listed(service["env_file"]) == [".env"]
    worker_env: object = services()["worker"]["environment"]
    assert isinstance(worker_env, dict)
    assert worker_env["CLICKHOUSE_URL"] == "${DOCKER_CLICKHOUSE_URL:-http://clickhouse:8123}"


def test_compose_ports_and_commands() -> None:
    """Check 35."""
    service: dict[str, object]
    for service in services().values():
        port: str
        for port in listed(service.get("ports")):
            assert port.startswith("127.0.0.1:")
        assert "debug" not in command_text(service).lower()
    assert "-l info" in command_text(services()["worker"])
    assert "-l info" in command_text(services()["beat"])
    assert "--schedule /tmp/celerybeat-schedule" in command_text(services()["beat"])


def test_env_example() -> None:
    """Check 36."""
    lines: list[str] = read(".env.example").splitlines()
    expected: list[str] = [
        "CLICKHOUSE_USER=radar_alerts",
        "CLICKHOUSE_DATABASE=alerts_v1",
        "TELEGRAM_MODE=polling",
        "TELEGRAM_API_URL=https://api.telegram.org",
        "TELEGRAM_WEBHOOK_SECRET=",
        "TELEGRAM_WEBHOOK_URL=",
        "HTTP_PORT=8080",
        "SENTRY_DSN=",
        "SENTRY_ENVIRONMENT=production",
        "SENTRY_RELEASE=",
        "SENTRY_TRACES_SAMPLE_RATE=0",
        "DOCKER_CLICKHOUSE_URL=http://clickhouse:8123",
        "RADAR_DOCKER_NETWORK=gamedev-trends_default",
        "POSTGRES_PASSWORD=",
        "RABBITMQ_PASSWORD=",
        "CLICKHOUSE_PASSWORD=",
        "TELEGRAM_BOT_TOKEN=",
    ]
    line: str
    for line in expected:
        assert line in lines
    assert not any(line.startswith("WEB_PORT=") for line in lines)


def test_ci_jobs_and_compose_steps() -> None:
    """Check 37."""
    ci: dict[str, object] = load_yaml(".github/workflows/ci.yml")
    jobs: object = ci["jobs"]
    assert isinstance(jobs, dict)
    assert set(jobs) == {"check", "postgres", "clickhouse", "compose"}
    job: object = jobs["compose"]
    assert isinstance(job, dict)
    text: str = json.dumps(job)
    snippets: list[str] = [
        "docker network create",
        "docker compose up",
        "--wait",
        "curl",
        "/healthz",
        "/static/admin/",
        "127.0.0.1:8080/healthz",
        "127.0.0.1:8080/static/admin/",
        "nginx -t",
        "x-content-type-options",
        "id -u",
        "grep -F",
        "ci-db-password",
        "ci-mq-password",
        "ci-secret-key",
        "ci-fake-token",
        "docker compose down -v",
        "TELEGRAM_API_URL=http://telegram-stub:8081",
        "docker run",
        "tests/telegram_stub.py",
        "docker logs telegram-stub",
        "/getUpdates",
    ]
    snippet: str
    for snippet in snippets:
        assert snippet in text, snippet
    assert "api.telegram.org" not in text
    assert "127.0.0.1:8000/static" not in text
    assert text.count("127.0.0.1:8000") == 1
    steps: object = job["steps"]
    assert isinstance(steps, list)
    teardown: list[object] = [s for s in steps if "docker compose down -v" in json.dumps(s)]
    assert teardown
    assert all(isinstance(s, dict) and s.get("if") == "always()" for s in teardown)


def test_pyproject_dependencies() -> None:
    """Check 38."""
    data: dict[str, object] = tomllib.loads(read("pyproject.toml"))
    project: object = data["project"]
    assert isinstance(project, dict)
    deps: list[str] = project["dependencies"]
    names: list[str] = [re.split(r"[<>=\[ ;~!]", d)[0].lower() for d in deps]
    assert {"gunicorn", "whitenoise", "sentry-sdk"} <= set(names)
    sentry: str = next(d for d in deps if d.lower().startswith("sentry-sdk"))
    match: re.Match[str] | None = re.search(r"\[([^\]]*)\]", sentry)
    assert match is not None
    assert {e.strip() for e in match.group(1).split(",")} == {"django", "celery"}
    groups: object = data["dependency-groups"]
    assert isinstance(groups, dict)
    dev: list[str] = [re.split(r"[<>=\[ ;~!]", d)[0].lower() for d in groups["dev"] if isinstance(d, str)]
    assert {"pyyaml", "types-pyyaml"} <= set(dev)
    tool: object = data["tool"]
    assert isinstance(tool, dict)
    warnings: list[str] = tool["pytest"]["ini_options"]["filterwarnings"]
    assert any("No directory at" in w for w in warnings)


def test_readme() -> None:
    """Check 39."""
    text: str = read("README.md")
    headings: list[str] = re.findall(r"^#+\s+(.*)$", text, flags=re.MULTILINE)
    wanted: str
    for wanted in ("Deploy", "Radar interface", "Telegram webhook"):
        assert any(wanted.lower() in h.lower() for h in headings), wanted
    phrase: str
    for phrase in (
        "docker compose up -d --build --wait",
        "check_radar_contract",
        "alerts_v1",
        "telegram_webhook",
    ):
        assert phrase in text, phrase
    assert "kadath" not in text.lower()
    for phrase in ("nginx", "deploy/nginx.conf", "HTTP_PORT", "SENTRY_DSN", "SENTRY_TRACES_SAMPLE_RATE"):
        assert phrase in text, phrase
    assert any("sentry" in h.lower() for h in headings)
    assert "No TLS and no reverse proxy" not in text
    assert "WEB_PORT" not in text
    assert "listen 443" in text or "TLS would" in text


NGINX_IMAGE_RE: str = r"^nginx:\d+\.\d+\.\d+-alpine$"
EXPECTED_DIRECTIVES: tuple[str, ...] = (
    "listen 80",
    "server_tokens off",
    "client_max_body_size 1m",
    "gzip on",
    "gzip_proxied any",
    "proxy_pass http://web:8000",
    "proxy_set_header Host $http_host",
    "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for",
    "proxy_set_header X-Forwarded-Proto $scheme",
    "proxy_hide_header X-Content-Type-Options",
    "proxy_hide_header X-Frame-Options",
    "proxy_hide_header Referrer-Policy",
    'add_header X-Content-Type-Options "nosniff" always',
    'add_header X-Frame-Options "DENY" always',
    'add_header Referrer-Policy "same-origin" always',
)


def nginx_text() -> str:
    """deploy/nginx.conf without comments."""
    return re.sub(r"#[^\n]*", "", read("deploy/nginx.conf"))


def nginx_directives() -> set[str]:
    parts: list[str] = re.split(r"[;{}]", nginx_text())
    return {" ".join(p.split()) for p in parts if p.strip()}


def test_nginx_service() -> None:
    """Check 18."""
    nginx: dict[str, object] = services()["nginx"]
    image: object = nginx["image"]
    assert isinstance(image, str)
    assert re.match(NGINX_IMAGE_RE, image)
    key: str
    for key in ("build", "env_file", "environment"):
        assert key not in nginx
    assert nginx["ports"] == ["127.0.0.1:${HTTP_PORT:-8080}:80"]
    assert nginx["volumes"] == ["./deploy/nginx.conf:/etc/nginx/conf.d/default.conf:ro"]


def test_nginx_depends_on_web_with_restart() -> None:
    """Check 19."""
    depends_on: object = services()["nginx"]["depends_on"]
    assert isinstance(depends_on, dict)
    assert depends_on["web"] == {"condition": "service_healthy", "restart": True}


def test_nginx_healthcheck() -> None:
    """Check 20."""
    check: object = services()["nginx"].get("healthcheck")
    assert isinstance(check, dict)
    test: object = check["test"]
    assert isinstance(test, list)
    assert any("/healthz" in str(item) for item in test)
    assert "Host: localhost" in test


def test_only_nginx_publishes_http_ports() -> None:
    """Check 21."""
    assert "ports" not in services()["web"]
    name: str
    service: dict[str, object]
    for name, service in services().items():
        if name == "nginx":
            continue
        port: str
        for port in listed(service.get("ports")):
            assert not port.endswith(":8000"), (name, port)
            assert not port.endswith(":80"), (name, port)


def test_nginx_not_in_radar_network() -> None:
    """Check 22: the service exists and stays out of radar."""
    assert "nginx" in services()
    assert "radar" not in listed(services()["nginx"].get("networks"))


def test_nginx_conf_directives() -> None:
    """Check 23."""
    found: set[str] = nginx_directives()
    wanted: str
    for wanted in EXPECTED_DIRECTIVES:
        assert wanted in found, wanted
    assert any(d.startswith("gzip_types") and "application/json" in d for d in found)
    prefix: str
    for prefix in (
        "proxy_connect_timeout",
        "proxy_read_timeout",
        "proxy_send_timeout",
        "client_body_timeout",
    ):
        assert any(d.startswith(prefix) for d in found), prefix


def test_nginx_conf_exclusions() -> None:
    """Check 24."""
    text: str = nginx_text()
    forbidden: str
    for forbidden in ("listen 443", " ssl", "upstream", "proxy_set_header Host $host"):
        assert forbidden not in text, forbidden
    match: re.Match[str] | None = re.search(r"location\s+/\s*\{([^}]*)\}", text)
    assert match is not None
    assert "add_header" not in match.group(1)


def test_nginx_conf_passes_nginx_t() -> None:
    """Check 25: optional, never pulls an image."""
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    image: object = services()["nginx"]["image"]
    assert isinstance(image, str)
    inspect: subprocess.CompletedProcess[bytes] = subprocess.run(
        ["docker", "image", "inspect", image], capture_output=True, timeout=60
    )
    if inspect.returncode != 0:
        pytest.skip(f"{image} is not pulled")
    run: subprocess.CompletedProcess[bytes] = subprocess.run(
        [
            "docker", "run", "--rm", "--add-host", "web:127.0.0.1",
            "-v", f"{ROOT}/deploy/nginx.conf:/etc/nginx/conf.d/default.conf:ro",
            image, "nginx", "-t",
        ],
        capture_output=True,
        timeout=60,
    )  # fmt: skip
    assert run.returncode == 0, run.stderr.decode()


def test_uv_lock_has_sentry() -> None:
    """Check 28."""
    assert 'name = "sentry-sdk"' in read("uv.lock")
