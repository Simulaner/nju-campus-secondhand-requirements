import secrets
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from .models import EmailVerification, User
from .validators import campus_email

CODE_LIFETIME = timedelta(minutes=10)
RESEND_INTERVAL = timedelta(seconds=60)
MAX_ATTEMPTS = 5


def _code_hash(verification, code):
    return salted_hmac(
        "qiwu.registration", f"{verification.pk}:{code}", algorithm="sha256"
    ).hexdigest()


def send_registration_code(email):
    email = campus_email(email)
    with transaction.atomic():
        now = timezone.now()
        if User.objects.filter(email=email).exists():
            raise ValidationError("该邮箱已注册，请直接登录。")
        previous = EmailVerification.objects.filter(email=email).first()
        if previous is not None:
            if now < previous.sent_at + RESEND_INTERVAL:
                raise ValidationError("验证码已发送，请在上次发送 60 秒后重试。")
            previous.delete()
        verification = EmailVerification(email=email, sent_at=now, expires_at=now + CODE_LIFETIME)
        code = f"{secrets.randbelow(1_000_000):06d}"
        verification.code_hash = _code_hash(verification, code)
        verification.save(force_insert=True)

    # An external delivery can take seconds. Release the SQLite write lock first.
    try:
        delivered = send_mail(
            "栖物校园账号注册验证码",
            f"你的栖物注册验证码为 {code}，10 分钟内有效。\n如果并非你本人申请，可忽略这封邮件。",
            settings.DEFAULT_FROM_EMAIL,
            [email],
        )
        if delivered != 1:
            raise OSError("Message not accepted by the email backend")
    except (OSError, ImproperlyConfigured) as exc:
        # A delayed failure must not invalidate a newer code sent for this address.
        EmailVerification.objects.filter(pk=verification.pk).delete()
        raise ValidationError("验证码发送失败，请稍后重新发送。") from exc


def register_user(*, email, code, display_name, password):
    email = campus_email(email)
    with transaction.atomic():
        now = timezone.now()
        if User.objects.filter(email=email).exists():
            raise ValidationError("该邮箱已注册，请直接登录。")
        verification = EmailVerification.objects.filter(email=email).first()
        if (
            verification is None
            or verification.consumed_at is not None
            or now >= verification.expires_at
            or verification.attempts >= MAX_ATTEMPTS
        ):
            raise ValidationError("验证码已失效，请重新获取。")
        if constant_time_compare(verification.code_hash, _code_hash(verification, code)):
            user = User.objects.create_user(email, password, display_name=display_name)
            verification.code_hash = ""
            verification.consumed_at = now
            verification.save(update_fields=["code_hash", "consumed_at"])
            return user
        verification.attempts += 1
        if verification.attempts == MAX_ATTEMPTS:
            verification.code_hash = ""
            verification.consumed_at = now
        verification.save(update_fields=["attempts", "code_hash", "consumed_at"])

    # Record a failed attempt even though the form must report a validation error.
    if verification.attempts == MAX_ATTEMPTS:
        raise ValidationError("验证码已连续输错 5 次，请重新获取。")
    raise ValidationError("验证码错误，请核对邮件。")
