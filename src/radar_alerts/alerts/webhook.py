import hmac
import json
import logging

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from radar_alerts.alerts.bot import Reply, accept_update

logger: logging.Logger = logging.getLogger(__name__)

WEBHOOK_PATH: str = "telegram/webhook/"


@csrf_exempt
def telegram_webhook(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return HttpResponse(status=405)
    if settings.TELEGRAM_MODE != "webhook":
        return HttpResponse(status=404)
    given: str = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(given.encode(), settings.TELEGRAM_WEBHOOK_SECRET.encode()):
        logger.warning("telegram webhook: bad secret")
        return HttpResponse(status=403)
    update: object
    try:
        update = json.loads(request.body)
    except ValueError:
        return HttpResponse(status=400)
    if not isinstance(update, dict):
        return HttpResponse(status=400)
    reply: Reply | None = accept_update(update, timezone.now())
    if reply is None:
        return HttpResponse()
    return JsonResponse({"method": "sendMessage", "chat_id": reply.chat_id, "text": reply.text})
