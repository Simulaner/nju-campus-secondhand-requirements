import logging
from threading import Event, Thread

from django.contrib.staticfiles.management.commands.runserver import (
    Command as Runserver,
)
from django.db import DatabaseError, connections

from qiwu.market.events import archive_failed_audits
from qiwu.market.reservations import expire_due_reservations

SCAN_INTERVAL_SECONDS = 15
logger = logging.getLogger(__name__)


class Command(Runserver):
    help = "Start the local marketplace and scan for expired reservations."

    def inner_run(self, *args, **options):
        self.stop_expiry = Event()
        try:
            return super().inner_run(*args, **options)
        finally:
            self.stop_expiry.set()

    def on_bind(self, server_port):
        # Only the bound server process scans, including when autoreload is enabled.
        self.scan_expired()
        super().on_bind(server_port)
        Thread(target=self.expiry_loop, name="reservation-expiry", daemon=True).start()

    def expiry_loop(self):
        while not self.stop_expiry.wait(SCAN_INTERVAL_SECONDS):
            self.scan_expired()

    def scan_expired(self):
        try:
            expired, failed = expire_due_reservations()
            if expired:
                self.stdout.write(f"已处理 {expired} 笔到期预约。")
            if failed:
                self.stderr.write(f"{failed} 笔到期预约将在下一轮重试。")
            archived = archive_failed_audits()
            if archived:
                self.stdout.write(f"已归档 {archived} 条备用审计。")
        except DatabaseError:
            logger.exception("Expiry scan could not read the database")
        finally:
            connections.close_all()
