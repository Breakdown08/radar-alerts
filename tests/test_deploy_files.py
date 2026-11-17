"""Checks 29 to 39: static tests of the Dockerfile, compose.yaml, .env.example, CI, pyproject, README."""

import json
import re
import tomllib
from pathlib import Path

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
    assert set(services()) == {"db", "rabbitmq", "migrate", "web", "worker", "beat"}
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
        "WEB_PORT=8000",
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
    assert {"gunicorn", "whitenoise"} <= set(names)
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
    assert not any("measure" in h.lower() or "benchmark" in h.lower() for h in headings)
