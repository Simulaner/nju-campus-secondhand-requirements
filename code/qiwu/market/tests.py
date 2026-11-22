from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    Product,
    ProductImage,
    ProductStatus,
    Reservation,
    ReservationStatus,
    ReviewDecision,
    ReviewRecord,
    TradeRecord,
)

User = get_user_model()


class MarketDataTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.seller = User.objects.create_user("seller@nju.edu.cn", display_name="卖家")
        cls.buyer = User.objects.create_user("buyer@nju.edu.cn", display_name="买家")
        cls.product = Product.objects.create(seller=cls.seller)

    def reservation(self, **overrides):
        values = {
            "product": self.product,
            "seller": self.seller,
            "buyer": self.buyer,
            "expires_at": timezone.now() + timedelta(days=1),
        }
        return Reservation.objects.create(**{**values, **overrides})

    def test_draft_may_be_incomplete_and_keeps_its_owner(self):
        self.assertIsNone(self.product.category_id)
        self.assertIsNone(self.product.price_fen)
        self.assertEqual(self.product.status, ProductStatus.DRAFT)
        self.assertEqual(self.product.seller, self.seller)
        with self.assertRaises(ProtectedError):
            self.seller.delete()

    def test_email_uniqueness_ignores_case(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create(email="SELLER@NJU.EDU.CN", display_name="重复")

    def test_only_one_active_reservation_and_history_is_retained(self):
        current = self.reservation(status=ReservationStatus.ACCEPTED)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.reservation()
        current.status = ReservationStatus.CANCELLED
        current.save(update_fields=["status"])
        self.reservation()
        self.assertEqual(self.product.reservations.count(), 2)

    def test_parties_must_be_distinct_and_trade_is_unique(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.reservation(buyer=self.seller)
        reservation = self.reservation(status=ReservationStatus.COMPLETED)
        trade = TradeRecord.objects.create(reservation=reservation, agreed_price_fen=1000)
        self.assertEqual(trade.reservation.product, self.product)
        with self.assertRaises(IntegrityError), transaction.atomic():
            TradeRecord.objects.create(reservation=reservation, agreed_price_fen=1000)

    def test_reservation_seller_matches_product_owner(self):
        outsider = User.objects.create_user("outsider@nju.edu.cn", display_name="其他人")
        with self.assertRaisesMessage(ValidationError, "商品的所有者"):
            self.reservation(seller=outsider)
        self.assertFalse(Reservation.objects.exists())

    def test_review_version_and_rejection_reason(self):
        admin = User.objects.create_superuser(
            "admin@nju.edu.cn", "CourseAdmin42", display_name="管理员"
        )
        values = {"product": self.product, "product_version": 1, "reviewer": admin}
        with self.assertRaises(IntegrityError), transaction.atomic():
            ReviewRecord.objects.create(**values, decision=ReviewDecision.REJECTED)
        ReviewRecord.objects.create(**values, decision=ReviewDecision.REJECTED, reason="缺少说明")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ReviewRecord.objects.create(**values, decision=ReviewDecision.APPROVED)

    def test_image_slot_and_price_constraints(self):
        image = {
            "product": self.product,
            "storage_key": "sample.jpg",
            "mime_type": "image/jpeg",
            "size_bytes": 100,
        }
        ProductImage.objects.create(**image, position=1)
        for position in (1, 10):
            with (
                self.subTest(position=position),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                ProductImage.objects.create(**image, position=position)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Product.objects.create(seller=self.seller, price_fen=0)


class AccessTests(TestCase):
    password = "CampusAccess42"

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("owner@nju.edu.cn", cls.password, display_name="卖家")
        cls.buyer = User.objects.create_user("buyer@nju.edu.cn", cls.password, display_name="买家")
        cls.other = User.objects.create_user(
            "other@nju.edu.cn", cls.password, display_name="其他同学"
        )
        cls.admin = User.objects.create_superuser(
            "admin@nju.edu.cn", cls.password, display_name="管理员"
        )
        cls.draft = Product.objects.create(seller=cls.owner, title="未公开的教材")
        cls.offer = Product.objects.create(
            seller=cls.owner, title="台灯", status=ProductStatus.RESERVED
        )
        cls.reservation = Reservation.objects.create(
            seller=cls.owner,
            buyer=cls.buyer,
            product=cls.offer,
            expires_at=timezone.now() + timedelta(days=1),
        )

    def test_two_sessions_have_independent_object_access(self):
        owner_browser = Client()
        buyer_browser = Client()
        for browser, user in [(owner_browser, self.owner), (buyer_browser, self.buyer)]:
            response = browser.post(
                reverse("login"), {"username": user.email, "password": self.password}
            )
            self.assertRedirects(response, reverse("home"))
        draft_url = reverse("my_product", args=[self.draft.pk])
        self.assertContains(owner_browser.get(draft_url), self.draft.title)
        self.assertEqual(buyer_browser.get(draft_url).status_code, 404)
        self.assertNotContains(buyer_browser.get(reverse("my_records")), self.draft.title)
        detail = reverse("my_reservation", args=[self.reservation.pk])
        for browser in (owner_browser, buyer_browser):
            self.assertContains(browser.get(detail), "台灯")
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(detail).status_code, 404)
        self.assertNotContains(self.client.get(reverse("my_records")), "台灯")

    def test_review_queue_requires_admin_and_only_lists_pending_items(self):
        url = reverse("review_queue")
        self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(url).status_code, 403)
        pending = Product.objects.create(
            seller=self.owner, title="待审相机", status=ProductStatus.PENDING
        )
        self.client.force_login(self.admin)
        response = self.client.get(url)
        self.assertContains(response, pending.title)
        self.assertNotContains(response, self.draft.title)
        self.assertNotContains(response, self.owner.email)

    def test_existing_session_loses_access_when_disabled(self):
        self.client.force_login(self.owner)
        self.owner.is_active = False
        self.owner.save(update_fields=["is_active"])
        url = reverse("my_records")
        self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")

    def test_revoked_session_version_requires_a_new_login(self):
        self.client.force_login(self.buyer)
        self.buyer.session_version += 1
        self.buyer.save(update_fields=["session_version"])
        url = reverse("my_records")
        self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")
