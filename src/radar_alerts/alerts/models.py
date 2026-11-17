from datetime import datetime
from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q

from radar_alerts.alerts.validation import check_rule, normalize_entity_id


class Source(models.TextChoices):
    STEAM = "steam"
    BLUESKY = "bluesky"


class EntityType(models.TextChoices):
    GAME = "game"
    TAG = "tag"


class RuleKind(models.TextChoices):
    GROWTH_PCT = "growth_pct"
    TOP_N = "top_n"


class User(AbstractUser):
    telegram_chat_id: models.BigIntegerField[int | None, int | None] = models.BigIntegerField(
        null=True, blank=True
    )


class Subscription(models.Model):
    user: models.ForeignKey[User, User] = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="subscriptions"
    )
    source: models.CharField[str, str] = models.CharField(max_length=16, choices=Source.choices)
    entity_type: models.CharField[str, str] = models.CharField(max_length=16, choices=EntityType.choices)
    entity_id: models.CharField[str, str] = models.CharField(max_length=64)
    label: models.CharField[str, str] = models.CharField(max_length=100, blank=True, default="")
    is_active: models.BooleanField[bool, bool] = models.BooleanField(default=True)
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(auto_now_add=True)
    updated_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "source", "entity_type", "entity_id"], name="subscription_unique_target"
            ),
            models.CheckConstraint(
                condition=Q(source="steam", entity_type="game") | Q(source="bluesky", entity_type="tag"),
                name="subscription_source_entity_type",
            ),
        ]
        indexes = [
            models.Index(fields=["source", "entity_type", "entity_id"], name="subscription_target_idx")
        ]

    def clean(self) -> None:
        self.entity_id: str = normalize_entity_id(self.source, self.entity_type, self.entity_id)

    def __str__(self) -> str:
        return f"{self.source}:{self.entity_type}:{self.entity_id}"


class Rule(models.Model):
    subscription: models.ForeignKey[Subscription, Subscription] = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="rules"
    )
    kind: models.CharField[str, str] = models.CharField(max_length=16, choices=RuleKind.choices)
    threshold: models.DecimalField[Decimal, Decimal] = models.DecimalField(max_digits=10, decimal_places=2)
    window_hours: models.PositiveSmallIntegerField[int | None, int | None] = models.PositiveSmallIntegerField(
        null=True, blank=True
    )
    is_active: models.BooleanField[bool, bool] = models.BooleanField(default=True)
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(auto_now_add=True)
    updated_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.CheckConstraint(condition=Q(threshold__gt=0), name="rule_threshold_positive"),
            models.CheckConstraint(
                condition=Q(window_hours__isnull=True) | Q(window_hours__gte=1, window_hours__lte=168),
                name="rule_window_hours_range",
            ),
        ]

    def clean(self) -> None:
        if self.subscription_id is None:
            return  # the form reports the missing subscription itself
        check_rule(self.subscription.source, self.kind, self.threshold, self.window_hours)

    def __str__(self) -> str:
        window: str = f" / {self.window_hours}h" if self.window_hours is not None else ""
        return f"{self.kind} {self.threshold:.2f}{window}"
