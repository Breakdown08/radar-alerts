from argparse import ArgumentParser

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from radar_alerts.alerts import telegram
from radar_alerts.alerts.telegram import HttpTelegram, TelegramError
from radar_alerts.alerts.webhook import WEBHOOK_PATH


class Command(BaseCommand):
    help: str = "Set, delete or show the Telegram webhook."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("action", choices=["set", "delete", "info"])

    def handle(self, *args: object, **options: object) -> None:
        action: object = options["action"]
        client: HttpTelegram | None = telegram.http_telegram_from_settings()
        if client is None:
            raise CommandError("TELEGRAM_BOT_TOKEN is not set")
        try:
            if action == "set":
                self._set(client)
            elif action == "delete":
                client.delete_webhook()
                self.stdout.write("webhook deleted")
            else:
                self._info(client)
        except TelegramError as exc:
            raise CommandError(str(exc)) from None

    def _set(self, client: HttpTelegram) -> None:
        if settings.TELEGRAM_MODE != "webhook":
            raise CommandError("TELEGRAM_MODE must be webhook")
        if not settings.TELEGRAM_WEBHOOK_URL.startswith("https://"):
            raise CommandError("TELEGRAM_WEBHOOK_URL must start with https://")
        url: str = f"{settings.TELEGRAM_WEBHOOK_URL}/{WEBHOOK_PATH}"
        client.set_webhook(url, settings.TELEGRAM_WEBHOOK_SECRET)
        self.stdout.write(f"webhook set to {url}")

    def _info(self, client: HttpTelegram) -> None:
        info: dict[str, object] = client.webhook_info()
        self.stdout.write(f"url: {info.get('url') or ''}")
        self.stdout.write(f"pending_update_count: {info.get('pending_update_count', 0)}")
        self.stdout.write(f"last_error_message: {info.get('last_error_message') or ''}")
