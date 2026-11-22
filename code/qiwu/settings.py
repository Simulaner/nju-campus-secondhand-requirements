"""Settings for the locally hosted course project."""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    try:
        SECRET_KEY = (BASE_DIR / ".django-secret").read_text(encoding="ascii").strip()
    except FileNotFoundError as exc:
        raise ImproperlyConfigured("请先在 code/ 中运行 make setup。") from exc

DEBUG = os.environ.get("QIWU_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "[::1]"]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "qiwu.accounts",
    "qiwu.market",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "qiwu.urls"
WSGI_APPLICATION = "qiwu.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "OPTIONS": {"transaction_mode": "IMMEDIATE"},
    },
}

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "home"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "qiwu.accounts.validators.LettersAndDigitsValidator"},
]

EMAIL_HOST = os.environ.get("QIWU_SMTP_HOST", "")
EMAIL_FILE_PATH = BASE_DIR / ".mailbox"
EMAIL_BACKEND = (
    "django.core.mail.backends.smtp.EmailBackend"
    if EMAIL_HOST
    else "django.core.mail.backends.filebased.EmailBackend"
)
SMTP_SECURITY = os.environ.get("QIWU_SMTP_SECURITY", "starttls")
if SMTP_SECURITY not in {"starttls", "ssl", "plain"}:
    raise ImproperlyConfigured("QIWU_SMTP_SECURITY 应为 starttls、ssl 或 plain。")
if SMTP_SECURITY == "plain" and EMAIL_HOST and EMAIL_HOST not in {"localhost", "127.0.0.1", "::1"}:
    raise ImproperlyConfigured("外部邮件服务请使用 starttls 或 ssl。")
EMAIL_USE_TLS = SMTP_SECURITY == "starttls"
EMAIL_USE_SSL = SMTP_SECURITY == "ssl"
EMAIL_PORT = int(os.environ.get("QIWU_SMTP_PORT", "465" if EMAIL_USE_SSL else "587"))
EMAIL_HOST_USER = os.environ.get("QIWU_SMTP_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("QIWU_SMTP_PASSWORD", "")
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = os.environ.get("QIWU_FROM_EMAIL") or EMAIL_HOST_USER or "qiwu@localhost"
