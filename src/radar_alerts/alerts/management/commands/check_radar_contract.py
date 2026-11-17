from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from radar_alerts.alerts import radar


class Command(BaseCommand):
    help: str = "Compare the radar's alerts_v1 views with the schema expected by this service."

    def handle(self, *args: object, **options: object) -> None:
        problems: list[str] = radar.clickhouse_from_settings().contract_problems()
        title: str = f"radar contract v{radar.CONTRACT_VERSION} in {settings.CLICKHOUSE_DATABASE}"
        if not problems:
            self.stdout.write(f"{title}: ok")
            return
        problem: str
        for problem in problems:
            self.stderr.write(problem)
        raise CommandError(f"{title}: {len(problems)} problem(s)")
