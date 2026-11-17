from collections.abc import Callable
from typing import Any

from django.db.models import QuerySet
from rest_framework import serializers, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from radar_alerts.alerts.models import EntityType, Rule, RuleKind, Source, Subscription
from radar_alerts.alerts.serializers import MeSerializer, RuleSerializer, SubscriptionSerializer
from radar_alerts.alerts.users import current_user


def _parse_bool(raw: str) -> bool:
    if raw in ("true", "false"):
        return raw == "true"
    raise ValueError("Must be true or false.")


def _parse_int(raw: str) -> int:
    if raw.isascii() and raw.isdigit():
        return int(raw)
    raise ValueError("Must be a whole number.")


def _parse_choice(choices: type[Source] | type[EntityType] | type[RuleKind]) -> Callable[[str], str]:
    def parse(raw: str) -> str:
        if raw in choices.values:
            return raw
        raise ValueError(f"Must be one of: {', '.join(choices.values)}.")

    return parse


def apply_filters[M: Any](
    request: Request, queryset: QuerySet[M], parsers: dict[str, Callable[[str], object]]
) -> QuerySet[M]:
    """Exact-match filters from query parameters; every bad value is reported under its parameter name."""
    errors: dict[str, list[str]] = {}
    lookups: dict[str, object] = {}
    name: str
    parse: Callable[[str], object]
    for name, parse in parsers.items():
        raw: str | None = request.query_params.get(name)
        if raw is None:
            continue
        try:
            lookups[name] = parse(raw)
        except ValueError as exc:
            errors[name] = [str(exc)]
    if errors:
        raise serializers.ValidationError(errors)
    return queryset.filter(**lookups)


class MeView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request: Request) -> Response:
        return Response(MeSerializer(current_user(request)).data)


class SubscriptionViewSet(viewsets.ModelViewSet[Subscription]):
    serializer_class = SubscriptionSerializer

    def get_queryset(self) -> QuerySet[Subscription]:
        queryset: QuerySet[Subscription] = Subscription.objects.filter(user=current_user(self.request))
        if self.action != "list":
            return queryset
        return apply_filters(
            self.request,
            queryset,
            {
                "source": _parse_choice(Source),
                "entity_type": _parse_choice(EntityType),
                "entity_id": str,
                "is_active": _parse_bool,
            },
        )

    def perform_create(self, serializer: serializers.BaseSerializer[Subscription]) -> None:
        serializer.save(user=current_user(self.request))


class RuleViewSet(viewsets.ModelViewSet[Rule]):
    serializer_class = RuleSerializer

    def get_queryset(self) -> QuerySet[Rule]:
        queryset: QuerySet[Rule] = Rule.objects.filter(subscription__user=current_user(self.request))
        if self.action != "list":
            return queryset
        return apply_filters(
            self.request,
            queryset,
            {"subscription": _parse_int, "kind": _parse_choice(RuleKind), "is_active": _parse_bool},
        )
