from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Prefetch
from django.http import Http404
from django.utils import timezone

from .models import (
    PUBLIC_PRODUCT_STATUSES,
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

SORTS = {
    "newest": ("最新发布", "-published_at"),
    "oldest": ("最早发布", "published_at"),
    "price_up": ("价格从低到高", "price_fen"),
    "price_down": ("价格从高到低", "-price_fen"),
}


class ProductConflict(ValidationError):
    pass


def _check_version(product, version):
    if product.version != version:
        raise ProductConflict("商品已更新，请重新打开商品查看最新状态。")


def record_action(actor, object_type, pk, action, *, failure=""):
    AuditLog.objects.create(
        actor=actor,
        object_type=object_type,
        object_id=pk,
        action=action,
        result="failure" if failure else "success",
        reason_code=failure,
    )


def record_product_action(actor, pk, action, *, failure=""):
    record_action(actor, "product", pk, action, failure=failure)


def require_active_actor(actor, *, staff):
    if not get_user_model().objects.filter(pk=actor.pk, is_active=True, is_staff=staff).exists():
        raise PermissionDenied


def _update_images(product, removed, uploads):
    product.images.filter(pk__in=removed).delete()
    kept = list(product.images.order_by("position"))
    for slot, img in enumerate(kept, 1):
        if img.position != slot:
            img.position = slot
            img.save(update_fields=["position"])
    for position, upload in enumerate(uploads, len(kept) + 1):
        image = ProductImage(
            product=product,
            position=position,
            mime_type=upload.mime_type,
            size_bytes=len(upload.data),
        )
        image.storage_key = image.pk.hex
        image.save()
        ProductImageContent.objects.create(image=image, data=upload.data)


@transaction.atomic
def save_product(*, actor, pk, creating, action, values):
    require_active_actor(actor, staff=False)
    if creating:
        if values["version"] != 0 or Product.objects.filter(pk=pk).exists():
            raise ProductConflict("这份发布表单已经保存，请从“我的记录”打开商品。")
        product = Product(pk=pk, seller=actor)
    else:
        product = Product.objects.filter(pk=pk, seller=actor).first()
        if product is None:
            raise Http404
        _check_version(product, values["version"])
    if not product.editable:
        raise ProductConflict("当前状态不能修改或重复送审。")
    if action not in ("save", "submit"):
        raise ValidationError("请选择保存或提交审核。")

    category = values["category"]
    if category and not Category.objects.filter(pk=category.pk, enabled=True).exists():
        raise ValidationError("商品分类已停用，请重新选择。")
    price = values["price"]
    fields = {name: values[name] for name in ("title", "condition", "description", "meeting_place")}
    fields.update(
        category_id=category.pk if category else None,
        price_fen=int(price * 100) if price is not None else None,
    )
    changed = any(getattr(product, name) != value for name, value in fields.items())
    image_changes = bool(values["remove_images"] or values["images"])
    if (
        not creating
        and not changed
        and not image_changes
        and product.status == ProductStatus.PUBLISHED
    ):
        return product
    submitting = action == "submit" or product.status == ProductStatus.PUBLISHED
    if submitting or image_changes:
        removed = values["remove_images"]
        uploads = values["images"]
        remaining = list(
            product.images.exclude(pk__in=removed).values_list("content__pk", flat=True)
        )
        if len(remaining) + len(uploads) > 9:
            raise ValidationError({"images": "每件商品最多保留 9 张图片。"})
        if submitting and not uploads and not any(remaining):
            raise ValidationError({"images": "提交审核至少需要一张图片。"})
    for name, value in fields.items():
        setattr(product, name, value)
    if submitting:
        product.status = ProductStatus.PENDING
        product.off_reason = ""
    if not creating:
        product.version += 1
    product.save()
    if image_changes:
        _update_images(product, values["remove_images"], values["images"])
    record_product_action(actor, pk, "submit_review" if submitting else "save_draft")
    return product


@transaction.atomic
def review_product(*, actor, pk, version, decision, reason):
    require_active_actor(actor, staff=True)
    try:
        product = Product.objects.get(pk=pk)
    except Product.DoesNotExist as exc:
        raise Http404 from exc
    _check_version(product, version)
    if product.status != ProductStatus.PENDING:
        raise ProductConflict("此商品当前不在待审核队列中。")
    ReviewRecord.objects.create(
        product=product, product_version=version, reviewer=actor, decision=decision, reason=reason
    )
    if decision == ReviewDecision.APPROVED:
        product.status = ProductStatus.PUBLISHED
        product.published_at = timezone.now()
        message = f"《{product}》已通过审核。"
    else:
        product.status = ProductStatus.REJECTED
        message = f"《{product}》已驳回，请查看审核意见。"
    product.version += 1
    product.save(update_fields=["status", "published_at", "version"])
    Notification.objects.create(
        recipient_id=product.seller_id,
        event_key=f"review:{product.pk}:{version}",
        object_type="product",
        object_id=product.pk,
        message=message,
    )
    record_product_action(actor, pk, f"review_{decision}")
    return product


def public_products():
    return Product.objects.filter(status__in=PUBLIC_PRODUCT_STATUSES).select_related(
        "category", "seller"
    )


def with_product_images(queryset):
    available = ProductImage.objects.filter(content__isnull=False)
    return queryset.prefetch_related(Prefetch("images", queryset=available))


def search_products(filters):
    items = public_products()
    if filters["q"]:
        items = items.filter(title__icontains=filters["q"])
    for field in ("category", "condition", "status"):
        if filters[field]:
            items = items.filter(**{field: filters[field]})
    for name, lookup in [("min_price", "price_fen__gte"), ("max_price", "price_fen__lte")]:
        if filters[name] is not None:
            items = items.filter(**{lookup: int(filters[name] * 100)})
    order = SORTS[filters["sort"] or "newest"][1]
    return with_product_images(items.order_by(order, "pk"))
