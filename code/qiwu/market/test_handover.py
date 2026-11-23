import uuid
from datetime import timedelta
from io import BytesIO, StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from . import reservations as service
from .models import (
    AuditLog,
    Category,
    HandoverContact,
    Notification,
    Product,
    ProductStatus,
    Reservation,
    ReservationStatus,
    TradeRecord,
)

User = get_user_model()


class HandoverWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.buyer = User.objects.create_user("buyer@smail.nju.edu.cn", display_name="买家")
        cls.seller = User.objects.create_user("seller@nju.edu.cn", display_name="卖家")
        cls.other = User.objects.create_user("other@nju.edu.cn", display_name="其他同学")
        cls.admin = User.objects.create_superuser("admin@nju.edu.cn", None, display_name="管理员")
        cls.category = Category.objects.get(name="生活用品")

    def setUp(self):
        self.buying = self.session(self.buyer)
        self.selling = self.session(self.seller)
        self.other_session = self.session(self.other)
        self.admin_session = self.session(self.admin)

    @staticmethod
    def session(user):
        browser = Client()
        browser.force_login(user)
        return browser

    def offer(self):
        return Product.objects.create(
            seller=self.seller,
            category=self.category,
            title="阅读台灯",
            description="学习照明",
            price_fen=1975,
            condition=8,
            status=ProductStatus.PUBLISHED,
            published_at=timezone.now(),
        )

    def reserve(self, *, accept=False, product=None):
        product = product or self.offer()
        pk = service.reserve_product(
            actor=self.buyer, pk=product.pk, request_key=uuid.uuid4(), version=product.version
        )
        reservation = Reservation.objects.get(pk=pk)
        if accept:
            response = self.act(self.selling, reservation, "accept")
            self.assertEqual(response.status_code, 302)
            reservation.refresh_from_db()
        return reservation

    def act(self, browser, reservation, action):
        return browser.post(
            reverse("change_reservation", args=[reservation.pk]),
            {
                "version": reservation.version,
                "action": action,
            },
        )

    def confirm(self, browser, reservation):
        return browser.post(reverse("confirm_handover", args=[reservation.pk]))

    def share(self, browser, reservation, value, **options):
        return browser.post(
            reverse("handover_contact", args=[reservation.pk]),
            {
                "action": "share",
                "kind": "other",
                "value": value,
                "consent": "on",
                **options,
            },
        )

    def offline(self, browser, product, *, publication=None):
        publication = product.published_at.isoformat() if publication is None else publication
        return browser.post(
            reverse("unpublish_product", args=[product.pk]),
            {
                "publication": publication,
            },
        )

    def test_buyer_or_seller_can_confirm_first_and_retries_keep_one_trade(self):
        for first, second in [(self.buying, self.selling), (self.selling, self.buying)]:
            with self.subTest(buyer_first=first is self.buying):
                reservation = self.reserve(accept=True)
                trades = TradeRecord.objects.filter(reservation=reservation)
                counts = [trades.count()]
                detail = reverse("my_reservation", args=[reservation.pk])
                first_label = "确认收货" if first is self.buying else "确认交易完成"
                self.assertContains(first.get(detail), first_label)
                self.assertRedirects(self.confirm(first, reservation), detail)
                counts.append(trades.count())
                reservation.refresh_from_db()
                saved = (reservation.buyer_confirmed_at, reservation.seller_confirmed_at)
                self.assertEqual(sum(value is not None for value in saved), 1)
                self.assertEqual(reservation.status, ReservationStatus.ACCEPTED)
                self.assertEqual(reservation.product.status, ProductStatus.RESERVED)
                self.assertNotContains(first.get(detail), first_label)
                self.assertEqual(self.confirm(first, reservation).status_code, 302)
                reservation.refresh_from_db()
                self.assertEqual(
                    (reservation.buyer_confirmed_at, reservation.seller_confirmed_at), saved
                )
                Product.objects.filter(pk=reservation.product_id).update(price_fen=2000)
                self.assertRedirects(self.confirm(second, reservation), detail)
                counts.append(trades.count())
                self.assertEqual(counts, [0, 0, 1])
                reservation.refresh_from_db()
                self.assertTrue(reservation.buyer_confirmed_at and reservation.seller_confirmed_at)
                self.assertEqual(reservation.status, ReservationStatus.COMPLETED)
                self.assertEqual(reservation.product.status, ProductStatus.SOLD)
                self.assertEqual(trades.get().agreed_price_fen, 1975)
                self.assertContains(first.get(detail), "成交价")
                self.assertContains(first.get(detail), "¥19.75")
                self.assertEqual(self.confirm(second, reservation).status_code, 302)
                self.assertEqual(trades.count(), 1)
                self.assertEqual(
                    AuditLog.objects.filter(
                        action="trade_complete", result="success", object_id=reservation.pk
                    ).count(),
                    1,
                )

    def test_confirmation_requires_accepted_reservation_and_the_actual_parties(self):
        reservation = self.reserve()
        self.assertEqual(self.confirm(self.buying, reservation).status_code, 409)
        self.assertEqual(self.act(self.selling, reservation, "accept").status_code, 302)
        self.assertEqual(self.confirm(self.other_session, reservation).status_code, 404)
        self.assertEqual(self.confirm(self.admin_session, reservation).status_code, 403)
        self.assertEqual(self.confirm(self.client, reservation).status_code, 302)
        self.assertFalse(TradeRecord.objects.exists())

    def test_buyer_can_cancel_after_one_confirmation(self):
        reservation = self.reserve(accept=True)
        self.assertEqual(self.confirm(self.selling, reservation).status_code, 302)
        reservation.refresh_from_db()
        self.assertEqual(self.act(self.buying, reservation, "cancel").status_code, 302)
        self.assertEqual(self.confirm(self.buying, reservation).status_code, 409)
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, ReservationStatus.CANCELLED)
        self.assertEqual(reservation.product.status, ProductStatus.PUBLISHED)
        self.assertFalse(TradeRecord.objects.exists())

    def test_completion_rolls_back_if_a_notification_cannot_be_written(self):
        reservation = self.reserve(accept=True)
        self.assertEqual(self.share(self.buying, reservation, "测试联系方式甲").status_code, 302)
        self.assertEqual(self.confirm(self.buying, reservation).status_code, 302)
        with patch.object(
            Notification.objects, "create", side_effect=IntegrityError("test failure")
        ):
            with self.assertLogs("qiwu.market.reservation_views", level="ERROR"):
                response = self.confirm(self.selling, reservation)
        self.assertEqual(response.status_code, 503)
        reservation.refresh_from_db()
        self.assertIsNotNone(reservation.buyer_confirmed_at)
        self.assertIsNone(reservation.seller_confirmed_at)
        self.assertEqual(reservation.status, ReservationStatus.ACCEPTED)
        self.assertEqual(reservation.product.status, ProductStatus.RESERVED)
        self.assertTrue(reservation.contacts.exists())
        self.assertFalse(TradeRecord.objects.exists())
        self.assertTrue(
            AuditLog.objects.filter(
                object_id=reservation.pk, action="reservation_confirm", reason_code="write_failed"
            ).exists()
        )
        self.assertEqual(self.confirm(self.selling, reservation).status_code, 302)
        self.assertEqual(TradeRecord.objects.count(), 1)
        self.assertFalse(reservation.contacts.exists())

    def test_contacts_need_consent_and_are_visible_only_to_the_current_parties(self):
        reservation = self.reserve()
        detail = reverse("my_reservation", args=[reservation.pk])
        public = reverse("public_product", args=[reservation.product_id])
        value = "仅面交使用的验收信息"
        self.assertEqual(self.share(self.buying, reservation, value).status_code, 409)
        self.assertNotContains(self.buying.get(detail), "面交联系方式")
        self.assertEqual(self.act(self.selling, reservation, "accept").status_code, 302)
        self.assertEqual(self.share(self.buying, reservation, value, consent="").status_code, 400)
        self.assertFalse(HandoverContact.objects.exists())
        self.assertEqual(self.share(self.buying, reservation, value).status_code, 302)
        response = self.selling.get(detail)
        self.assertContains(response, value)
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertNotContains(response, self.buyer.email)
        self.assertNotContains(self.client.get(public), value)
        for browser in (self.other_session, self.admin_session):
            self.assertEqual(browser.get(detail).status_code, 404)
        self.assertEqual(self.share(self.other_session, reservation, "测试值").status_code, 404)
        self.assertEqual(self.share(self.admin_session, reservation, "测试值").status_code, 403)
        self.assertNotIn(value, str(reservation.product_snapshot))
        self.assertFalse(Notification.objects.filter(message__contains=value).exists())
        self.assertFalse(AuditLog.objects.filter(reason_code__contains=value).exists())

    def test_each_side_can_update_and_withdraw_only_its_own_contact(self):
        reservation = self.reserve(accept=True)
        detail = reverse("my_reservation", args=[reservation.pk])
        self.assertEqual(self.share(self.buying, reservation, "资料甲").status_code, 302)
        self.assertEqual(self.share(self.buying, reservation, "资料甲更新").status_code, 302)
        self.assertEqual(self.share(self.selling, reservation, "资料乙").status_code, 302)
        self.assertEqual(reservation.contacts.count(), 2)
        self.assertContains(self.selling.get(detail), "资料甲更新")
        response = self.buying.post(
            reverse("handover_contact", args=[reservation.pk]),
            {
                "action": "withdraw",
                "owner": self.seller.pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(reservation.contacts.get().owner_id, self.seller.pk)
        self.assertNotContains(self.selling.get(detail), "资料甲更新")
        self.assertContains(self.buying.get(detail), "资料乙")
        reservation.refresh_from_db()
        self.assertEqual(self.act(self.buying, reservation, "cancel").status_code, 302)
        self.assertFalse(reservation.contacts.exists())
        self.assertNotContains(self.buying.get(detail), "资料乙")

    def test_unpublish_cancels_the_active_reservation_and_old_scan_does_nothing(self):
        product = self.offer()
        reservation = self.reserve(product=product)
        self.assertEqual(self.offline(self.other_session, product).status_code, 404)
        self.assertEqual(self.offline(self.admin_session, product).status_code, 403)
        self.assertEqual(self.offline(self.selling, product).status_code, 302)
        self.assertEqual(self.offline(self.selling, product).status_code, 302)
        self.assertFalse(service.expire_reservation(pk=reservation.pk, now=reservation.expires_at))
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, ReservationStatus.CANCELLED)
        self.assertEqual(reservation.product.status, ProductStatus.OFFLINE)
        self.assertEqual(
            self.buying.get(reverse("public_product", args=[product.pk])).status_code, 404
        )
        self.assertEqual(
            Notification.objects.filter(
                recipient=self.buyer, object_id=reservation.pk, message__contains="下架"
            ).count(),
            1,
        )
        self.assertEqual(
            AuditLog.objects.filter(
                object_id=product.pk, action="product_unpublish", result="success"
            ).count(),
            1,
        )

    def test_unpublish_removes_contacts_on_an_accepted_reservation(self):
        reservation = self.reserve(accept=True)
        self.assertEqual(self.share(self.selling, reservation, "联络测试").status_code, 302)
        self.assertEqual(self.offline(self.selling, reservation.product).status_code, 302)
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, ReservationStatus.CANCELLED)
        self.assertFalse(reservation.contacts.exists())
        detail = reverse("my_reservation", args=[reservation.pk])
        self.assertNotContains(self.buying.get(detail), "联络测试")
        self.assertEqual(self.confirm(self.buying, reservation).status_code, 409)

    def test_sold_goods_cannot_be_unpublished(self):
        reservation = self.reserve(accept=True)
        self.assertEqual(self.confirm(self.buying, reservation).status_code, 302)
        self.assertEqual(self.confirm(self.selling, reservation).status_code, 302)
        self.assertEqual(self.offline(self.selling, reservation.product).status_code, 409)
        self.assertEqual(TradeRecord.objects.count(), 1)
        reservation.refresh_from_db()
        self.assertEqual(reservation.product.status, ProductStatus.SOLD)

    def test_unpublish_rolls_back_if_the_buyer_notification_cannot_be_written(self):
        reservation = self.reserve(accept=True)
        self.assertEqual(self.share(self.selling, reservation, "测试联系值").status_code, 302)
        with patch.object(
            Notification.objects, "create", side_effect=IntegrityError("test failure")
        ):
            with self.assertLogs("qiwu.market.views", level="ERROR"):
                response = self.offline(self.selling, reservation.product)
        self.assertEqual(response.status_code, 503)
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, ReservationStatus.ACCEPTED)
        self.assertEqual(reservation.product.status, ProductStatus.RESERVED)
        self.assertTrue(reservation.contacts.exists())
        self.assertTrue(
            AuditLog.objects.filter(
                object_id=reservation.product_id,
                action="product_unpublish",
                reason_code="write_failed",
            ).exists()
        )

    def test_seller_can_unpublish_after_expiry_from_the_same_publication(self):
        product = self.offer()
        reservation = self.reserve(product=product)
        form = self.selling.get(reverse("unpublish_product", args=[product.pk])).context["form"]
        publication = form.initial["publication"]
        self.assertTrue(service.expire_reservation(pk=reservation.pk, now=reservation.expires_at))
        self.assertEqual(
            self.offline(self.selling, product, publication=publication).status_code, 302
        )
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, ReservationStatus.EXPIRED)
        self.assertEqual(reservation.product.status, ProductStatus.OFFLINE)

    def test_voluntary_unpublish_allows_resubmission_and_republication_rejects_old_form(self):
        product = self.offer()
        old = product.published_at.isoformat()
        self.assertEqual(self.offline(self.selling, product).status_code, 302)
        product.refresh_from_db()
        self.assertTrue(product.editable)
        buffer = BytesIO()
        Image.new("RGB", (16, 16), "purple").save(buffer, format="PNG")
        uploaded = SimpleUploadedFile("listing.png", buffer.getvalue(), content_type="image/png")
        response = self.selling.post(
            reverse("edit_product", args=[product.pk]),
            {
                "version": product.version,
                "action": "submit",
                "title": product.title,
                "category": self.category.pk,
                "price": "21.25",
                "condition": 8,
                "description": product.description,
                "images": [uploaded],
            },
        )
        self.assertEqual(response.status_code, 302)
        product.refresh_from_db()
        self.assertEqual(product.status, ProductStatus.PENDING)
        response = self.admin_session.post(
            reverse("moderate", args=[product.pk]),
            {
                "version": product.version,
                "decision": "approved",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.offline(self.selling, product, publication=old).status_code, 409)
        product.refresh_from_db()
        self.assertEqual(product.status, ProductStatus.PUBLISHED)

    def test_scan_at_deadline_is_idempotent_and_accepted_reservations_stay_active(self):
        awaiting = self.reserve()
        accepted = self.reserve(accept=True)
        before = awaiting.expires_at - timedelta(microseconds=1)
        self.assertFalse(service.expire_reservation(pk=awaiting.pk, now=before))
        with patch("qiwu.market.reservations.timezone.now", return_value=awaiting.expires_at):
            result = StringIO()
            call_command("expire_reservations", stdout=result)
            self.assertIn("已处理 1 笔到期预约", result.getvalue())
            self.assertEqual(service.expire_due_reservations(), (0, 0))
        self.assertFalse(
            service.expire_reservation(
                pk=accepted.pk, now=accepted.expires_at + timedelta(minutes=1)
            )
        )
        awaiting.refresh_from_db()
        accepted.refresh_from_db()
        self.assertEqual(awaiting.status, ReservationStatus.EXPIRED)
        self.assertEqual(awaiting.product.status, ProductStatus.PUBLISHED)
        self.assertEqual(accepted.status, ReservationStatus.ACCEPTED)
        self.assertEqual(accepted.product.status, ProductStatus.RESERVED)
        self.assertEqual(
            Notification.objects.filter(object_id=awaiting.pk, message__contains="已超时").count(),
            2,
        )
        self.assertEqual(
            AuditLog.objects.filter(
                object_id=awaiting.pk,
                action="reservation_expire",
                result="success",
                actor=None,
            ).count(),
            1,
        )

    def test_scan_leaves_offline_goods_offline_and_continues_after_an_item_fails(self):
        stalled = self.reserve()
        offline = self.reserve()
        Product.objects.filter(pk=offline.product_id).update(status=ProductStatus.OFFLINE)
        original_notify = service._notify

        def notify(reservation, *args):
            if reservation.pk == stalled.pk:
                raise IntegrityError("test failure")
            return original_notify(reservation, *args)

        cutoff = max(stalled.expires_at, offline.expires_at)
        with patch("qiwu.market.reservations.timezone.now", return_value=cutoff):
            with patch.object(service, "_notify", side_effect=notify):
                with self.assertLogs("qiwu.market.reservations", level="ERROR"):
                    self.assertEqual(service.expire_due_reservations(), (1, 1))
            stalled.refresh_from_db()
            self.assertEqual(stalled.status, ReservationStatus.PENDING)
            self.assertEqual(stalled.product.status, ProductStatus.RESERVED)
            self.assertEqual(service.expire_due_reservations(), (1, 0))
        offline.refresh_from_db()
        self.assertEqual(offline.status, ReservationStatus.EXPIRED)
        self.assertEqual(offline.product.status, ProductStatus.OFFLINE)
