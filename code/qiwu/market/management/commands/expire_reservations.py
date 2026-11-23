from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from qiwu.market.reservations import expire_due_reservations


class Command(BaseCommand):
    help = "Scan pending reservations that have reached their seller deadline."

    def handle(self, *args, **options):
        try:
            expired, failed = expire_due_reservations()
        except DatabaseError as exc:
            raise CommandError("无法扫描预约，请在数据库可用后重试。") from exc
        self.stdout.write(f"已处理 {expired} 笔到期预约。")
        if failed:
            raise CommandError(f"仍有 {failed} 笔预约需要重试，详见审计记录。")
