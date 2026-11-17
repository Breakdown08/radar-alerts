"""Test doubles for the radar and Telegram, and builders for the notification tests."""

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from factories import make_rule, make_sub, make_user

from radar_alerts.alerts.models import Firing, Rule, Subscription, User
from radar_alerts.alerts.radar import RadarError, SteamChart
from radar_alerts.alerts.telegram import TelegramError

NOW: datetime = datetime(2026, 11, 17, 12, 10, tzinfo=UTC)
H: datetime = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


def at(hour: int, day: int = 17, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


class FakeRadar:
    """A RadarSource that records every call and can fail per method."""

    def __init__(
        self,
        players: dict[str, dict[datetime, int]] | None = None,
        chart: SteamChart | None = None,
        tags: dict[str, dict[datetime, int]] | None = None,
        failing: frozenset[str] = frozenset(),
    ) -> None:
        self.players: dict[str, dict[datetime, int]] = players or {}
        self.chart: SteamChart = chart or SteamChart(days=(), ranks={})
        self.tags: dict[str, dict[datetime, int]] = tags or {}
        self.failing: frozenset[str] = failing
        self.players_calls: list[tuple[list[str], datetime]] = []
        self.chart_calls: list[tuple[list[str], datetime]] = []
        self.tag_calls: list[tuple[list[str], datetime, datetime]] = []

    def steam_players(self, appids: Sequence[str], since: datetime) -> dict[str, dict[datetime, int]]:
        self.players_calls.append((list(appids), since))
        if "players" in self.failing:
            raise RadarError("players down")
        return self.players

    def steam_chart(self, appids: Sequence[str], since: datetime) -> SteamChart:
        self.chart_calls.append((list(appids), since))
        if "chart" in self.failing:
            raise RadarError("chart down")
        return self.chart

    def tag_counts(
        self, tags: Sequence[str], since: datetime, until: datetime
    ) -> dict[str, dict[datetime, int]]:
        self.tag_calls.append((list(tags), since, until))
        if "tags" in self.failing:
            raise RadarError("tags down")
        return self.tags

    @property
    def total_calls(self) -> int:
        return len(self.players_calls) + len(self.chart_calls) + len(self.tag_calls)


class FakeTelegram:
    """A Telegram that records sends, raises queued errors before succeeding, serves queued update batches."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.send_calls: int = 0
        self.errors: list[TelegramError] = []
        self.always: TelegramError | None = None
        self.message_id: int = 777
        self.batches: list[list[dict[str, object]]] = []
        self.updates_calls: list[tuple[int, int]] = []
        self.updates_error: TelegramError | None = None

    def send_message(self, chat_id: int, text: str) -> int:
        self.send_calls += 1
        if self.always is not None:
            raise self.always
        if self.errors:
            raise self.errors.pop(0)
        self.sent.append((chat_id, text))
        return self.message_id

    def get_updates(self, offset: int, timeout: int) -> list[dict[str, object]]:
        self.updates_calls.append((offset, timeout))
        if self.updates_error is not None:
            raise self.updates_error
        return self.batches.pop(0) if self.batches else []


def temporary(retry_after: float | None = None) -> TelegramError:
    return TelegramError("temporary failure", retryable=True, retry_after=retry_after)


def permanent() -> TelegramError:
    return TelegramError("Telegram answered 403: Forbidden", retryable=False)


def steam_growth_series() -> dict[str, dict[datetime, int]]:
    """Fires steam growth 30% / 24h for appid 570 at H = 12:00 on 17 Nov."""
    return {"570": {at(12, day=16): 100, at(12): 200}}


def tag_growth_counts() -> dict[str, dict[datetime, int]]:
    """Fires bluesky growth 50% / 2h for godot at H = 12:00 on 17 Nov (Hit current=15, base=7)."""
    return {"godot": {at(8): 5, at(9): 2, at(10): 4, at(11): 11}}


class World:
    """One user with a linked chat, a subscription, a rule and (optionally) a firing."""

    def __init__(self, chat_id: int | None = 111, username: str = "alice") -> None:
        self.user: User = make_user(username, telegram_chat_id=chat_id)
        self.subscription: Subscription = make_sub(self.user, label="Dota 2")
        self.rule: Rule = make_rule(self.subscription)

    def firing(self, period: datetime = H, status: str = "pending", text: str = "msg") -> Firing:
        return Firing.objects.create(rule=self.rule, period=period, text=text, status=status)


def steam_rule(user: User, appid: str, kind: str = "growth_pct", window: int | None = 24) -> Rule:
    sub: Subscription = make_sub(user, entity_id=appid)
    threshold: Decimal = Decimal("30") if kind == "growth_pct" else Decimal("10")
    return make_rule(sub, kind=kind, threshold=threshold, window_hours=window)


def tag_rule(user: User, tag: str, kind: str, threshold: str, window: int) -> Rule:
    sub: Subscription = make_sub(user, source="bluesky", entity_type="tag", entity_id=tag)
    return make_rule(sub, kind=kind, threshold=Decimal(threshold), window_hours=window)
