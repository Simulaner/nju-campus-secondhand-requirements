"""WSGI entry point for Qiwu."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qiwu.settings")
application = get_wsgi_application()
