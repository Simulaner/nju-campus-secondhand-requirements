import logging
from contextlib import contextmanager
from datetime import timedelta
from hashlib import sha256

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, transaction
from django.db.models import F, Q
from django.http import Http404
from django.utils import timezone

from .models import (
    ACTIVE_RESERVATION_STATUSES,
    Notification,
    Product,
    ProductStatus,
    RequestReceipt,
    Reservation,
    ReservationStatus,
)
from .services import record_action, require_active_actor

SELLER_WINDOW = timedelta(hours=24)
RESERVATION_CREATED = "reservation_created"
RESERVATION_ACTIONS = {
    "accept": ("接受预约", ReservationStatus.ACCEPTED),
    "reject": ("拒绝预约", ReservationStatus.REJECTED),
    "cancel": ("取消预约", ReservationStatus.CANCELLED),
}

logger = logging.getLogger(__name__)


class ReservationConflict(ValidationError):
    pass


@contextmanager
def _record_reservation_mutation(actor, object_type, pk, action):
    try:
        with transaction.atomic():
            yield
    except (ReservationConflict, Http404, PermissionDenied, DatabaseError) as exc:
        if isinstance(exc, ReservationConflict):
            failure = exc.code
        elif isinstance(exc, Http404):
            failure = "not_found"
        elif isinstance(exc, PermissionDenied):
            failure = "not_allowed"
        else:
            failure = "write_failed"
        try:
            record_action(actor, object_type, pk, action, failure=failure)
        except DatabaseError:
            logger.exception(
                "Reservation audit failed: actor=%s object_type=%s object=%s action=%s reason=%s",
                actor.pk,
                object_type,
                pk,
                action,
                failure,
            )
        raise


def participant_reservations(user):
    return Reservation.objects.filter(Q(buyer=user) | Q(seller=user)).select_related(
        "product", "buyer", "seller"
    )


def _notify(reservation, recipient_id, description):
    Notification.objects.create(
        recipient_id=recipient_id,
        event_key=f"reservation:{reservation.pk}:{reservation.version}",
        object_type="reservation",
        object_id=reservation.pk,
        message=f"《{reservation.product_snapshot.get('title') or reservation.product}》{description}",
    )


def _reservation_from_receipt(actor, product_id, receipt, digest):
    if receipt.request_hash != digest or receipt.result_code != RESERVATION_CREATED:
        raise ReservationConflict(
            "此请求已用于另一笔操作，请重新打开商品详情。", code="request_mismatch"
        )
    if not Reservation.objects.filter(
        pk=receipt.object_id, buyer=actor, product_id=product_id
    ).exists():
        raise ReservationConflict("无法找到原预约，请查看我的记录。", code="missing_receipt_target")
    return receipt.object_id


def reserve_product(*, actor, pk, request_key, version):
    key = str(request_key)
    digest = sha256(f"reserve:{pk}:{version}".encode("ascii")).hexdigest()
    with _record_reservation_mutation(actor, "product", pk, "reserve"):
        require_active_actor(actor, staff=False)
        receipt = RequestReceipt.objects.filter(actor=actor, request_key=key).first()
        if receipt is not None:
            return _reservation_from_receipt(actor, pk, receipt, digest)

        product = Product.objects.select_related("seller", "category").filter(pk=pk).first()
        if product is None or product.status not in (
            ProductStatus.PUBLISHED,
            ProductStatus.RESERVED,
        ):
            raise ReservationConflict("该商品当前不可预约。", code="unavailable")
        if product.seller_id == actor.pk:
            raise PermissionDenied("不能预约自己发布的商品。")
        if product.status == ProductStatus.RESERVED:
            raise ReservationConflict("这件商品已有有效预约。", code="already_reserved")
        if product.version != version:
            raise ReservationConflict("商品信息已更新，请重新查看后再预约。", code="stale_product")
        if not product.seller.is_active or product.price_fen is None:
            raise ReservationConflict("该商品当前不可预约。", code="unavailable")
        if product.reservations.filter(status__in=ACTIVE_RESERVATION_STATUSES).exists():
            raise ReservationConflict("这件商品已有有效预约。", code="already_reserved")

        now = timezone.now()
        updated = Product.objects.filter(
            pk=pk, version=version, status=ProductStatus.PUBLISHED
        ).update(status=ProductStatus.RESERVED, version=F("version") + 1)
        if updated != 1:
            raise ReservationConflict("商品状态已更新，请查看最新详情。", code="stale_product")
        reservation = Reservation.objects.create(
            product=product,
            buyer=actor,
            seller=product.seller,
            created_at=now,
            expires_at=now + SELLER_WINDOW,
            product_snapshot={
                "title": product.title,
                "price_fen": product.price_fen,
                "category": product.category.name if product.category_id else "",
                "condition": product.condition,
                "meeting_place": product.meeting_place,
            },
        )
        RequestReceipt.objects.create(
            actor=actor,
            request_key=key,
            request_hash=digest,
            object_id=reservation.pk,
            result_code=RESERVATION_CREATED,
        )
        _notify(reservation, reservation.seller_id, "收到一笔预约，请在 24 小时内处理。")
        record_action(actor, "reservation", reservation.pk, "reserve")
        return reservation.pk


def _check_transition(reservation, actor, action, version):
    if action == "cancel":
        allowed = reservation.buyer_id == actor.pk
    else:
        allowed = reservation.seller_id == actor.pk
    if not allowed:
        raise PermissionDenied
    if reservation.version != version:
        raise ReservationConflict("预约状态已更新，请查看最新详情。", code="stale_reservation")
    if action == "cancel":
        if not reservation.is_active:
            raise ReservationConflict("这笔预约已结束。", code="not_active")
        if reservation.product.status not in (ProductStatus.RESERVED, ProductStatus.OFFLINE):
            raise ReservationConflict("商品当前状态无法取消预约。", code="product_changed")
        return
    if (
        reservation.status != ReservationStatus.PENDING
        or reservation.product.status != ProductStatus.RESERVED
    ):
        raise ReservationConflict("这笔预约当前无法接受或拒绝。", code="not_pending")
    if reservation.pending_expired(timezone.now()):
        raise ReservationConflict("该预约已超过卖家处理期限。", code="seller_deadline")
    if action == "accept" and not reservation.buyer.is_active:
        raise ReservationConflict("买家账号当前不可用。", code="buyer_inactive")


def release_reserved_product(product):
    if product.status != ProductStatus.RESERVED:
        return
    updated = Product.objects.filter(
        pk=product.pk, version=product.version, status=ProductStatus.RESERVED
    ).update(status=ProductStatus.PUBLISHED, version=F("version") + 1)
    if updated != 1:
        raise ReservationConflict("商品状态已更新，请查看最新详情。", code="stale_product")


def change_reservation(*, actor, pk, action, version):
    label, next_status = RESERVATION_ACTIONS[action]
    audit_action = f"reservation_{action}"
    with _record_reservation_mutation(actor, "reservation", pk, audit_action):
        require_active_actor(actor, staff=False)
        reservation = participant_reservations(actor).filter(pk=pk).first()
        if reservation is None:
            raise Http404
        _check_transition(reservation, actor, action, version)

        if action == "cancel":
            reason = "买家主动取消预约。"
            recipient_id, description = reservation.seller_id, "的预约已由买家取消。"
        elif action == "reject":
            reason = "卖家拒绝了预约。"
            recipient_id, description = reservation.buyer_id, "的预约已被卖家拒绝。"
        else:
            reason = ""
            recipient_id, description = reservation.buyer_id, "的预约已获卖家接受，请准备线下面交。"
        updated = Reservation.objects.filter(
            pk=pk, version=version, status=reservation.status
        ).update(status=next_status, version=F("version") + 1, cancel_reason=reason)
        if updated != 1:
            raise ReservationConflict("预约状态已更新，请查看最新详情。", code="stale_reservation")
        reservation.version += 1
        if next_status != ReservationStatus.ACCEPTED:
            release_reserved_product(reservation.product)
            reservation.contacts.all().delete()
        _notify(reservation, recipient_id, description)
        record_action(actor, "reservation", pk, audit_action)
        return label
