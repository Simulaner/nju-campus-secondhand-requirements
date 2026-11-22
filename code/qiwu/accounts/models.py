import uuid

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .validators import campus_email


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        user = self.model(email=campus_email(email), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if not extra_fields["is_staff"] or not extra_fields["is_superuser"]:
            raise ValueError("管理员需要同时具备职员和超级用户权限。")
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(
        "校园邮箱", unique=True, db_collation="nocase", validators=[campus_email]
    )
    display_name = models.CharField("昵称", max_length=20)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    session_version = models.PositiveIntegerField(default=1)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["display_name"]

    def __str__(self):
        return self.display_name

    def _get_session_auth_hash(self, secret=None):
        return salted_hmac(
            "qiwu.accounts.User.session",
            f"{self.password}:{self.session_version}",
            secret=secret,
            algorithm="sha256",
        ).hexdigest()


class EmailVerification(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True, db_collation="nocase")
    code_hash = models.CharField(max_length=64)
    sent_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)
