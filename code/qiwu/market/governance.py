from django.contrib.auth import get_user_model
from django.db.models import F, Q
from django.http import Http404

from .events import WorkflowConflict, audited_transaction, notify, record_action
from .models import (
    ACTIVE_RESERVATION_STATUSES,
    PUBLIC_PRODUCT_STATUSES,
    ModerationAction,
    ModerationRecord,
    Product,
    ProductStatus,
    Reservation,
)
from .reservations import cancel_for_suspension, take_product_offline
from .services import require_active_actor


def moderated_products():
    return Product.objects.exclude(status=ProductStatus.DRAFT).select_related("category", "seller")


def ordinary_accounts():
    return get_user_model().objects.filter(is_staff=False, is_superuser=False)


def suspension_scope(account_id):
    products = Product.objects.filter(seller_id=account_id, status__in=PUBLIC_PRODUCT_STATUSES)
    reservations = Reservation.objects.filter(
        Q(buyer_id=account_id) | Q(seller_id=account_id), status__in=ACTIVE_RESERVATION_STATUSES
    )
    return products, reservations


def _require_reason(reason):
    reason = reason.strip()
    if not reason:
        raise WorkflowConflict("请填写处理原因。", code="reason_required")
    return reason


def moderate_product(*, actor, pk, action, version, reason):
    with audited_transaction(actor, "product", pk, action):
        require_active_actor(actor, staff=True)
        product = moderated_products().filter(pk=pk).first()
        if product is None:
            raise Http404
        if product.version != version:
            raise WorkflowConflict("商品已更新，请核对最新状态。", code="stale_product")

        if action == ModerationAction.VIOLATION:
            reason = _require_reason(reason)
            if product.status not in PUBLIC_PRODUCT_STATUSES:
                raise WorkflowConflict("当前状态不能执行违规下架。", code="product_not_public")
            take_product_offline(product, actor=actor, reason=reason, block_relist=True)
            description = "已因违规下架。处理原因可在商品记录中查看。"
        elif action == ModerationAction.UNBLOCK:
            if product.status != ProductStatus.OFFLINE or not product.relist_blocked:
                raise WorkflowConflict("当前商品没有重审限制。", code="not_blocked")
            description = "已解除重审限制，可以编辑并重新提交审核。"
            Product.objects.filter(pk=pk).update(
                relist_blocked=False, version=F("version") + 1, off_reason=description
            )
        else:
            raise WorkflowConflict("请选择有效的商品处理操作。", code="invalid_action")

        decision = ModerationRecord.objects.create(
            operator=actor, product=product, action=action, reason=reason
        )
        notify(
            product.seller_id,
            "product",
            pk,
            f"moderation:{decision.pk}",
            f"《{product}》{description}",
        )
        record_action(actor, "product", pk, action)


def change_account_status(*, actor, pk, action, version, reason):
    with audited_transaction(actor, "account", pk, action):
        require_active_actor(actor, staff=True)
        account = ordinary_accounts().filter(pk=pk).first()
        if account is None:
            raise Http404
        if account.session_version != version:
            raise WorkflowConflict("账号状态已更新，请查看最新资料。", code="stale_account")
        if action == ModerationAction.DISABLE:
            reason = _require_reason(reason)
            next_active = False
        elif action == ModerationAction.RESTORE:
            next_active = True
        else:
            raise WorkflowConflict("请选择有效的账号处理操作。", code="invalid_action")
        if account.is_active == next_active:
            raise WorkflowConflict("账号已经处于该状态。", code="account_unchanged")

        ordinary_accounts().filter(pk=pk).update(
            is_active=next_active, session_version=F("session_version") + 1
        )
        # Also close residual activity on accounts disabled before this workflow existed.
        products, reservations = suspension_scope(pk)
        for product in products.order_by("pk"):
            take_product_offline(product, actor=actor, reason="卖家账号已停用。")
            record_action(actor, "product", product.pk, "account_product_unpublish")
        for reservation in reservations.select_related("product__seller").order_by("pk"):
            cancel_for_suspension(reservation, actor=actor, suspended_account=pk)

        decision = ModerationRecord.objects.create(
            operator=actor, account=account, action=action, reason=reason
        )
        message = (
            "账号已恢复，可以重新登录。请在我的记录中检查商品和预约。"
            if next_active
            else "账号已停用。未售公开商品和有效预约已处理，详情可在恢复后查看。"
        )
        notify(pk, "account", pk, f"moderation:{decision.pk}", message)
        record_action(actor, "account", pk, action)
