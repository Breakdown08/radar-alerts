from django.urls import URLPattern, URLResolver, path
from rest_framework.routers import SimpleRouter

from radar_alerts.alerts.views import MeView, RuleViewSet, SubscriptionViewSet

router: SimpleRouter = SimpleRouter()
router.register("subscriptions", SubscriptionViewSet, basename="subscription")
router.register("rules", RuleViewSet, basename="rule")

urlpatterns: list[URLPattern | URLResolver] = [path("me/", MeView.as_view()), *router.urls]
