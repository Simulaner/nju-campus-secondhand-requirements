import logging
from datetime import timedelta
from hashlib import sha256

from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, transaction
from django.db.models import F, Q
from django.http import Http404
from django.utils import timezone

from .events import WorkflowConflict, audited_transaction, notify, record_action
from .models import (
    ACTIVE_RESERVATION_STATUSES,
    Product,
    ProductStatus,
    RequestReceipt,
    Reservation,
    ReservationStatus,
    TradeRecord,
)
from .services import require_active_actor

SELLER_WINDOW = timedelta(hours=24)
RESERVATION_CREATED = "reservation_created"
RESERVATION_ACTIONS = {
    "accept": ("接受预约", ReservationStatus.ACCEPTED),
    "reject": ("拒绝预约", ReservationStatus.REJECTED),
    "cancel": ("取消预约", ReservationStatus.CANCELLED),
}

logger = logging.getLogger(__name__)


class ReservationConflict(WorkflowConflict):
    pass


def participant_reservations(user):
    return Reservation.objects.filter(Q(buyer=user) | Q(seller=user)).select_related(
        "product", "buyer", "seller"
    )


def _notify(reservation, recipient_id, description):
    notify(
        recipient_id,
        "reservation",
        reservation.pk,
        f"reservation:{reservation.pk}:{reservation.version}",
        f"《{reservation.product_snapshot.get('title') or reservation.product}》{description}",
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
    with audited_transaction(actor, "product", pk, "reserve"):
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
        _set_product_status(product, ProductStatus.RESERVED)
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
        if reservation.pending_expired(timezone.now()):
            raise ReservationConflict("该预约已到期。", code="seller_deadline")
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


def _set_product_status(product, status, **fields):
    updated = Product.objects.filter(
        pk=product.pk, version=product.version, status=product.status
    ).update(status=status, version=F("version") + 1, **fields)
    if updated != 1:
        raise ReservationConflict("商品状态已更新，请查看最新详情。", code="stale_product")
    product.status = status
    product.version += 1


def _release_reserved_product(product):
    if product.status == ProductStatus.RESERVED:
        _set_product_status(product, ProductStatus.PUBLISHED)


def _get_participant_reservation(actor, pk):
    require_active_actor(actor, staff=False)
    reservation = participant_reservations(actor).filter(pk=pk).first()
    if reservation is None:
        raise Http404
    return reservation


def _update_reservation(reservation, **fields):
    updated = Reservation.objects.filter(
        pk=reservation.pk, version=reservation.version, status=reservation.status
    ).update(version=F("version") + 1, **fields)
    if updated != 1:
        raise ReservationConflict("预约状态已更新，请查看最新详情。", code="stale_reservation")
    for name, value in fields.items():
        setattr(reservation, name, value)
    reservation.version += 1


def _end_reservation(reservation, status, reason):
    _update_reservation(reservation, status=status, cancel_reason=reason)
    reservation.contacts.all().delete()


def change_reservation(*, actor, pk, action, version):
    label, next_status = RESERVATION_ACTIONS[action]
    audit_action = f"reservation_{action}"
    with audited_transaction(actor, "reservation", pk, audit_action):
        reservation = _get_participant_reservation(actor, pk)
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
        if next_status == ReservationStatus.ACCEPTED:
            _update_reservation(reservation, status=next_status)
        else:
            _end_reservation(reservation, next_status, reason)
            _release_reserved_product(reservation.product)
        _notify(reservation, recipient_id, description)
        record_action(actor, "reservation", pk, audit_action)
        return label


def handover_is_open(reservation):
    return (
        reservation.status == ReservationStatus.ACCEPTED
        and reservation.product.status == ProductStatus.RESERVED
        and reservation.buyer.is_active
        and reservation.seller.is_active
    )


def _require_handover(reservation):
    if not handover_is_open(reservation):
        raise ReservationConflict("这笔预约当前不能办理交接。", code="handover_closed")


@transaction.atomic
def save_handover_contact(*, actor, pk, details):
    reservation = _get_participant_reservation(actor, pk)
    _require_handover(reservation)
    if details is None:
        reservation.contacts.filter(owner=actor).delete()
    else:
        reservation.contacts.update_or_create(
            owner=actor, defaults={**details, "consent_at": timezone.now()}
        )


def confirm_handover(*, actor, pk):
    with audited_transaction(actor, "reservation", pk, "reservation_confirm"):
        reservation = _get_participant_reservation(actor, pk)
        if (
            reservation.status == ReservationStatus.COMPLETED
            and TradeRecord.objects.filter(reservation=reservation).exists()
        ):
            return True
        _require_handover(reservation)
        buying = reservation.buyer_id == actor.pk
        if buying:
            own_field, other_confirmation = "buyer_confirmed_at", reservation.seller_confirmed_at
            recipient = reservation.seller_id
        else:
            own_field, other_confirmation = "seller_confirmed_at", reservation.buyer_confirmed_at
            recipient = reservation.buyer_id
        if getattr(reservation, own_field) is not None:
            return False

        now = timezone.now()
        confirmation = {own_field: now}
        if other_confirmation is None:
            _update_reservation(reservation, **confirmation)
            _notify(reservation, recipient, "的预约中，对方已确认交接。")
            record_action(actor, "reservation", pk, "reservation_confirm")
            return False

        agreed_price = reservation.product_snapshot.get("price_fen", reservation.product.price_fen)
        if type(agreed_price) is not int or agreed_price <= 0:
            raise ReservationConflict("该预约缺少有效的成交价格。", code="invalid_trade_price")
        _update_reservation(reservation, status=ReservationStatus.COMPLETED, **confirmation)
        _set_product_status(reservation.product, ProductStatus.SOLD)
        TradeRecord.objects.create(
            reservation=reservation, completed_at=now, agreed_price_fen=agreed_price
        )
        reservation.contacts.all().delete()
        for recipient_id in (reservation.buyer_id, reservation.seller_id):
            _notify(reservation, recipient_id, "已完成交易。")
        record_action(actor, "reservation", pk, "trade_complete")
        return True


def unpublish_product(*, actor, pk, publication):
    with audited_transaction(actor, "product", pk, "product_unpublish"):
        require_active_actor(actor, staff=False)
        product = Product.objects.filter(pk=pk, seller=actor).first()
        if product is None:
            raise Http404
        if product.published_at != publication:
            raise ReservationConflict("商品已重新发布，请查看最新详情。", code="stale_publication")
        if product.status == ProductStatus.OFFLINE:
            return
        if product.status not in (ProductStatus.PUBLISHED, ProductStatus.RESERVED):
            raise ReservationConflict("商品当前不能下架。", code="product_not_public")

        take_product_offline(product, actor=actor, reason="卖家主动下架。")
        record_action(actor, "product", pk, "product_unpublish")


def take_product_offline(product, *, actor, reason, block_relist=False):
    # Callers hold the write transaction and have verified the product state.
    _set_product_status(
        product,
        ProductStatus.OFFLINE,
        off_reason=reason,
        relist_blocked=product.relist_blocked or block_relist,
    )
    reservation = product.reservations.filter(status__in=ACTIVE_RESERVATION_STATUSES).first()
    if reservation is not None:
        reservation_reason = "商品因违规下架。" if block_relist else reason
        _end_reservation(reservation, ReservationStatus.CANCELLED, reservation_reason)
        _notify(reservation, reservation.buyer_id, "已下架，预约随之取消。")
        record_action(actor, "reservation", reservation.pk, "reservation_cancel")


def cancel_for_suspension(reservation, *, actor, suspended_account):
    _end_reservation(reservation, ReservationStatus.CANCELLED, "当事人账号已停用。")
    if reservation.product.seller.is_active:
        _release_reserved_product(reservation.product)
    partner = (
        reservation.seller_id if reservation.buyer_id == suspended_account else reservation.buyer_id
    )
    _notify(reservation, partner, "的预约因对方账号停用已取消。")
    record_action(actor, "reservation", reservation.pk, "reservation_cancel")


def expire_reservation(*, pk, now):
    with audited_transaction(None, "reservation", pk, "reservation_expire"):
        reservation = Reservation.objects.select_related("product").filter(pk=pk).first()
        if reservation is None or not reservation.pending_expired(now):
            return False
        _end_reservation(reservation, ReservationStatus.EXPIRED, "卖家未在 24 小时内处理。")
        _release_reserved_product(reservation.product)
        for recipient_id in (reservation.buyer_id, reservation.seller_id):
            _notify(reservation, recipient_id, "的预约已超时。")
        record_action(None, "reservation", pk, "reservation_expire")
        return True


def expire_due_reservations():
    now = timezone.now()
    due_ids = list(
        Reservation.objects.filter(
            status=ReservationStatus.PENDING, expires_at__lte=now
        ).values_list("pk", flat=True)
    )
    expired = failed = 0
    for pk in due_ids:
        try:
            expired += expire_reservation(pk=pk, now=now)
        except (ReservationConflict, DatabaseError):
            logger.exception("Failed to expire reservation %s", pk)
            failed += 1
    return expired, failed
