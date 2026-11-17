from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from factories import DEFAULT_TAGS, error_keys
from pytest_django.fixtures import Settings

from radar_alerts.alerts.validation import check_rule, normalize_entity_id

pytestmark: pytest.MarkDecorator = pytest.mark.usefixtures("default_tags")


def d(value: str) -> Decimal:
    return Decimal(value)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("570", "570"), (" 570 ", "570"), ("0570", "570"), ("4294967295", "4294967295"), ("1", "1")],
)
def test_steam_appid_normalized(raw: str, expected: str) -> None:
    assert normalize_entity_id("steam", "game", raw) == expected


@pytest.mark.parametrize("raw", ["0", "-5", "abc", "", "4294967296", "57 0"])
def test_steam_appid_rejected(raw: str) -> None:
    assert "entity_id" in error_keys(lambda: normalize_entity_id("steam", "game", raw))


@pytest.mark.parametrize("raw", ["gamedev", "#GameDev", " GAMEDEV "])
def test_bluesky_tag_normalized(raw: str) -> None:
    assert normalize_entity_id("bluesky", "tag", raw) == "gamedev"


@pytest.mark.parametrize("raw", ["##gamedev", "game-dev", "", "#", "a" * 29])
def test_bluesky_tag_rejected(raw: str) -> None:
    assert "entity_id" in error_keys(lambda: normalize_entity_id("bluesky", "tag", raw))


def test_bluesky_tag_must_be_on_whitelist(settings: Settings) -> None:
    settings.RADAR_BLUESKY_TAGS = ("godot",)
    assert normalize_entity_id("bluesky", "tag", "godot") == "godot"
    assert "entity_id" in error_keys(lambda: normalize_entity_id("bluesky", "tag", "gamedev"))


def test_unknown_tag_rejected_by_default() -> None:
    assert "entity_id" in error_keys(lambda: normalize_entity_id("bluesky", "tag", "unity"))


@pytest.mark.parametrize(
    ("source", "entity_type", "raw"), [("steam", "tag", "570"), ("bluesky", "game", "gamedev")]
)
def test_wrong_source_entity_type_pair(source: str, entity_type: str, raw: str) -> None:
    assert "entity_type" in error_keys(lambda: normalize_entity_id(source, entity_type, raw))


def test_growth_pct_valid() -> None:
    check_rule("steam", "growth_pct", d("30"), 24)
    check_rule("bluesky", "growth_pct", d("0.01"), 1)
    check_rule("steam", "growth_pct", d("10000"), 168)


@pytest.mark.parametrize("threshold", ["0", "-1", "10000.01"])
def test_growth_pct_bad_threshold(threshold: str) -> None:
    assert "threshold" in error_keys(lambda: check_rule("steam", "growth_pct", d(threshold), 24))


@pytest.mark.parametrize("window", [None, 0, 169])
def test_growth_pct_bad_window(window: int | None) -> None:
    assert "window_hours" in error_keys(lambda: check_rule("steam", "growth_pct", d("30"), window))


def test_steam_top_n_valid() -> None:
    check_rule("steam", "top_n", d("10"), None)
    check_rule("steam", "top_n", d("100"), None)
    check_rule("steam", "top_n", d("1"), None)


def test_steam_top_n_window_forbidden() -> None:
    assert "window_hours" in error_keys(lambda: check_rule("steam", "top_n", d("10"), 24))


@pytest.mark.parametrize("threshold", ["10.5", "0", "101"])
def test_steam_top_n_bad_threshold(threshold: str) -> None:
    assert "threshold" in error_keys(lambda: check_rule("steam", "top_n", d(threshold), None))


def test_bluesky_top_n_valid_and_window_required() -> None:
    check_rule("bluesky", "top_n", d("3"), 24)
    assert "window_hours" in error_keys(lambda: check_rule("bluesky", "top_n", d("3"), None))


def test_bluesky_top_n_threshold_below_tag_count() -> None:
    assert len(DEFAULT_TAGS) == 7
    check_rule("bluesky", "top_n", d("6"), 24)
    assert "threshold" in error_keys(lambda: check_rule("bluesky", "top_n", d("7"), 24))
    assert "threshold" in error_keys(lambda: check_rule("bluesky", "top_n", d("100"), 24))


def test_bluesky_top_n_follows_whitelist_size(settings: Settings) -> None:
    settings.RADAR_BLUESKY_TAGS = ("a", "b", "c")
    check_rule("bluesky", "top_n", d("2"), 24)
    assert "threshold" in error_keys(lambda: check_rule("bluesky", "top_n", d("3"), 24))


def test_both_errors_reported_together() -> None:
    info: pytest.ExceptionInfo[ValidationError]
    with pytest.raises(ValidationError) as info:
        check_rule("steam", "growth_pct", d("0"), None)
    assert {"threshold", "window_hours"} <= set(info.value.error_dict)


def test_unknown_kind() -> None:
    assert "kind" in error_keys(lambda: check_rule("steam", "above", d("1"), 1))
