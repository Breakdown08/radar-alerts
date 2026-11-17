from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.http import HttpRequest

from radar_alerts.alerts.models import Firing, Rule, Subscription, User


@admin.register(User)
class RadarUserAdmin(UserAdmin[User]):
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "email")}),
        ("Telegram", {"fields": ("telegram_chat_id",)}),
        (
            "Permissions",
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    list_display = ("username", "email", "first_name", "last_name", "is_staff", "telegram_chat_id")


class RuleInline(admin.TabularInline[Rule, Subscription]):
    model = Rule
    extra = 0


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin[Subscription]):
    list_display = ("id", "user", "source", "entity_type", "entity_id", "label", "is_active", "created_at")
    list_filter = ("source", "entity_type", "is_active")
    search_fields = ("entity_id", "label", "user__username")
    raw_id_fields = ("user",)
    inlines = (RuleInline,)


@admin.register(Rule)
class RuleAdmin(admin.ModelAdmin[Rule]):
    list_display = ("id", "subscription", "kind", "threshold", "window_hours", "is_active")
    list_filter = ("kind", "is_active")


@admin.register(Firing)
class FiringAdmin(admin.ModelAdmin[Firing]):
    list_display = ("id", "rule", "period", "status", "attempts", "sent_at", "created_at")
    list_filter = ("status",)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Firing | None = None) -> bool:
        return False
