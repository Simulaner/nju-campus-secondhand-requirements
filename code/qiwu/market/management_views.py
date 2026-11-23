import logging
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import DatabaseError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from .events import WorkflowConflict
from .governance import (
    change_account_status,
    moderate_product,
    moderated_products,
    ordinary_accounts,
    suspension_scope,
)
from .management_forms import (
    OBJECT_LABELS,
    RESULT_LABELS,
    AccountFilterForm,
    AuditFilterForm,
    ModerationForm,
    ProductFilterForm,
)
from .models import (
    ACTIVE_RESERVATION_STATUSES,
    PUBLIC_PRODUCT_STATUSES,
    AuditLog,
    Category,
    ModerationAction,
    Product,
    ProductStatus,
    ReservationStatus,
)
from .services import with_product_images

logger = logging.getLogger(__name__)
ACTION_LABELS = {
    **dict(ModerationAction.choices),
    "save_draft": "保存草稿",
    "submit_review": "提交审核",
    "review": "商品审核",
    "review_approved": "审核通过",
    "review_rejected": "审核驳回",
    "product_unpublish": "卖家主动下架",
    "account_product_unpublish": "账号停用联动下架",
    "reserve": "创建预约",
    "reservation_accept": "卖家接受预约",
    "reservation_reject": "卖家拒绝预约",
    "reservation_cancel": "取消预约",
    "reservation_expire": "预约超时",
    "reservation_confirm": "单方确认交接",
    "trade_complete": "双方确认成交",
}


def staff_required(view):
    @login_required
    @wraps(view)
    def checked(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return checked


@never_cache
@staff_required
@require_GET
def dashboard(request):
    groups = (
        Product.objects.values("status", "category__name")
        .annotate(
            products=Count("pk", distinct=True),
            active=Count(
                "reservations", filter=Q(reservations__status__in=ACTIVE_RESERVATION_STATUSES)
            ),
            trades=Count(
                "reservations__trade", filter=Q(reservations__status=ReservationStatus.COMPLETED)
            ),
        )
        .order_by()
    )
    states = dict.fromkeys(ProductStatus.values, 0)
    categories = dict.fromkeys(Category.objects.order_by("name").values_list("name", flat=True), 0)
    categories[None] = 0
    active = trades = 0
    for group in groups:
        states[group["status"]] += group["products"]
        category = group["category__name"]
        categories[category] = categories.get(category, 0) + group["products"]
        active += group["active"]
        trades += group["trades"]
    return render(
        request,
        "moderation/dashboard.html",
        {
            "product_total": sum(states.values()),
            "pending_total": states[ProductStatus.PENDING],
            "active_total": active,
            "trade_total": trades,
            "states": [(label, states[value]) for value, label in ProductStatus.choices],
            "categories": [(name or "未分类", total) for name, total in categories.items()],
        },
    )


@never_cache
@staff_required
@require_GET
def product_list(request):
    form = ProductFilterForm(request.GET)
    products = moderated_products().order_by("-created_at", "-pk")
    if form.is_valid():
        values = form.cleaned_data
        if values["q"]:
            products = products.filter(title__icontains=values["q"])
        for name in ("category", "status"):
            if values[name]:
                products = products.filter(**{name: values[name]})
    else:
        products = products.none()
    page = Paginator(products, 20).get_page(request.GET.get("page"))
    return render(request, "moderation/products.html", {"form": form, "page": page})


@never_cache
@staff_required
@require_http_methods(["GET", "POST"])
def product_detail(request, pk):
    product = get_object_or_404(with_product_images(moderated_products()), pk=pk)
    if product.status in PUBLIC_PRODUCT_STATUSES:
        action = ModerationAction.VIOLATION
    elif product.status == ProductStatus.OFFLINE and product.relist_blocked:
        action = ModerationAction.UNBLOCK
    else:
        action = None
    posting = request.method == "POST"
    form = ModerationForm(
        request.POST if posting else None,
        actions=(ModerationAction.VIOLATION, ModerationAction.UNBLOCK),
        initial={"version": product.version, "action": action},
    )
    status = 200
    if posting:
        status = 400
        if form.is_valid():
            try:
                moderate_product(actor=request.user, pk=pk, **form.cleaned_data)
            except WorkflowConflict as exc:
                return render(
                    request, "moderation/problem.html", {"notice": exc.message}, status=409
                )
            except DatabaseError:
                logger.exception("Product moderation could not be saved for %s", pk)
                return render(
                    request,
                    "moderation/problem.html",
                    {
                        "notice": "暂时无法确认商品处理结果。",
                    },
                    status=503,
                )
            else:
                messages.success(request, "商品处理结果已保存。")
                return redirect("managed_product", pk=pk)
    return render(
        request,
        "moderation/product.html",
        {
            "product": product,
            "form": form,
            "action": action,
            "decisions": product.moderation_records.select_related("operator"),
        },
        status=status,
    )


@never_cache
@staff_required
@require_GET
def account_list(request):
    form = AccountFilterForm(request.GET)
    accounts = ordinary_accounts().order_by("-date_joined", "-pk")
    if form.is_valid():
        values = form.cleaned_data
        if values["q"]:
            accounts = accounts.filter(
                Q(display_name__icontains=values["q"]) | Q(email__icontains=values["q"])
            )
        if values["status"]:
            accounts = accounts.filter(is_active=(values["status"] == "active"))
    else:
        accounts = accounts.none()
    page = Paginator(accounts, 20).get_page(request.GET.get("page"))
    return render(request, "moderation/accounts.html", {"form": form, "page": page})


@never_cache
@staff_required
@require_http_methods(["GET", "POST"])
def account_detail(request, pk):
    account = get_object_or_404(ordinary_accounts(), pk=pk)
    action = ModerationAction.DISABLE if account.is_active else ModerationAction.RESTORE
    posting = request.method == "POST"
    form = ModerationForm(
        request.POST if posting else None,
        actions=(ModerationAction.DISABLE, ModerationAction.RESTORE),
        initial={"version": account.session_version, "action": action},
    )
    status = 200
    if posting:
        status = 400
        if form.is_valid():
            try:
                change_account_status(actor=request.user, pk=pk, **form.cleaned_data)
            except WorkflowConflict as exc:
                return render(
                    request, "moderation/problem.html", {"notice": exc.message}, status=409
                )
            except DatabaseError:
                logger.exception("Account moderation could not be saved for %s", pk)
                return render(
                    request,
                    "moderation/problem.html",
                    {
                        "notice": "暂时无法确认账号处理结果。",
                    },
                    status=503,
                )
            else:
                messages.success(request, "账号状态已更新。")
                return redirect("managed_account", pk=pk)
    products, reservations = suspension_scope(pk)
    return render(
        request,
        "moderation/account.html",
        {
            "account": account,
            "form": form,
            "action": action,
            "product_count": products.count(),
            "reservation_count": reservations.count(),
            "decisions": account.moderation_records.select_related("operator"),
        },
        status=status,
    )


@never_cache
@staff_required
@require_GET
def audit_log(request):
    form = AuditFilterForm(request.GET)
    entries = AuditLog.objects.select_related("actor").order_by("-occurred_at", "-pk")
    if form.is_valid():
        values = form.cleaned_data
        if values["actor"]:
            entries = entries.filter(actor__display_name__icontains=values["actor"])
        for name in ("object_type", "result"):
            if values[name]:
                entries = entries.filter(**{name: values[name]})
    else:
        entries = entries.none()
    page = Paginator(entries, 25).get_page(request.GET.get("page"))
    for entry in page:
        entry.action_label = ACTION_LABELS.get(entry.action, entry.action)
        entry.object_label = OBJECT_LABELS.get(entry.object_type, entry.object_type)
        entry.result_label = RESULT_LABELS.get(entry.result, entry.result)
    return render(request, "moderation/audit.html", {"form": form, "page": page})
