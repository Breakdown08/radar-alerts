from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from factories import error_keys, make_rule, make_sub, make_user

from radar_alerts.alerts.models import Rule, Subscription, User

pytestmark: list[pytest.MarkDecorator] = [pytest.mark.django_db, pytest.mark.usefixtures("default_tags")]


def test_subscription_full_clean_normalizes(user_a: User) -> None:
    sub: Subscription = Subscription(user=user_a, source="bluesky", entity_type="tag", entity_id="#GameDev")
    sub.full_clean()
    assert sub.entity_id == "gamedev"


def test_subscription_full_clean_rejects_unknown_tag(user_a: User) -> None:
    sub: Subscription = Subscription(user=user_a, source="bluesky", entity_type="tag", entity_id="unity")
    assert "entity_id" in error_keys(sub.full_clean)


def test_rule_full_clean_rejects_steam_top_n_with_window(user_a: User) -> None:
    rule: Rule = Rule(subscription=make_sub(user_a), kind="top_n", threshold=Decimal("10"), window_hours=24)
    assert "window_hours" in error_keys(rule.full_clean)


def test_rule_full_clean_accepts_valid_rule(user_a: User) -> None:
    rule: Rule = Rule(
        subscription=make_sub(user_a), kind="growth_pct", threshold=Decimal("30"), window_hours=24
    )
    rule.full_clean()


def test_rule_full_clean_is_a_validation_error(user_a: User) -> None:
    rule: Rule = Rule(subscription=make_sub(user_a), kind="top_n", threshold=Decimal("10"), window_hours=24)
    with pytest.raises(ValidationError):
        rule.full_clean()


def test_rule_threshold_constraint(user_a: User) -> None:
    sub: Subscription = make_sub(user_a)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_rule(sub, threshold=Decimal("0"))


def test_rule_window_constraint(user_a: User) -> None:
    sub: Subscription = make_sub(user_a)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_rule(sub, window_hours=200)


def test_subscription_source_entity_type_constraint(user_a: User) -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        make_sub(user_a, source="steam", entity_type="tag", entity_id="gamedev")


def test_subscription_unique_per_user(user_a: User, user_b: User) -> None:
    make_sub(user_a)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_sub(user_a)
    assert make_sub(user_b).pk is not None


def test_delete_subscription_deletes_rules(user_a: User) -> None:
    sub: Subscription = make_sub(user_a)
    make_rule(sub)
    sub.delete()
    assert Rule.objects.count() == 0


def test_delete_user_deletes_subscriptions(user_a: User) -> None:
    sub: Subscription = make_sub(user_a)
    make_rule(sub)
    user_a.delete()
    assert Subscription.objects.count() == 0
    assert Rule.objects.count() == 0


def test_str_subscription(user_a: User) -> None:
    assert str(make_sub(user_a)) == "steam:game:570"


def test_str_rule(user_a: User) -> None:
    sub: Subscription = make_sub(user_a)
    assert str(make_rule(sub, "growth_pct", Decimal("30"), 24)) == "growth_pct 30.00 / 24h"
    assert str(make_rule(sub, "top_n", Decimal("10"), None)) == "top_n 10.00"


def test_telegram_chat_id_roundtrip() -> None:
    group: User = make_user("g", telegram_chat_id=-1001234567890)
    none: User = make_user("n", telegram_chat_id=None)
    group.refresh_from_db()
    none.refresh_from_db()
    assert group.telegram_chat_id == -1001234567890
    assert none.telegram_chat_id is None
