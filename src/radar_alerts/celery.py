import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "radar_alerts.settings")

app: Celery = Celery("radar_alerts")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
