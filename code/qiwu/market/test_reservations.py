import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    AuditLog,
    HandoverContact,
    Notification,
    Product,
    ProductStatus,
    RequestReceipt,
    Reservation,
    ReservationStatus,
    TradeRecord,
)

User = get_user_model()


class ReservationWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.seller = User.objects.create_user("seller@smail.nju.edu.cn", display_name="卖家")
        cls.buyer = User.objects.create_user("buyer@nju.edu.cn", display_name="买家")
        cls.other = User.objects.create_user("other@smail.nju.edu.cn", display_name="其他同学")
        cls.admin = User.objects.create_superuser("admin@nju.edu.cn", None, display_name="管理员")
        cls.product = Product.objects.create(
            seller=cls.seller,
            title="折叠台灯",
            status=ProductStatus.PUBLISHED,
            price_fen=3285,
            condition=8,
            meeting_place="教学楼门口",
            published_at=timezone.now(),
        )

    def setUp(self):
        self.selling = self.session(self.seller)
        self.buying = self.session(self.buyer)
        self.other_session = self.session(self.other)
        self.public_url = reverse("public_product", args=[self.product.pk])
        self.reserve_url = reverse("reserve_product", args=[self.product.pk])

    def session(self, user):
        browser = Client()
        browser.force_login(user)
        return browser

    def read_form(self, browser=None, product=None):
        browser = browser or self.buying
        product = product or self.product
        form = browser.get(reverse("public_product", args=[product.pk])).context["reserve_form"]
        return form.initial

    def book(self, data=None, browser=None):
        browser = browser or self.buying
        data = data or self.read_form(browser)
        response = browser.post(self.reserve_url, data)
        reservation = Reservation.objects.get(buyer=self.buyer, product=self.product)
        self.assertRedirects(response, reverse("my_reservation", args=[reservation.pk]))
        return reservation

    def change(self, browser, reservation, action, **overrides):
        url = reverse("change_reservation", args=[reservation.pk])
        return browser.post(url, {"action": action, "version": reservation.version, **overrides})

    def test_only_buyers_get_the_form_and_posts_require_csrf(self):
        self.assertContains(self.client.get(self.public_url), "登录后预约")
        self.assertNotContains(self.selling.get(self.public_url), "预约商品")
        admin = self.session(self.admin)
        self.assertNotContains(admin.get(self.public_url), "预约商品")
        values = self.read_form()
        for browser in (self.selling, admin):
            self.assertEqual(browser.post(self.reserve_url, values).status_code, 403)
        self.assertEqual(self.client.post(self.reserve_url, values).status_code, 302)
        checked = Client(enforce_csrf_checks=True)
        checked.force_login(self.buyer)
        values = self.read_form(checked)
        self.assertEqual(checked.post(self.reserve_url, values).status_code, 403)
        token = checked.cookies["csrftoken"].value
        response = checked.post(self.reserve_url, {**values, "csrfmiddlewaretoken": token})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Reservation.objects.count(), 1)

    def test_first_request_reserves_product_and_replay_returns_same_receipt(self):
        data = self.read_form()
        other_data = self.read_form(self.other_session)
        reservation = self.book(data)
        response = self.buying.post(self.reserve_url, data)
        self.assertRedirects(response, reverse("my_reservation", args=[reservation.pk]))
        self.assertEqual(Reservation.objects.count(), 1)
        self.assertEqual(RequestReceipt.objects.get().object_id, reservation.pk)
        self.assertEqual(Notification.objects.get().recipient_id, self.seller.pk)
        self.assertEqual(AuditLog.objects.filter(action="reserve", result="success").count(), 1)
        self.assertEqual(reservation.expires_at - reservation.created_at, timedelta(hours=24))
        self.assertEqual(reservation.product_snapshot["price_fen"], 3285)
        self.assertNotIn(self.seller.email, str(reservation.product_snapshot))
        self.assertEqual(reservation.status, ReservationStatus.PENDING)
        self.assertEqual(reservation.product.status, ProductStatus.RESERVED)
        self.assertEqual(reservation.product.version, self.product.version + 1)
        response = self.other_session.post(self.reserve_url, other_data)
        self.assertContains(response, "已有有效预约", status_code=409)
        self.assertContains(self.buying.get(self.public_url), "查看我的预约")
        self.assertNotContains(self.other_session.get(self.public_url), "预约商品")
        self.assertNotContains(self.other_session.get(self.public_url), "查看我的预约")

    def test_request_key_binds_product_and_version_and_is_scoped_to_actor(self):
        data = self.read_form()
        self.book(data)
        second = Product.objects.create(
            seller=self.seller, title="另一盏灯", status=ProductStatus.PUBLISHED, price_fen=9800
        )
        second_url = reverse("reserve_product", args=[second.pk])
        wrong_version = self.buying.post(self.reserve_url, {**data, "version": data["version"] + 1})
        result = self.buying.post(second_url, {**data, "version": second.version})
        for response in (wrong_version, result):
            self.assertContains(response, "已用于另一笔操作", status_code=409)
        response = self.other_session.post(second_url, {**data, "version": second.version})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            RequestReceipt.objects.filter(request_key=str(data["request_key"])).count(), 2
        )

    def test_old_product_pages_are_rejected_and_private_objects_stay_hidden(self):
        data = self.read_form()
        Product.objects.filter(pk=self.product.pk).update(price_fen=9900, version=9)
        response = self.buying.post(self.reserve_url, data)
        self.assertContains(response, "商品信息已更新", status_code=409)
        self.assertFalse(Reservation.objects.exists())
        for state in (
            ProductStatus.DRAFT,
            ProductStatus.PENDING,
            ProductStatus.OFFLINE,
            ProductStatus.SOLD,
        ):
            Product.objects.filter(pk=self.product.pk).update(status=state)
            with self.subTest(state=state):
                result = self.buying.post(self.reserve_url, {**data, "version": 9})
                self.assertContains(result, "该商品当前不可预约", status_code=409)
                self.assertNotContains(result, self.product.title, status_code=409)
        self.assertFalse(RequestReceipt.objects.exists())

    def test_seller_can_accept_but_other_accounts_cannot_act(self):
        reservation = self.book()
        detail = reverse("my_reservation", args=[reservation.pk])
        self.assertContains(self.selling.get(detail), "接受预约")
        self.assertNotContains(self.buying.get(detail), "接受预约")
        self.assertContains(self.buying.get(detail), "取消预约")
        self.assertEqual(self.other_session.get(detail).status_code, 404)
        self.assertEqual(self.change(self.other_session, reservation, "accept").status_code, 404)
        self.assertEqual(self.change(self.buying, reservation, "accept").status_code, 403)
        self.assertEqual(self.change(self.selling, reservation, "cancel").status_code, 403)
        self.assertEqual(
            self.change(self.session(self.admin), reservation, "accept").status_code, 403
        )
        self.assertRedirects(self.change(self.selling, reservation, "accept"), detail)
        self.assertContains(self.buying.get(detail), "已确认待交接")
        self.assertNotContains(self.buying.get(detail), self.seller.email)
        self.assertEqual(self.change(self.selling, reservation, "accept").status_code, 409)
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, ReservationStatus.ACCEPTED)
        self.assertEqual(reservation.product.status, ProductStatus.RESERVED)
        self.assertIsNone(reservation.seller_confirmed_at)
        self.assertFalse(TradeRecord.objects.exists())
        self.assertEqual(Notification.objects.filter(recipient=self.buyer).count(), 1)

    def test_reject_releases_product_and_a_new_buyer_can_reserve(self):
        reservation = self.book()
        response = self.change(self.selling, reservation, "reject")
        self.assertEqual(response.status_code, 302)
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, ReservationStatus.REJECTED)
        self.assertEqual(reservation.cancel_reason, "卖家拒绝了预约。")
        self.assertEqual(reservation.product.status, ProductStatus.PUBLISHED)
        data = self.read_form(self.other_session)
        self.assertEqual(self.other_session.post(self.reserve_url, data).status_code, 302)
        self.assertEqual(Reservation.objects.filter(product=self.product).count(), 2)

    def test_cancel_active_reservations_and_replay_keeps_the_original_uuid(self):
        for accept_first in (False, True):
            with self.subTest(accept_first=accept_first):
                data = self.read_form()
                response = self.buying.post(self.reserve_url, data)
                reservation = Reservation.objects.get(
                    pk=RequestReceipt.objects.get(
                        actor=self.buyer, request_key=str(data["request_key"])
                    ).object_id
                )
                detail = reverse("my_reservation", args=[reservation.pk])
                self.assertRedirects(response, detail)
                if accept_first:
                    self.assertEqual(
                        self.change(self.selling, reservation, "accept").status_code, 302
                    )
                    reservation.refresh_from_db()
                    Reservation.objects.filter(pk=reservation.pk).update(
                        expires_at=timezone.now() - timedelta(seconds=1)
                    )
                self.assertRedirects(self.change(self.buying, reservation, "cancel"), detail)
                self.assertEqual(self.change(self.buying, reservation, "cancel").status_code, 409)
                reservation.refresh_from_db()
                self.assertEqual(reservation.status, ReservationStatus.CANCELLED)
                self.assertEqual(reservation.product.status, ProductStatus.PUBLISHED)
                self.assertRedirects(self.buying.post(self.reserve_url, data), detail)
        self.assertEqual(Reservation.objects.count(), 2)
        self.assertEqual(RequestReceipt.objects.count(), 2)
        self.assertEqual(
            AuditLog.objects.filter(action="reservation_cancel", result="success").count(), 2
        )

    def test_cancel_offline_product_does_not_publish_it(self):
        reservation = self.book()
        Product.objects.filter(pk=self.product.pk).update(status=ProductStatus.OFFLINE)
        self.assertEqual(self.change(self.buying, reservation, "cancel").status_code, 302)
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, ReservationStatus.CANCELLED)
        self.assertEqual(reservation.product.status, ProductStatus.OFFLINE)

    def test_completed_reservation_cannot_be_cancelled(self):
        reservation = self.book()
        Reservation.objects.filter(pk=reservation.pk).update(status=ReservationStatus.COMPLETED)
        Product.objects.filter(pk=self.product.pk).update(status=ProductStatus.SOLD)
        self.assertContains(
            self.change(self.buying, reservation, "cancel"), "这笔预约已结束", status_code=409
        )
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, ReservationStatus.COMPLETED)
        self.assertEqual(reservation.product.status, ProductStatus.SOLD)

    def test_seller_deadline_is_inclusive_and_only_limits_pending_seller_actions(self):
        reservation = self.book()
        endpoint = reverse("my_reservation", args=[reservation.pk])
        with patch("qiwu.market.reservations.timezone.now", return_value=reservation.expires_at):
            for action in ("accept", "reject"):
                response = self.change(self.selling, reservation, action)
                self.assertContains(response, "已超过卖家处理期限", status_code=409)
            self.assertNotContains(self.selling.get(endpoint), "接受预约")
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, ReservationStatus.PENDING)
        with patch(
            "qiwu.market.reservations.timezone.now",
            return_value=reservation.expires_at - timedelta(seconds=1),
        ):
            self.assertEqual(self.change(self.selling, reservation, "accept").status_code, 302)

    def test_receipt_rollback_and_retry_use_the_same_key(self):
        data = self.read_form()
        with (
            patch.object(
                Notification.objects, "create", side_effect=IntegrityError("test write failure")
            ),
            self.assertLogs("qiwu.market.reservation_views", level="ERROR"),
        ):
            response = self.buying.post(self.reserve_url, data)
        self.assertContains(response, "重试原请求", status_code=503)
        self.assertContains(response, str(data["request_key"]), status_code=503)
        self.product.refresh_from_db()
        self.assertEqual(self.product.status, ProductStatus.PUBLISHED)
        self.assertEqual(self.product.version, data["version"])
        self.assertFalse(Reservation.objects.exists())
        self.assertFalse(RequestReceipt.objects.exists())
        self.assertFalse(AuditLog.objects.filter(action="reserve", result="success").exists())
        self.assertTrue(
            AuditLog.objects.filter(action="reserve", reason_code="write_failed").exists()
        )
        self.book(data)

    def test_cancel_rolls_back_status_and_contact_deletion_on_notification_failure(self):
        reservation = self.book()
        self.assertEqual(self.change(self.selling, reservation, "accept").status_code, 302)
        reservation.refresh_from_db()
        contact = HandoverContact.objects.create(
            reservation=reservation, owner=self.buyer, kind="demo", value="local test contact"
        )
        with (
            patch.object(
                Notification.objects, "create", side_effect=IntegrityError("test write failure")
            ),
            self.assertLogs("qiwu.market.reservation_views", level="ERROR"),
        ):
            response = self.change(self.buying, reservation, "cancel")
        self.assertEqual(response.status_code, 503)
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, ReservationStatus.ACCEPTED)
        self.assertEqual(reservation.product.status, ProductStatus.RESERVED)
        self.assertTrue(HandoverContact.objects.filter(pk=contact.pk).exists())
        self.assertTrue(
            AuditLog.objects.filter(
                object_id=reservation.pk, action="reservation_cancel", reason_code="write_failed"
            ).exists()
        )
        self.assertEqual(self.change(self.buying, reservation, "cancel").status_code, 302)
        self.assertFalse(HandoverContact.objects.exists())

    def test_bad_request_key_does_not_reserve(self):
        response = self.buying.post(
            self.reserve_url,
            {"version": self.product.version, "request_key": str(uuid.uuid4())[:-2]},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Reservation.objects.exists())
