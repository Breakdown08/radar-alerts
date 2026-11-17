from typing import Any, NoReturn

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import QuerySet
from rest_framework import serializers

from radar_alerts.alerts.models import Rule, Subscription, User
from radar_alerts.alerts.users import current_user
from radar_alerts.alerts.validation import check_rule, normalize_entity_id

TARGET_FIELDS: tuple[str, ...] = ("source", "entity_type", "entity_id")
CHANGE_MESSAGE: str = "The target of a subscription cannot be changed after creation."


def _django_errors(exc: DjangoValidationError) -> serializers.ValidationError:
    return serializers.ValidationError(exc.message_dict)


class MeSerializer(serializers.ModelSerializer[User]):
    class Meta:  # ty: ignore[incompatible-attribute-override]
        model = User
        fields = ("id", "username", "telegram_chat_id")
        read_only_fields = fields


class SubscriptionSerializer(serializers.ModelSerializer[Subscription]):
    class Meta:  # ty: ignore[incompatible-attribute-override]
        model = Subscription
        fields = (
            "id",
            "source",
            "entity_type",
            "entity_id",
            "label",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        current: Subscription | None = self.instance if isinstance(self.instance, Subscription) else None
        merged: dict[str, str] = {
            name: attrs[name] if name in attrs else getattr(current, name, "") for name in TARGET_FIELDS
        }
        errors: dict[str, list[str]] = {}
        if current is not None:
            name: str
            for name in ("source", "entity_type"):
                if merged[name] != getattr(current, name):
                    errors[name] = [CHANGE_MESSAGE]
            if errors:
                raise serializers.ValidationError(errors)
        try:
            merged["entity_id"] = normalize_entity_id(
                merged["source"], merged["entity_type"], merged["entity_id"]
            )
        except DjangoValidationError as exc:
            raise _django_errors(exc) from exc
        if current is not None:
            if merged["entity_id"] != current.entity_id:
                raise serializers.ValidationError({"entity_id": [CHANGE_MESSAGE]})
        elif Subscription.objects.filter(user=current_user(self.context["request"]), **merged).exists():
            raise serializers.ValidationError("You are already subscribed to this target.")
        attrs["entity_id"] = merged["entity_id"]
        return attrs


class OwnSubscriptionField(serializers.PrimaryKeyRelatedField[Subscription]):
    def get_queryset(self) -> QuerySet[Subscription]:
        return Subscription.objects.filter(user=current_user(self.context["request"]))

    def fail(self, key: str, **kwargs: object) -> NoReturn:
        # one message for "not yours" and "does not exist": do not reveal other users' ids
        if key in ("does_not_exist", "incorrect_type"):
            raise serializers.ValidationError("Unknown subscription.", code=key)
        super().fail(key, **kwargs)


class RuleSerializer(serializers.ModelSerializer[Rule]):
    subscription = OwnSubscriptionField()

    class Meta:  # ty: ignore[incompatible-attribute-override]
        model = Rule
        fields = (
            "id",
            "subscription",
            "kind",
            "threshold",
            "window_hours",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        current: Rule | None = self.instance if isinstance(self.instance, Rule) else None
        if current is not None and "subscription" in attrs and attrs["subscription"] != current.subscription:
            raise serializers.ValidationError(
                {"subscription": ["A rule cannot move to another subscription."]}
            )
        subscription: Subscription = attrs["subscription"] if current is None else current.subscription
        merged: dict[str, Any] = {
            name: attrs[name] if name in attrs else (getattr(current, name) if current else None)
            for name in ("kind", "threshold", "window_hours")
        }
        try:
            check_rule(subscription.source, merged["kind"], merged["threshold"], merged["window_hours"])
        except DjangoValidationError as exc:
            raise _django_errors(exc) from exc
        return attrs
