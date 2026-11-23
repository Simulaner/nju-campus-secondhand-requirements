import uuid
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, OperationalError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .events import archive_failed_audits, record_action
from .models import (
    AuditLog,
    Category,
    ModerationAction,
    ModerationRecord,
    Notification,
    Product,
    ProductStatus,
    Reservation,
    ReservationStatus,
    TradeRecord,
)
from .reservations import (
    change_reservation,
    confirm_handover,
    reserve_product,
    save_handover_contact,
)

User = get_user_model()


class ManagementTests(TestCase):
    password = "TestAccount42"

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            "admin@nju.edu.cn", None, display_name="验收管理员"
        )
        cls.first = User.objects.create_user(
            "first@smail.nju.edu.cn", cls.password, display_name="小朱"
        )
        cls.second = User.objects.create_user("second@nju.edu.cn", display_name="小王")
        cls.other = User.objects.create_user("other@nju.edu.cn", display_name="无关用户")
        cls.electronics = Category.objects.get(name="数码电子")
        cls.books = Category.objects.get(name="图书教材")

    def setUp(self):
        self.staff = self.session(self.admin)
        self.acting = self.session(self.first)
        self.partner = self.session(self.second)
        self.outsider = self.session(self.other)

    @staticmethod
    def session(user):
        client = Client()
        client.force_login(user)
        return client

    def listing(self, title, *, seller=None, **fields):
        data = {
            "title": title,
            "seller": seller or self.first,
            "price_fen": 4250,
            "description": "校内面交",
            "category": self.electronics,
            "condition": 8,
            "status": ProductStatus.PUBLISHED,
            "published_at": timezone.now(),
            **fields,
        }
        return Product.objects.create(**data)

    def book(self, product, buyer, *, accept=False):
        pk = reserve_product(
            actor=buyer, pk=product.pk, request_key=uuid.uuid4(), version=product.version
        )
        result = Reservation.objects.get(pk=pk)
        if accept:
            change_reservation(actor=product.seller, pk=pk, action="accept", version=result.version)
            result.refresh_from_db()
        product.refresh_from_db()
        return result

    def product_action(self, product, action, reason="", browser=None):
        return (browser or self.staff).post(
            reverse("managed_product", args=[product.pk]),
            {
                "version": product.version,
                "action": action,
                "reason": reason,
            },
        )

    def account_action(self, account, action, reason="", browser=None):
        return (browser or self.staff).post(
            reverse("managed_account", args=[account.pk]),
            {
                "version": account.session_version,
                "action": action,
                "reason": reason,
            },
        )

    def test_admin_pages_and_writes_require_staff_and_drafts_are_private(self):
        draft = self.listing("未公开私有草稿", status=ProductStatus.DRAFT)
        product = self.listing("已发布商品")
        for name in ("moderation_dashboard", "managed_products", "managed_accounts", "audit_log"):
            path = reverse(name)
            self.assertEqual(self.client.get(path).status_code, 302)
            self.assertEqual(self.acting.get(path).status_code, 403)
        self.assertNotContains(self.staff.get(reverse("managed_products")), draft.title)
        self.assertEqual(
            self.staff.get(reverse("managed_product", args=[draft.pk])).status_code, 404
        )
        self.assertEqual(
            self.staff.get(reverse("managed_account", args=[self.admin.pk])).status_code, 404
        )
        self.assertEqual(
            self.product_action(
                product, ModerationAction.VIOLATION, "理由", self.acting
            ).status_code,
            403,
        )
        self.assertEqual(
            self.account_action(
                self.first, ModerationAction.DISABLE, "理由", self.outsider
            ).status_code,
            403,
        )
        self.assertFalse(ModerationRecord.objects.exists())

    def test_violation_cancels_reservation_and_preserves_staff_reason_for_seller(self):
        product = self.listing("预约中的台灯")
        reservation = self.book(product, self.second, accept=True)
        save_handover_contact(
            actor=self.first, pk=reservation.pk, details={"kind": "other", "value": "测试联系值"}
        )
        blank = self.product_action(product, ModerationAction.VIOLATION, "  ")
        self.assertContains(blank, "请填写处理原因", status_code=400)
        reason = "该商品内容与审核信息不一致"
        self.assertEqual(
            self.product_action(product, ModerationAction.VIOLATION, reason).status_code, 302
        )
        self.assertEqual(
            self.product_action(product, ModerationAction.VIOLATION, reason).status_code, 409
        )
        self.assertEqual(ModerationRecord.objects.filter(product=product).count(), 1)
        product.refresh_from_db()
        reservation.refresh_from_db()
        self.assertEqual(product.status, ProductStatus.OFFLINE)
        self.assertTrue(product.relist_blocked)
        self.assertEqual(reservation.status, ReservationStatus.CANCELLED)
        self.assertFalse(reservation.contacts.exists())
        self.assertContains(self.acting.get(reverse("my_product", args=[product.pk])), reason)
        self.assertContains(
            self.acting.get(reverse("my_product", args=[product.pk])), self.admin.display_name
        )
        buyer_page = self.partner.get(reverse("my_reservation", args=[reservation.pk]))
        self.assertContains(buyer_page, "商品因违规下架")
        self.assertNotContains(buyer_page, reason)
        self.assertTrue(
            Notification.objects.filter(recipient=self.second, object_id=reservation.pk).exists()
        )
        self.assertTrue(
            Notification.objects.filter(recipient=self.first, object_id=product.pk).exists()
        )
        self.assertEqual(
            self.partner.get(reverse("public_product", args=[product.pk])).status_code, 404
        )

    def test_unblock_leaves_product_offline_until_a_fresh_review(self):
        product = self.listing("重新送审的商品")
        self.assertEqual(
            self.product_action(product, ModerationAction.VIOLATION, "请完善资料").status_code, 302
        )
        product.refresh_from_db()
        edit = reverse("edit_product", args=[product.pk])
        self.assertEqual(
            self.acting.post(edit, {"action": "submit", "version": product.version}).status_code,
            409,
        )
        self.assertEqual(self.product_action(product, ModerationAction.UNBLOCK).status_code, 302)
        product.refresh_from_db()
        self.assertEqual(product.status, ProductStatus.OFFLINE)
        self.assertFalse(product.relist_blocked)
        self.assertEqual(
            self.partner.get(reverse("public_product", args=[product.pk])).status_code, 404
        )
        pixels = BytesIO()
        Image.new("RGB", (20, 20), "blue").save(pixels, format="PNG")
        image = SimpleUploadedFile("product.png", pixels.getvalue(), content_type="image/png")
        self.assertEqual(
            self.acting.post(
                edit,
                {
                    "version": product.version,
                    "action": "submit",
                    "title": product.title,
                    "category": self.electronics.pk,
                    "condition": 8,
                    "price": "45.00",
                    "description": "已完善资料",
                    "images": [image],
                },
            ).status_code,
            302,
        )
        product.refresh_from_db()
        self.assertEqual(product.status, ProductStatus.PENDING)
        self.assertEqual(
            self.staff.post(
                reverse("moderate", args=[product.pk]),
                {
                    "version": product.version,
                    "decision": "approved",
                },
            ).status_code,
            302,
        )
        self.assertContains(
            self.partner.get(reverse("public_product", args=[product.pk])), "¥45.00"
        )
        self.assertContains(self.acting.get(reverse("my_product", args=[product.pk])), "请完善资料")

    def test_suspension_closes_both_roles_keeps_history_and_revokes_old_sessions(self):
        public = self.listing("账号在售商品")
        own = self.listing("账号作为卖家")
        own_res = self.book(own, self.second, accept=True)
        foreign = self.listing("账号作为买家", seller=self.second)
        incoming = self.book(foreign, self.first, accept=True)
        save_handover_contact(
            actor=self.first, pk=incoming.pk, details={"kind": "other", "value": "暂停测试联系值"}
        )
        sold = self.listing("账号已完成交易")
        finished = self.book(sold, self.second, accept=True)
        confirm_handover(actor=self.first, pk=finished.pk)
        confirm_handover(actor=self.second, pk=finished.pk)
        pending = self.listing("停用期间的待审稿", status=ProductStatus.PENDING)
        dormant = self.session(self.first)
        page = self.staff.get(reverse("managed_account", args=[self.first.pk]))
        self.assertEqual((page.context["product_count"], page.context["reservation_count"]), (2, 2))
        self.assertContains(
            self.account_action(self.first, ModerationAction.DISABLE),
            "请填写处理原因",
            status_code=400,
        )
        self.assertEqual(
            self.account_action(self.first, ModerationAction.DISABLE, "账号处理验收").status_code,
            302,
        )
        self.assertEqual(
            self.account_action(self.first, ModerationAction.DISABLE, "同一旧表单").status_code, 409
        )
        self.first.refresh_from_db()
        self.assertFalse(self.first.is_active)
        self.assertEqual(self.first.session_version, 2)
        self.assertEqual(self.acting.get(reverse("my_records")).status_code, 302)
        self.assertContains(
            self.acting.post(
                reverse("login"),
                {
                    "username": self.first.email,
                    "password": self.password,
                },
            ),
            "账号已停用",
        )
        for item in (own, public):
            item.refresh_from_db()
            self.assertEqual(item.status, ProductStatus.OFFLINE)
        for result in (own_res, incoming):
            result.refresh_from_db()
            self.assertEqual(result.status, ReservationStatus.CANCELLED)
            self.assertFalse(result.contacts.exists())
            self.assertTrue(
                Notification.objects.filter(recipient=self.second, object_id=result.pk).exists()
            )
        foreign.refresh_from_db()
        self.assertEqual(foreign.status, ProductStatus.PUBLISHED)
        sold.refresh_from_db()
        self.assertEqual(sold.status, ProductStatus.SOLD)
        self.assertTrue(TradeRecord.objects.filter(reservation=finished).exists())
        self.assertContains(
            self.staff.post(
                reverse("moderate", args=[pending.pk]),
                {"version": pending.version, "decision": "approved"},
            ),
            "卖家账号已停用",
            status_code=409,
        )
        self.assertEqual(self.account_action(self.first, ModerationAction.RESTORE).status_code, 302)
        self.first.refresh_from_db()
        self.assertTrue(self.first.is_active)
        self.assertEqual(self.first.session_version, 3)
        self.assertEqual(dormant.get(reverse("my_records")).status_code, 302)
        self.assertEqual(
            self.acting.post(
                reverse("login"),
                {
                    "username": self.first.email,
                    "password": self.password,
                },
            ).status_code,
            302,
        )
        public.refresh_from_db()
        self.assertEqual(public.status, ProductStatus.OFFLINE)
        self.assertContains(self.acting.get(reverse("my_records")), "账号处理验收")

    def test_restore_keeps_legacy_suspended_listings_unpublished(self):
        old = self.listing("旧数据库中的公开记录")
        User.objects.filter(pk=self.first.pk).update(is_active=False)
        self.first.refresh_from_db()
        self.assertEqual(self.client.get(reverse("public_product", args=[old.pk])).status_code, 404)
        self.assertEqual(self.account_action(self.first, ModerationAction.RESTORE).status_code, 302)
        old.refresh_from_db()
        self.assertEqual(old.status, ProductStatus.OFFLINE)

    def test_suspension_rolls_back_session_version_and_product_if_notice_fails(self):
        product = self.listing("事务回滚样品")
        reservation = self.book(product, self.second)
        with patch.object(
            Notification.objects, "create", side_effect=IntegrityError("test failure")
        ):
            with self.assertLogs("qiwu.market.management_views", level="ERROR"):
                response = self.account_action(self.first, ModerationAction.DISABLE, "失败处理")
        self.assertEqual(response.status_code, 503)
        self.first.refresh_from_db()
        reservation.refresh_from_db()
        self.assertTrue(self.first.is_active)
        self.assertEqual(self.first.session_version, 1)
        self.assertEqual(reservation.status, ReservationStatus.PENDING)
        self.assertEqual(reservation.product.status, ProductStatus.RESERVED)
        self.assertFalse(ModerationRecord.objects.exists())
        self.assertTrue(
            AuditLog.objects.filter(
                object_id=self.first.pk,
                action=ModerationAction.DISABLE,
                result="failure",
                reason_code="write_failed",
            ).exists()
        )

    def test_inbox_read_operations_are_scoped_and_check_the_target_again(self):
        product = self.listing("我的通知商品")
        own = Notification.objects.create(
            recipient=self.first,
            event_key="own",
            object_type="product",
            object_id=product.pk,
            message="本人消息",
        )
        hidden = self.listing("他人的草稿", seller=self.second, status=ProductStatus.DRAFT)
        inaccessible = Notification.objects.create(
            recipient=self.first,
            event_key="inaccessible",
            object_type="product",
            object_id=hidden.pk,
            message="过期通知",
        )
        other = Notification.objects.create(
            recipient=self.second,
            event_key="other",
            object_type="product",
            object_id=product.pk,
            message="他人私有消息",
        )
        inbox = reverse("notifications")
        endpoint = reverse("read_notification", args=[own.pk])
        response = self.acting.get(inbox)
        self.assertEqual(response.context["unread"], 2)
        self.assertContains(response, "本人消息")
        self.assertNotContains(response, "他人私有消息")
        self.assertEqual(self.partner.post(endpoint).status_code, 404)
        self.assertEqual(self.acting.get(endpoint).status_code, 405)
        self.assertRedirects(self.acting.post(endpoint), reverse("my_product", args=[product.pk]))
        own.refresh_from_db()
        timestamp = own.read_at
        self.assertIsNotNone(timestamp)
        self.acting.post(endpoint)
        own.refresh_from_db()
        self.assertEqual(own.read_at, timestamp)
        self.assertRedirects(
            self.acting.post(reverse("read_notification", args=[inaccessible.pk])), inbox
        )
        new = Notification.objects.create(
            recipient=self.first,
            event_key="new",
            object_type="account",
            object_id=self.first.pk,
            message="账号处理消息",
        )
        self.assertRedirects(self.acting.post(reverse("read_all_notifications")), inbox)
        new.refresh_from_db()
        other.refresh_from_db()
        self.assertIsNotNone(new.read_at)
        self.assertIsNone(other.read_at)
        self.assertContains(self.acting.get(inbox, {"status": "unread"}), "暂无未读消息")

    def test_statistics_count_products_once_even_with_multiple_reservations(self):
        sold = self.listing("已完成交易")
        first = self.book(sold, self.second)
        change_reservation(actor=self.first, pk=first.pk, action="reject", version=first.version)
        sold.refresh_from_db()
        finished = self.book(sold, self.other, accept=True)
        confirm_handover(actor=self.first, pk=finished.pk)
        confirm_handover(actor=self.other, pk=finished.pk)
        active = self.listing("预约中书籍", category=self.books)
        self.book(active, self.second)
        self.listing("公开教材", category=self.books)
        self.listing("待审核教材", category=self.books, status=ProductStatus.PENDING)
        self.listing("驳回商品", status=ProductStatus.REJECTED)
        self.listing("未分类草稿", category=None, status=ProductStatus.DRAFT)
        data = self.staff.get(reverse("moderation_dashboard")).context
        self.assertEqual(
            (
                data["product_total"],
                data["pending_total"],
                data["active_total"],
                data["trade_total"],
            ),
            (6, 1, 1, 1),
        )
        self.assertEqual(dict(data["categories"])["图书教材"], 3)
        self.assertEqual(dict(data["categories"])["数码电子"], 2)
        self.assertEqual(dict(data["categories"])["未分类"], 1)
        self.assertEqual(dict(data["states"])["已发布"], 1)
        self.assertEqual(dict(data["states"])["已售出"], 1)
        self.assertEqual(dict(data["states"])["已驳回"], 1)

    def test_personal_trade_history_is_scoped_to_the_parties(self):
        item = self.listing("个人成交样品")
        reservation = self.book(item, self.second, accept=True)
        self.assertEqual(
            list(
                self.acting.get(reverse("my_records"), {"reservations": "active"}).context[
                    "reservations"
                ]
            ),
            [reservation],
        )
        confirm_handover(actor=self.first, pk=reservation.pk)
        confirm_handover(actor=self.second, pk=reservation.pk)
        page = self.acting.get(reverse("my_records"), {"reservations": "history"})
        self.assertContains(page, item.title)
        self.assertContains(page, "卖出 · ¥42.50")
        self.assertContains(self.partner.get(reverse("my_records")), "买入 · ¥42.50")
        self.assertNotContains(self.outsider.get(reverse("my_records")), item.title)
        self.assertFalse(
            self.acting.get(reverse("my_records"), {"reservations": "active"}).context[
                "reservations"
            ]
        )

    def test_audit_page_is_read_only_and_filters_failure_without_private_details(self):
        item = self.listing("操作日志样品")
        for fields in [
            {
                "actor": self.admin,
                "action": "product_violation",
                "result": "failure",
                "reason_code": "stale_product",
            },
            {"actor": None, "action": "reservation_expire", "result": "success", "reason_code": ""},
        ]:
            AuditLog.objects.create(object_type="product", object_id=item.pk, **fields)
        url = reverse("audit_log")
        self.assertContains(self.staff.get(url), "系统")
        result = self.staff.get(url, {"result": "failure", "actor": self.admin.display_name})
        self.assertContains(result, self.admin.display_name)
        self.assertContains(result, "stale_product")
        self.assertNotContains(result, "预约超时")
        self.assertEqual(self.staff.post(url).status_code, 405)
        self.assertEqual(self.staff.delete(url).status_code, 405)
        self.assertEqual(AuditLog.objects.count(), 2)

    def test_unavailable_database_falls_back_to_a_private_audit_file_and_archives_once(self):
        target = uuid.uuid4()
        with TemporaryDirectory() as directory, override_settings(BASE_DIR=Path(directory)):
            with patch.object(AuditLog, "save", side_effect=OperationalError("DB unavailable")):
                record_action(
                    self.admin, "product", target, "product_violation", failure="write_failed"
                )
            backup = Path(directory) / ".audit-backup.jsonl"
            self.assertEqual(backup.stat().st_mode & 0o077, 0)
            self.assertNotIn(self.admin.email, backup.read_text())
            self.assertEqual(archive_failed_audits(), 1)
            self.assertEqual(archive_failed_audits(), 0)
        restored = AuditLog.objects.get()
        self.assertEqual(restored.object_id, target)
        self.assertEqual(restored.actor_id, self.admin.pk)
        self.assertEqual(restored.reason_code, "write_failed")
