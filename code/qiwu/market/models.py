import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


class Entity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class Category(Entity):
    name = models.CharField(max_length=40, unique=True)
    enabled = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class ProductStatus(models.TextChoices):
    DRAFT = "draft", "草稿"
    PENDING = "pending", "待审核"
    REJECTED = "rejected", "已驳回"
    PUBLISHED = "published", "已发布"
    RESERVED = "reserved", "已预约"
    SOLD = "sold", "已售出"
    OFFLINE = "offline", "已下架"


PUBLIC_PRODUCT_STATUSES = (ProductStatus.PUBLISHED, ProductStatus.RESERVED)


class Product(Entity):
    seller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, null=True, blank=True)
    title = models.CharField(max_length=120, blank=True)
    description = models.TextField(blank=True)
    price_fen = models.PositiveIntegerField(null=True, blank=True)
    condition = models.PositiveSmallIntegerField(null=True, blank=True)
    meeting_place = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=12, choices=ProductStatus, default=ProductStatus.DRAFT)
    version = models.PositiveIntegerField(default=1)
    relist_blocked = models.BooleanField(default=False)
    off_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=ProductStatus.values), name="product_status_valid"
            ),
            models.CheckConstraint(
                condition=Q(price_fen__gt=0) | Q(price_fen=None), name="product_price_positive"
            ),
            models.CheckConstraint(
                condition=Q(condition__range=(1, 9)) | Q(condition=None),
                name="product_condition_valid",
            ),
        ]
        indexes = [models.Index(fields=["status", "published_at"])]

    def __str__(self):
        return self.title or "未命名草稿"

    @property
    def price_yuan(self):
        if self.price_fen is not None:
            return Decimal(self.price_fen).scaleb(-2)

    @property
    def editable(self):
        return self.status in (
            ProductStatus.DRAFT,
            ProductStatus.REJECTED,
            ProductStatus.PUBLISHED,
        ) or (self.status == ProductStatus.OFFLINE and not self.relist_blocked)


class ProductImage(Entity):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    storage_key = models.CharField(max_length=255)
    position = models.PositiveSmallIntegerField()
    mime_type = models.CharField(max_length=40)
    size_bytes = models.PositiveIntegerField()

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "position"], name="product_image_position_unique"
            ),
            models.CheckConstraint(
                condition=Q(position__range=(1, 9)), name="product_image_slot_valid"
            ),
        ]


class ProductImageContent(models.Model):
    # Keep image bytes out of regular list queries and commit them with the product.
    image = models.OneToOneField(
        ProductImage, primary_key=True, on_delete=models.CASCADE, related_name="content"
    )
    data = models.BinaryField()


class ReviewDecision(models.TextChoices):
    APPROVED = "approved", "通过"
    REJECTED = "rejected", "驳回"


class ReviewRecord(Entity):
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="reviews")
    product_version = models.PositiveIntegerField()
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    decision = models.CharField(max_length=8, choices=ReviewDecision)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "product_version"], name="review_per_version"
            ),
            models.CheckConstraint(
                condition=Q(decision__in=ReviewDecision.values), name="review_decision_valid"
            ),
            models.CheckConstraint(
                condition=~Q(decision=ReviewDecision.REJECTED) | ~Q(reason=""),
                name="review_rejection_reason",
            ),
        ]


class ReservationStatus(models.TextChoices):
    PENDING = "pending", "待卖家确认"
    ACCEPTED = "accepted", "已确认待交接"
    REJECTED = "rejected", "已拒绝"
    CANCELLED = "cancelled", "已取消"
    EXPIRED = "expired", "已超时"
    COMPLETED = "completed", "已完成"


ACTIVE_RESERVATION_STATUSES = (ReservationStatus.PENDING, ReservationStatus.ACCEPTED)


class Reservation(Entity):
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="reservations")
    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="purchases"
    )
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sales"
    )
    status = models.CharField(
        max_length=12, choices=ReservationStatus, default=ReservationStatus.PENDING
    )
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    buyer_confirmed_at = models.DateTimeField(null=True, blank=True)
    seller_confirmed_at = models.DateTimeField(null=True, blank=True)
    product_snapshot = models.JSONField(default=dict)
    cancel_reason = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=ReservationStatus.values), name="reservation_status_valid"
            ),
            models.CheckConstraint(
                condition=~Q(buyer=F("seller")), name="reservation_distinct_parties"
            ),
            models.UniqueConstraint(
                fields=["product"],
                condition=Q(status__in=list(ACTIVE_RESERVATION_STATUSES)),
                name="one_active_reservation_per_product",
            ),
        ]
        indexes = [models.Index(fields=["status", "expires_at"])]

    @property
    def is_active(self):
        return self.status in ACTIVE_RESERVATION_STATUSES

    @property
    def price_yuan(self):
        price_fen = self.product_snapshot.get("price_fen")
        if price_fen is not None:
            return Decimal(price_fen).scaleb(-2)

    def pending_expired(self, now):
        return self.status == ReservationStatus.PENDING and now >= self.expires_at

    def save(self, *args, **kwargs):
        if self.product_id and self.seller_id != self.product.seller_id:
            raise ValidationError({"seller": "预约的卖家必须是该商品的所有者。"})
        return super().save(*args, **kwargs)


CONTACT_KINDS = (
    ("phone", "手机号"),
    ("wechat", "微信号"),
    ("email", "邮箱"),
    ("other", "其他联系方式"),
)


class HandoverContact(Entity):
    reservation = models.ForeignKey(Reservation, on_delete=models.CASCADE, related_name="contacts")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    kind = models.CharField(max_length=24)
    value = models.CharField(max_length=160)
    consent_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["reservation", "owner"], name="one_contact_per_party"),
        ]

    @property
    def kind_label(self):
        return dict(CONTACT_KINDS).get(self.kind, "联系方式")


class TradeRecord(Entity):
    reservation = models.OneToOneField(Reservation, on_delete=models.PROTECT, related_name="trade")
    completed_at = models.DateTimeField(default=timezone.now)
    agreed_price_fen = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(agreed_price_fen__gt=0), name="trade_price_positive"
            ),
        ]

    @property
    def agreed_price_yuan(self):
        return Decimal(self.agreed_price_fen).scaleb(-2)


class RequestReceipt(Entity):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    request_key = models.CharField(max_length=80)
    request_hash = models.CharField(max_length=64)
    object_id = models.UUIDField()
    result_code = models.CharField(max_length=48)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["actor", "request_key"], name="request_key_per_actor"),
        ]


class Notification(Entity):
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    event_key = models.CharField(max_length=160)
    object_type = models.CharField(max_length=40)
    object_id = models.UUIDField()
    message = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["recipient", "event_key"], name="one_notice_per_event_recipient"
            ),
        ]


class AuditLog(Entity):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    object_type = models.CharField(max_length=40)
    object_id = models.UUIDField()
    action = models.CharField(max_length=48)
    result = models.CharField(max_length=32)
    reason_code = models.CharField(max_length=48, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    correlation_id = models.UUIDField(default=uuid.uuid4)
