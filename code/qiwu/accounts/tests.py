import re
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import SESSION_KEY
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import EmailVerification, User
from .services import register_user


class RegistrationTests(TestCase):
    email = "reader@nju.edu.cn"
    password = "CampusBooks42"

    def start(self, email=None):
        return self.client.post(reverse("register"), {"email": email or self.email})

    def finish(self, code, **extra):
        fields = {"code": code, "display_name": "读书人", "password": self.password}
        return self.client.post(reverse("verify_registration"), {**fields, **extra})

    def delivered_code(self):
        return re.search(r"\b[0-9]{6}\b", mail.outbox[-1].body).group()

    def test_verified_registration_and_separate_login(self):
        self.assertRedirects(self.start("  Reader@NJU.EDU.CN "), reverse("verify_registration"))
        self.assertFalse(User.objects.exists())
        self.assertEqual(mail.outbox[-1].to, [self.email])
        code = self.delivered_code()
        verification = EmailVerification.objects.get(email=self.email)
        self.assertEqual(len(verification.code_hash), 64)
        self.assertNotEqual(verification.code_hash, code)
        self.assertRedirects(self.finish(code, is_staff="on", is_superuser="on"), reverse("login"))
        user = User.objects.get(email=self.email)
        self.assertTrue(user.check_password(self.password))
        self.assertFalse(user.is_staff or user.is_superuser)
        self.assertNotIn(SESSION_KEY, self.client.session)
        verification.refresh_from_db()
        self.assertIsNotNone(verification.consumed_at)
        self.assertFalse(verification.code_hash)
        self.assertRedirects(
            self.client.post(
                reverse("login"), {"username": "READER@NJU.EDU.CN", "password": self.password}
            ),
            reverse("home"),
        )
        self.assertEqual(self.client.session[SESSION_KEY], str(user.pk))
        self.assertRedirects(self.client.post(reverse("logout")), reverse("home"))
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_only_exact_campus_domain_may_request_code(self):
        for email in (
            "a@example.org",
            "a@dept.nju.edu.cn",
            "a@x.smail.nju.edu.cn",
            "a@nju.edu.cn.example.org",
        ):
            with self.subTest(email=email):
                self.assertContains(self.start(email), "请使用南京大学教工或学生邮箱")
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(EmailVerification.objects.exists())

    def test_student_mailbox_can_register_and_login(self):
        student = "reader@smail.nju.edu.cn"
        self.start(student.upper())
        self.assertEqual(mail.outbox[-1].to, [student])
        self.assertRedirects(self.finish(self.delivered_code()), reverse("login"))
        response = self.client.post(
            reverse("login"), {"username": student, "password": self.password}
        )
        self.assertRedirects(response, reverse("home"))
        self.assertEqual(self.client.session[SESSION_KEY], str(User.objects.get(email=student).pk))

    def test_verification_is_bound_to_the_requested_address(self):
        self.assertRedirects(self.client.get(reverse("verify_registration")), reverse("register"))
        self.start()
        response = self.finish(self.delivered_code(), email="someone@nju.edu.cn")
        self.assertRedirects(response, reverse("login"))
        self.assertEqual(list(User.objects.values_list("email", flat=True)), [self.email])

    def test_duplicate_registration_even_from_second_browser(self):
        other = Client()
        self.start()
        response = other.post(reverse("register"), {"email": self.email.upper()})
        self.assertContains(response, "60 秒")
        code = self.delivered_code()
        self.finish(code)
        with self.assertRaisesMessage(ValidationError, "已注册"):
            register_user(
                email=self.email, code=code, display_name="另一人", password=self.password
            )
        self.assertContains(other.post(reverse("register"), {"email": self.email}), "已注册")
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    @patch("qiwu.accounts.services.secrets.randbelow", side_effect=[123456, 234567])
    def test_resend_boundary_and_replacement(self, _):
        now = timezone.now()
        with patch("qiwu.accounts.services.timezone.now", return_value=now):
            self.start()
        with patch("qiwu.accounts.services.timezone.now", return_value=now + timedelta(seconds=59)):
            self.assertContains(self.start(), "60 秒")
        with patch("qiwu.accounts.services.timezone.now", return_value=now + timedelta(seconds=60)):
            self.assertRedirects(self.start(), reverse("verify_registration"))
            self.assertContains(self.finish("123456"), "验证码错误")
            self.assertRedirects(self.finish("234567"), reverse("login"))
        self.assertEqual(len(mail.outbox), 2)

    def test_expiry_at_ten_minutes_and_just_before(self):
        self.start()
        record = EmailVerification.objects.get(email=self.email)
        self.assertEqual(record.expires_at - record.sent_at, timedelta(minutes=10))
        code = self.delivered_code()
        with patch("qiwu.accounts.services.timezone.now", return_value=record.expires_at):
            self.assertContains(self.finish(code), "已失效")
        self.assertFalse(User.objects.exists())
        # Use a second address and a fresh challenge for the last valid instant.
        self.start("second@nju.edu.cn")
        latest = EmailVerification.objects.get(email="second@nju.edu.cn")
        with patch(
            "qiwu.accounts.services.timezone.now",
            return_value=latest.expires_at - timedelta(microseconds=1),
        ):
            self.assertRedirects(self.finish(self.delivered_code()), reverse("login"))

    @patch("qiwu.accounts.services.secrets.randbelow", return_value=123456)
    def test_fifth_wrong_answer_invalidates_code(self, _):
        self.start()
        for _ in range(4):
            self.assertContains(self.finish("000000"), "验证码错误")
        self.assertContains(self.finish("000000"), "连续输错 5 次")
        self.assertContains(self.finish("123456"), "已失效")
        record = EmailVerification.objects.get(email=self.email)
        self.assertEqual(record.attempts, 5)
        self.assertIsNotNone(record.consumed_at)
        self.assertFalse(User.objects.exists())

    def test_password_and_nickname_checked_before_consuming_code(self):
        self.start()
        code = self.delivered_code()
        self.assertContains(self.finish(code, password="onlyletters"), "包含字母和数字")
        self.assertEqual(self.finish(code, display_name=" " * 2).status_code, 200)
        self.assertIsNone(EmailVerification.objects.get().consumed_at)
        self.assertRedirects(self.finish(code), reverse("login"))

    def test_failed_email_delivery_allows_immediate_retry(self):
        with patch("qiwu.accounts.services.send_mail", side_effect=OSError("unavailable")):
            self.assertContains(self.start(), "验证码发送失败")
        self.assertFalse(EmailVerification.objects.exists())
        self.assertRedirects(self.start(), reverse("verify_registration"))
        self.assertEqual(len(mail.outbox), 1)

    def test_failed_account_insert_does_not_consume_code(self):
        self.start()
        code = self.delivered_code()
        with patch.object(User.objects, "create_user", side_effect=RuntimeError("write failed")):
            with self.assertRaises(RuntimeError):
                register_user(
                    email=self.email, code=code, display_name="读书人", password=self.password
                )
        self.assertIsNone(EmailVerification.objects.get().consumed_at)
        self.assertRedirects(self.finish(code), reverse("login"))

    def test_wrong_password_and_disabled_account(self):
        user = User.objects.create_user(self.email, self.password, display_name="读书人")
        url = reverse("login")
        self.assertContains(
            self.client.post(url, {"username": self.email, "password": "incorrect"}), "账号已停用"
        )
        self.assertNotIn(SESSION_KEY, self.client.session)
        user.is_active = False
        user.save(update_fields=["is_active"])
        self.assertContains(
            self.client.post(url, {"username": self.email, "password": self.password}), "账号已停用"
        )
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_post_requests_require_csrf_token(self):
        browser = Client(enforce_csrf_checks=True)
        self.assertEqual(browser.post(reverse("register"), {"email": self.email}).status_code, 403)
        self.assertEqual(len(mail.outbox), 0)
