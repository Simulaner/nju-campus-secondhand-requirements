import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen

import django
from django.apps import apps
from django.core.management import call_command
from django.db import connections

SOURCE = Path(__file__).resolve().parents[1]


@contextmanager
def isolated_app():
    if apps.ready:
        raise RuntimeError("Start this check in a new Python process.")
    with TemporaryDirectory(prefix="qiwu-qa-") as temporary:
        root = Path(temporary)
        for name in ("qiwu", "templates", "static"):
            shutil.copytree(
                SOURCE / name,
                root / name,
                ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"),
            )
        shutil.copy2(SOURCE / "manage.py", root / "manage.py")
        os.environ.update(
            DJANGO_SETTINGS_MODULE="qiwu.settings",
            DJANGO_SECRET_KEY=secrets.token_urlsafe(40),
            QIWU_DEBUG="1",
            QIWU_SMTP_HOST="",
        )
        sys.path.insert(0, str(root))
        django.setup()
        call_command("migrate", interactive=False, verbosity=0)
        try:
            yield root
        finally:
            connections.close_all()


@contextmanager
def serve(root, log_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "manage.py", "serve_market", f"127.0.0.1:{port}", "--noreload"],
            cwd=root,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError(f"The local server stopped. See {log_path}.")
                try:
                    with urlopen(f"http://127.0.0.1:{port}/", timeout=0.4) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.07)
            else:
                raise RuntimeError(f"The local server did not become ready. See {log_path}.")
            yield port
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
