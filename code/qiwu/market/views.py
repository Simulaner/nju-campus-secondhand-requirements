import logging
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import DatabaseError
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from .forms import ProductForm, ReserveForm, ReviewForm, SearchForm, UnpublishForm
from .models import (
    ACTIVE_RESERVATION_STATUSES,
    PUBLIC_PRODUCT_STATUSES,
    Product,
    ProductImageContent,
    ProductStatus,
)
from .reservations import (
    ReservationConflict,
    participant_reservations,
    unpublish_product,
)
from .services import (
    ProductConflict,
    public_products,
    record_product_action,
    review_product,
    save_product,
    search_products,
    with_product_images,
)

logger = logging.getLogger(__name__)


@require_GET
def home(request):
    form = SearchForm(request.GET)
    items = search_products(form.cleaned_data) if form.is_valid() else Product.objects.none()
    page = Paginator(items, 24).get_page(request.GET.get("page"))
    return render(request, "home.html", {"form": form, "page": page})


@never_cache
@require_GET
def public_product(request, pk):
    product = get_object_or_404(with_product_images(public_products()), pk=pk)
    reservable = (
        product.status == ProductStatus.PUBLISHED
        and product.seller.is_active
        and product.price_fen is not None
    )
    context = {
        "product": product,
        "login_to_reserve": reservable and not request.user.is_authenticated,
    }
    if request.user.is_authenticated:
        if reservable and not request.user.is_staff and product.seller_id != request.user.pk:
            context["reserve_form"] = ReserveForm(
                initial={"version": product.version, "request_key": uuid.uuid4()}
            )
        if product.status == ProductStatus.RESERVED:
            context["reservation_id"] = (
                participant_reservations(request.user)
                .filter(product=product, status__in=ACTIVE_RESERVATION_STATUSES)
                .values_list("pk", flat=True)
                .first()
            )
    return render(request, "market/public_product.html", context)


@never_cache
@require_GET
def product_image(request, pk):
    allowed = Q(image__product__status__in=PUBLIC_PRODUCT_STATUSES)
    if request.user.is_authenticated:
        allowed |= Q(image__product__seller=request.user)
        if request.user.is_staff:
            allowed |= Q(image__product__status=ProductStatus.PENDING)
    content = get_object_or_404(
        ProductImageContent.objects.select_related("image").filter(allowed), pk=pk
    )
    return HttpResponse(bytes(content.data), content_type=content.image.mime_type)


@login_required
@require_GET
def my_records(request):
    products = Product.objects.filter(seller=request.user).order_by("-created_at")
    status = request.GET.get("status", "")
    if status:
        products = products.filter(status=status)
    reservations = participant_reservations(request.user).order_by("-created_at")
    return render(
        request,
        "market/records.html",
        {
            "products": products,
            "reservations": reservations,
            "states": ProductStatus.choices,
            "status": status,
        },
    )


@login_required
@require_GET
def my_product(request, pk):
    product = get_object_or_404(
        with_product_images(Product.objects.all()), pk=pk, seller=request.user
    )
    return render(
        request,
        "market/product.html",
        {
            "product": product,
            "reviews": product.reviews.order_by("-created_at"),
            "can_unpublish": not request.user.is_staff
            and product.status in PUBLIC_PRODUCT_STATUSES,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def unpublish(request, pk):
    if request.user.is_staff:
        raise PermissionDenied
    product = get_object_or_404(Product, pk=pk, seller=request.user)
    if request.method == "GET" and product.status not in PUBLIC_PRODUCT_STATUSES:
        return redirect("my_product", pk=pk)
    form = UnpublishForm(
        request.POST if request.method == "POST" else None,
        initial={"publication": product.published_at.isoformat() if product.published_at else ""},
    )
    if request.method == "POST" and form.is_valid():
        try:
            unpublish_product(actor=request.user, pk=pk, **form.cleaned_data)
        except ReservationConflict as exc:
            return render(
                request,
                "market/edit_closed.html",
                {"product": product, "notice": exc.message},
                status=409,
            )
        except DatabaseError:
            logger.exception("Failed to unpublish product %s", pk)
            return render(
                request,
                "market/edit_closed.html",
                {"product": product, "notice": "暂时无法确认下架结果。"},
                status=503,
            )
        messages.success(request, "商品已下架。")
        return redirect("my_product", pk=pk)
    return render(request, "market/unpublish.html", {"product": product, "form": form})


@login_required
@require_http_methods(["GET", "POST"])
def edit_product(request, pk=None):
    if request.user.is_staff:
        raise PermissionDenied
    creating = pk is None
    token = ""
    if creating:
        signer = signing.Signer(salt=f"qiwu.new-product.{request.user.pk}")
        if request.method == "POST":
            token = request.POST.get("draft_token", "")
            try:
                pk = uuid.UUID(hex=signer.unsign(token))
            except (signing.BadSignature, ValueError):
                return HttpResponseBadRequest("发布表单无效，请重新打开发布页。")
        else:
            pk = uuid.uuid4()
            token = signer.sign(pk.hex)
        product = Product(pk=pk, seller=request.user)
    else:
        product = get_object_or_404(Product, pk=pk, seller=request.user)
        if not product.editable:
            if request.method == "GET":
                messages.info(request, "商品当前状态不可编辑。")
                return redirect("my_product", pk=pk)
            return render(
                request,
                "market/edit_closed.html",
                {"product": product, "notice": "商品当前状态不可编辑。"},
                status=409,
            )
    posting = request.method == "POST"
    action = request.POST.get("action")
    form = ProductForm(
        request.POST if posting else None,
        request.FILES if posting else None,
        product=product,
        action=action,
    )
    if posting and form.is_valid():
        try:
            product = save_product(
                actor=request.user,
                pk=pk,
                creating=creating,
                action=action,
                values=form.cleaned_data,
            )
        except ProductConflict as exc:
            return render(
                request,
                "market/edit_closed.html",
                {"product": None if creating else product, "notice": exc.message},
                status=409,
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(
                request,
                "已提交审核。" if product.status == ProductStatus.PENDING else "商品已保存。",
            )
            return redirect("my_product", pk=pk)
    return render(
        request,
        "market/edit.html",
        {"product": product, "form": form, "creating": creating, "token": token},
    )


@login_required
@require_GET
def review_queue(request):
    if not request.user.is_staff:
        raise PermissionDenied
    products = (
        Product.objects.filter(status=ProductStatus.PENDING)
        .select_related("seller")
        .order_by("created_at")
    )
    return render(request, "market/review_queue.html", {"products": products})


@login_required
@require_http_methods(["GET", "POST"])
def moderate(request, pk):
    posting = request.method == "POST"
    if not request.user.is_staff:
        if posting:
            record_product_action(request.user, pk, "review", failure="not_staff")
        raise PermissionDenied
    items = with_product_images(Product.objects.select_related("seller", "category"))
    product = items.filter(pk=pk).first()
    if product is None:
        if posting:
            record_product_action(request.user, pk, "review", failure="missing_product")
        raise Http404
    if product.status != ProductStatus.PENDING:
        if not posting:
            raise Http404
        record_product_action(request.user, pk, "review", failure="not_pending")
        return render(request, "market/review_closed.html", status=409)
    form = ReviewForm(request.POST if posting else None, initial={"version": product.version})
    if not posting or not form.is_valid():
        if posting:
            record_product_action(request.user, pk, "review", failure="invalid_form")
        return render(request, "market/moderate.html", {"product": product, "form": form})
    try:
        review_product(actor=request.user, pk=pk, **form.cleaned_data)
    except ProductConflict:
        record_product_action(request.user, pk, "review", failure="stale")
        return render(request, "market/review_closed.html", status=409)
    except (Http404, PermissionDenied):
        record_product_action(request.user, pk, "review", failure="access_changed")
        raise
    except DatabaseError:
        logger.exception("Failed to save the review of product %s", pk)
        try:
            record_product_action(request.user, pk, "review", failure="write_failed")
        except DatabaseError:
            logger.exception("Failed to record the review failure for product %s", pk)
        return render(
            request, "market/review_closed.html", {"notice": "审核未保存，请重试。"}, status=503
        )
    messages.success(request, "审核结果已保存。")
    return redirect("review_queue")
