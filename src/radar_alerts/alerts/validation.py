import re
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError

MAX_STEAM_APPID: int = 4_294_967_295
TAG_PATTERN: str = r"^[a-z][a-z0-9_]{0,27}$"
MAX_WINDOW_HOURS: int = 168
MAX_GROWTH_PCT: Decimal = Decimal("10000")
MAX_TOP_N: int = 100


def normalize_entity_id(source: str, entity_type: str, raw: str) -> str:
    """Return the canonical entity_id for a target, or raise ValidationError keyed by field."""
    if (source, entity_type) == ("steam", "game"):
        value: str = raw.strip()
        if not (value.isascii() and value.isdigit()) or not 1 <= int(value) <= MAX_STEAM_APPID:
            raise ValidationError(
                {"entity_id": f"A Steam appid is a whole number from 1 to {MAX_STEAM_APPID}."}
            )
        return str(int(value))
    if (source, entity_type) == ("bluesky", "tag"):
        tag: str = raw.strip()
        tag = tag.removeprefix("#").lower()
        if not re.match(TAG_PATTERN, tag):
            raise ValidationError(
                {"entity_id": "A tag: lowercase letters, digits, underscores; starts with a letter; max 28."}
            )
        if tag not in settings.RADAR_BLUESKY_TAGS:
            raise ValidationError({"entity_id": f"The radar does not collect the tag {tag!r}."})
        return tag
    raise ValidationError({"entity_type": "Supported targets are steam/game and bluesky/tag."})


def _window_error(window_hours: int | None) -> str | None:
    if window_hours is None or not 1 <= window_hours <= MAX_WINDOW_HOURS:
        return f"window_hours is required and must be from 1 to {MAX_WINDOW_HOURS}."
    return None


def check_rule(source: str, kind: str, threshold: Decimal, window_hours: int | None) -> None:
    """Validate a rule against its subscription's source; raise ValidationError keyed by field."""
    errors: dict[str, list[str]] = {}
    if kind == "growth_pct":
        if not 0 < threshold <= MAX_GROWTH_PCT:
            errors["threshold"] = [f"Growth threshold must be above 0 and at most {MAX_GROWTH_PCT}."]
        window_msg: str | None = _window_error(window_hours)
        if window_msg:
            errors["window_hours"] = [window_msg]
    elif kind == "top_n":
        upper: int = MAX_TOP_N if source == "steam" else len(settings.RADAR_BLUESKY_TAGS) - 1
        if threshold != threshold.to_integral_value() or not 1 <= threshold <= upper:
            errors["threshold"] = [f"top_n threshold must be a whole number from 1 to {upper}."]
        if source == "steam":
            if window_hours is not None:
                errors["window_hours"] = ["Steam top_n uses the daily chart: window_hours must be empty."]
        else:
            window_msg = _window_error(window_hours)
            if window_msg:
                errors["window_hours"] = [window_msg]
    else:
        errors["kind"] = [f"Unknown rule kind {kind!r}."]
    if errors:
        raise ValidationError(errors)
