from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .models import (
    AuditLog,
    Category,
    Notification,
    Product,
    ProductImage,
    ProductImageContent,
    ProductStatus,
    ReviewDecision,
    ReviewRecord,
)

User = get_user_model()


def upload(kind="PNG", color="purple"):
    buffer = BytesIO()
    Image.new("RGB", (24, 18), color).save(buffer, format=kind)
    return SimpleUploadedFile("upload.bin", buffer.getvalue())


class ProductWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.seller = User.objects.create_user("seller@smail.nju.edu.cn", display_name="卖家")
        cls.buyer = User.objects.create_user("buyer@nju.edu.cn", display_name="买家")
        cls.admin = User.objects.create_superuser(
            "admin@nju.edu.cn", "ReviewTest42", display_name="管理员"
        )
        cls.category = Category.objects.get(name="数码电子")

    def setUp(self):
        self.selling = Client()
        self.selling.force_login(self.seller)
        self.buying = Client()
        self.buying.force_login(self.buyer)
        self.moderating = Client()
        self.moderating.force_login(self.admin)
        self.new_url = reverse("new_product")

    def fields(self, **overrides):
        return {
            "title": "闲置相机",
            "category": self.category.pk,
            "price": "42.75",
            "condition": 8,
            "description": "功能正常，附存储卡。",
            **overrides,
        }

    def new_data(self, **fields):
        token = self.selling.get(self.new_url).context["token"]
        return {"draft_token": token, "version": 0, "action": "save", **fields}

    def pending(self):
        data = self.new_data(**self.fields(action="submit", images=[upload()]))
        response = self.selling.post(self.new_url, data)
        product = Product.objects.get(seller=self.seller)
        self.assertRedirects(response, reverse("my_product", args=[product.pk]))
        self.assertEqual(product.status, ProductStatus.PENDING)
        return product

    def approve(self, product):
        response = self.moderating.post(
            reverse("moderate", args=[product.pk]),
            {"decision": ReviewDecision.APPROVED, "version": product.version},
        )
        self.assertRedirects(response, reverse("review_queue"))
        product.refresh_from_db()

    def test_incomplete_draft_is_private_and_can_be_edited_in_another_session(self):
        data = self.new_data(description="晚点补价格")
        response = self.selling.post(self.new_url, data)
        product = Product.objects.get()
        detail = reverse("my_product", args=[product.pk])
        edit = reverse("edit_product", args=[product.pk])
        self.assertRedirects(response, detail)
        owner = Client()
        owner.force_login(self.seller)
        self.assertContains(owner.get(edit), "晚点补价格")
        self.assertEqual(self.buying.get(detail).status_code, 404)
        self.assertEqual(self.buying.post(edit, {"version": product.version}).status_code, 404)
        self.assertEqual(product.status, ProductStatus.DRAFT)
        self.assertIsNone(product.price_fen)

    def test_new_form_replay_cannot_create_duplicate_drafts(self):
        data = self.new_data(title="同一表单")
        self.assertEqual(self.buying.post(self.new_url, data).status_code, 400)
        self.assertEqual(self.selling.post(self.new_url, data).status_code, 302)
        self.assertEqual(self.selling.post(self.new_url, data).status_code, 409)
        self.assertEqual(Product.objects.count(), 1)

    def test_submission_rejects_missing_fields_and_image(self):
        response = self.selling.post(self.new_url, self.new_data(action="submit"))
        self.assertEqual(
            set(response.context["form"].errors),
            {
                "title",
                "category",
                "price",
                "condition",
                "description",
            },
        )
        response = self.selling.post(self.new_url, self.new_data(**self.fields(action="submit")))
        self.assertContains(response, "提交审核至少需要一张图片")
        self.assertFalse(Product.objects.exists())

    def test_invalid_price_condition_and_image_contents_are_rejected(self):
        response = self.selling.post(
            self.new_url,
            self.new_data(
                **self.fields(
                    price="0",
                    condition="10",
                    images=[
                        SimpleUploadedFile("bad.jpg", b"not image data", content_type="image/jpeg")
                    ],
                )
            ),
        )
        self.assertEqual(set(response.context["form"].errors), {"price", "condition", "images"})
        self.assertFalse(Product.objects.exists())

    def test_image_limits_and_supported_formats(self):
        for files, error in [
            ([upload() for _ in range(10)], "最多保留 9 张"),
            ([SimpleUploadedFile("large.png", b"0" * (5 * 1024 * 1024 + 1))], "最大为 5 MB"),
            ([upload("GIF")], "JPG、PNG 或 WebP"),
        ]:
            with self.subTest(error=error):
                response = self.selling.post(self.new_url, self.new_data(images=files))
                self.assertContains(response, error)
        response = self.selling.post(
            self.new_url, self.new_data(images=[upload("JPEG"), upload("PNG"), upload("WEBP")])
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            list(ProductImage.objects.values_list("mime_type", flat=True)),
            ["image/jpeg", "image/png", "image/webp"],
        )

    def test_legacy_image_metadata_does_not_count_as_an_uploaded_file(self):
        product = Product.objects.create(seller=self.seller)
        img = ProductImage.objects.create(
            product=product,
            position=1,
            storage_key="old.jpg",
            mime_type="image/jpeg",
            size_bytes=10,
        )
        response = self.selling.post(
            reverse("edit_product", args=[product.pk]),
            self.fields(version=product.version, action="submit"),
        )
        self.assertContains(response, "至少需要一张图片")
        self.assertEqual(self.selling.get(reverse("product_image", args=[img.pk])).status_code, 404)
        product.refresh_from_db()
        self.assertEqual(product.status, ProductStatus.DRAFT)

    def test_adding_removing_and_reordering_images_is_atomic(self):
        data = self.new_data(
            images=[upload(color="red"), upload(color="green"), upload(color="blue")]
        )
        self.assertEqual(self.selling.post(self.new_url, data).status_code, 302)
        product = Product.objects.get()
        first, second, third = product.images.all()
        endpoint = reverse("edit_product", args=[product.pk])
        self.assertContains(self.selling.get(endpoint), "移除图片 2")
        with patch.object(
            ProductImageContent.objects, "create", side_effect=IntegrityError("disk write")
        ):
            with self.assertRaises(IntegrityError):
                self.selling.post(
                    endpoint,
                    {
                        "action": "save",
                        "version": product.version,
                        "title": "不可写入",
                        "remove_images": [first.pk],
                        "images": [upload()],
                    },
                )
        self.assertEqual(list(product.images.all()), [first, second, third])
        product.refresh_from_db()
        self.assertEqual(product.title, "")
        self.assertEqual(
            self.selling.post(
                endpoint,
                {
                    "action": "save",
                    "version": product.version,
                    "remove_images": [first.pk],
                    "images": [upload()],
                },
            ).status_code,
            302,
        )
        remaining = list(product.images.all())
        self.assertEqual(remaining[:2], [second, third])
        self.assertEqual([img.position for img in remaining], [1, 2, 3])
        self.assertFalse(ProductImageContent.objects.filter(pk=first.pk).exists())

    def test_pending_product_and_its_images_are_private_until_approval(self):
        product = self.pending()
        detail = reverse("public_product", args=[product.pk])
        image = reverse("product_image", args=[product.images.get().pk])
        edit = reverse("edit_product", args=[product.pk])
        for page in [detail, image]:
            self.assertEqual(self.buying.get(page).status_code, 404)
        self.assertEqual(
            self.selling.post(edit, {"action": "submit", "version": product.version}).status_code,
            409,
        )
        self.assertEqual(self.moderating.get(image).status_code, 200)
        self.approve(product)
        self.assertContains(self.buying.get(detail), "¥42.75")
        self.assertNotContains(self.buying.get(detail), self.seller.email)
        response = self.buying.get(image)
        self.assertEqual(response.headers["Content-Type"], "image/png")
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(ReviewRecord.objects.count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.seller).count(), 1)

    def test_rejection_reason_and_fresh_review_version_after_resubmission(self):
        product = self.pending()
        endpoint = reverse("moderate", args=[product.pk])
        data = {"decision": ReviewDecision.REJECTED, "version": product.version}
        response = self.moderating.post(endpoint, {**data, "reason": "   "})
        self.assertContains(response, "请填写驳回原因")
        self.assertFalse(ReviewRecord.objects.exists())
        self.assertRedirects(
            self.moderating.post(endpoint, {**data, "reason": "请补充型号"}),
            reverse("review_queue"),
        )
        self.assertContains(
            self.selling.get(reverse("my_product", args=[product.pk])), "请补充型号"
        )
        self.assertEqual(
            self.buying.get(reverse("public_product", args=[product.pk])).status_code, 404
        )
        product.refresh_from_db()
        self.assertEqual(product.status, ProductStatus.REJECTED)
        response = self.selling.post(
            reverse("edit_product", args=[product.pk]),
            self.fields(
                action="submit",
                version=product.version,
                description="已补充型号",
            ),
        )
        self.assertEqual(response.status_code, 302)
        product.refresh_from_db()
        self.assertEqual(
            self.moderating.post(endpoint, {**data, "reason": "旧表单"}).status_code, 409
        )
        self.approve(product)
        self.assertEqual(product.status, ProductStatus.PUBLISHED)
        self.assertEqual(ReviewRecord.objects.count(), 2)

    def test_real_change_to_published_product_suspends_public_access(self):
        product = self.pending()
        self.approve(product)
        edit = reverse("edit_product", args=[product.pk])
        original = self.fields(version=product.version, action="save")
        self.assertEqual(self.selling.post(edit, original).status_code, 302)
        product.refresh_from_db()
        self.assertEqual(product.status, ProductStatus.PUBLISHED)
        self.assertEqual(product.version, original["version"])
        self.assertEqual(self.selling.post(edit, {**original, "price": "43.50"}).status_code, 302)
        product.refresh_from_db()
        self.assertEqual(product.status, ProductStatus.PENDING)
        image = reverse("product_image", args=[product.images.get().pk])
        detail = reverse("public_product", args=[product.pk])
        self.assertEqual(self.buying.get(image).status_code, 404)
        self.assertEqual(self.buying.get(detail).status_code, 404)
        search = self.buying.get(reverse("home"), {"q": product.title})
        self.assertFalse(list(search.context["page"]))
        self.approve(product)
        self.assertContains(self.buying.get(detail), "¥43.50")
        self.assertEqual(self.buying.get(image).status_code, 200)

    def test_reserved_and_sold_products_cannot_be_edited(self):
        product = self.pending()
        self.approve(product)
        endpoint = reverse("edit_product", args=[product.pk])
        page = reverse("public_product", args=[product.pk])
        for state in (ProductStatus.RESERVED, ProductStatus.SOLD):
            Product.objects.filter(pk=product.pk).update(status=state, version=product.version + 1)
            self.assertEqual(
                self.selling.post(endpoint, {"version": product.version}).status_code, 409
            )
            if state == ProductStatus.RESERVED:
                self.assertContains(self.buying.get(page), "已有有效预约")
            else:
                self.assertEqual(self.buying.get(page).status_code, 404)

    def test_review_permissions_and_duplicate_requests_are_audited(self):
        product = self.pending()
        endpoint = reverse("moderate", args=[product.pk])
        data = {"decision": ReviewDecision.APPROVED, "version": product.version}
        self.assertEqual(self.selling.post(endpoint, data).status_code, 403)
        self.assertFalse(ReviewRecord.objects.exists())
        self.approve(product)
        self.assertEqual(self.moderating.post(endpoint, data).status_code, 409)
        events = AuditLog.objects.filter(object_id=product.pk)
        self.assertTrue(
            events.filter(action="review_approved", result="success", actor=self.admin).exists()
        )
        self.assertEqual(
            set(events.filter(result="failure").values_list("reason_code", flat=True)),
            {"not_staff", "not_pending"},
        )

    def test_review_rolls_back_if_notification_fails_and_writes_a_failure_audit(self):
        product = self.pending()
        with patch.object(
            Notification.objects, "create", side_effect=IntegrityError("write failed")
        ):
            with self.assertLogs("qiwu.market.views", level="ERROR"):
                response = self.moderating.post(
                    reverse("moderate", args=[product.pk]),
                    {"decision": ReviewDecision.APPROVED, "version": product.version},
                )
        self.assertContains(response, "审核未保存", status_code=503)
        product.refresh_from_db()
        self.assertEqual(product.status, ProductStatus.PENDING)
        self.assertFalse(ReviewRecord.objects.exists())
        self.assertTrue(
            AuditLog.objects.filter(
                object_id=product.pk, result="failure", reason_code="write_failed"
            ).exists()
        )


class PublicCatalogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.seller = User.objects.create_user("seller@nju.edu.cn", display_name="卖家")
        cls.category = Category.objects.get(name="数码电子")
        cls.others = Category.objects.get(name="生活用品")

    def item(self, title, **overrides):
        data = {
            "title": title,
            "seller": self.seller,
            "category": self.category,
            "condition": 8,
            "price_fen": 12000,
            "status": ProductStatus.PUBLISHED,
            "published_at": timezone.now(),
        }
        return Product.objects.create(**{**data, **overrides})

    def test_combined_filters_and_visibility(self):
        match = self.item("轻便相机")
        self.item("已预约相机", status=ProductStatus.RESERVED)
        self.item("高价相机", price_fen=16000)
        self.item("旧相机", condition=5)
        self.item("其他分类相机", category=self.others)
        self.item("旧平板")
        for state in (
            ProductStatus.DRAFT,
            ProductStatus.PENDING,
            ProductStatus.REJECTED,
            ProductStatus.OFFLINE,
            ProductStatus.SOLD,
        ):
            private = self.item(f"私有相机 {state}", status=state)
            self.assertEqual(
                self.client.get(reverse("public_product", args=[private.pk])).status_code, 404
            )
        response = self.client.get(
            reverse("home"),
            {
                "q": "相机",
                "category": self.category.pk,
                "min_price": 120,
                "max_price": "159.99",
                "condition": 8,
                "status": ProductStatus.PUBLISHED,
            },
        )
        self.assertEqual(list(response.context["page"]), [match])
        self.assertContains(response, reverse("public_product", args=[match.pk]))
        self.assertContains(self.client.get(reverse("home"), {"q": "不存在"}), "没有符合条件的商品")
        invalid = self.client.get(reverse("home"), {"min_price": "10", "max_price": "1"})
        self.assertContains(invalid, "最高价不能低于最低价")
        self.assertFalse(list(invalid.context["page"]))

    def test_four_sort_orders_and_filter_preserving_pagination(self):
        cheap = self.item("课本甲", price_fen=1000)
        newer = self.item(
            "课本乙", price_fen=2000, published_at=timezone.now() + timedelta(hours=1)
        )
        for order, expected in [
            ("newest", [newer, cheap]),
            ("oldest", [cheap, newer]),
            ("price_down", [newer, cheap]),
            ("price_up", [cheap, newer]),
        ]:
            self.assertEqual(
                list(self.client.get(reverse("home"), {"sort": order}).context["page"]), expected
            )
        for n in range(23):
            self.item(f"课本 {n}")
        first = self.client.get(reverse("home"), {"q": "课本", "sort": "price_up"})
        self.assertEqual(len(first.context["page"]), 24)
        self.assertContains(first, "sort=price_up&amp;page=2")
        second = self.client.get(reverse("home"), {"q": "课本", "sort": "price_up", "page": 2})
        self.assertEqual(len(second.context["page"]), 1)

    def test_seller_state_filter_does_not_include_other_sellers(self):
        other = User.objects.create_user("other@nju.edu.cn", display_name="另一卖家")
        own = self.item("自己草稿", status=ProductStatus.DRAFT)
        self.item("他人草稿", status=ProductStatus.DRAFT, seller=other)
        self.item("自己公开")
        self.client.force_login(self.seller)
        response = self.client.get(reverse("my_records"), {"status": ProductStatus.DRAFT})
        self.assertEqual(list(response.context["products"]), [own])
