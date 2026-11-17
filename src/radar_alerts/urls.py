from django.contrib import admin
from django.urls import URLPattern, URLResolver, include, path
from rest_framework.authtoken.views import obtain_auth_token

urlpatterns: list[URLPattern | URLResolver] = [
    path("admin/", admin.site.urls),
    path("api/v1/auth/token/", obtain_auth_token),
    path("api/v1/", include("radar_alerts.alerts.urls")),
]
