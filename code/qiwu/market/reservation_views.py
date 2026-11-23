import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import DatabaseError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .forms import ReservationActionForm, ReserveForm
from .models import PUBLIC_PRODUCT_STATUSES, ProductStatus, ReservationStatus
from .reservations import (
    ReservationConflict,
    change_reservation,
    participant_reservations,
    reserve_product,
)

logger = logging.getLogger(__name__)


def _problem(request, notice, status, back_url, back_label, retry_form=None):
    return render(
        request,
        "market/reservation_problem.html",
        {
            "notice": notice,
            "back_url": back_url,
            "back_label": back_label,
            "retry_form": retry_form,
        },
        status=status,
    )


@login_required
@require_POST
def reserve(request, pk):
    product_url = reverse("public_product", args=[pk])
    form = ReserveForm(request.POST)
    if not form.is_valid():
        return _problem(
            request, "预约表单无效，请重新打开商品详情。", 400, product_url, "返回商品详情"
        )
    try:
        reservation_id = reserve_product(actor=request.user, pk=pk, **form.cleaned_data)
    except ReservationConflict as exc:
        if exc.code == "unavailable":
            return _problem(request, exc.message, 409, reverse("home"), "返回商品大厅")
        return _problem(request, exc.message, 409, product_url, "查看商品最新状态")
    except DatabaseError:
        logger.exception("Failed to create or retrieve a reservation for product %s", pk)
        return _problem(
            request,
            "暂时无法确认预约结果，可以重试原请求。",
            503,
            product_url,
            "返回商品详情",
            retry_form=form,
        )
    return redirect("my_reservation", pk=reservation_id)


@never_cache
@login_required
@require_GET
def my_reservation(request, pk):
    reservation = get_object_or_404(participant_reservations(request.user), pk=pk)
    buying = reservation.buyer_id == request.user.pk
    product = reservation.product
    expired = reservation.pending_expired(timezone.now())
    product_link = None
    if product.status in PUBLIC_PRODUCT_STATUSES:
        product_link = reverse("public_product", args=[product.pk])
    elif not buying:
        product_link = reverse("my_product", args=[product.pk])
    return render(
        request,
        "market/reservation.html",
        {
            "reservation": reservation,
            "buying": buying,
            "partner": reservation.seller if buying else reservation.buyer,
            "product_link": product_link,
            "expired": expired,
            "show_seller_actions": (
                not buying
                and not request.user.is_staff
                and reservation.status == ReservationStatus.PENDING
                and not expired
                and product.status == ProductStatus.RESERVED
            ),
            "show_cancel": (
                buying
                and not request.user.is_staff
                and reservation.is_active
                and product.status in (ProductStatus.RESERVED, ProductStatus.OFFLINE)
            ),
        },
    )


@login_required
@require_POST
def change(request, pk):
    reservation_url = reverse("my_reservation", args=[pk])
    form = ReservationActionForm(request.POST)
    if not form.is_valid():
        return _problem(
            request, "操作表单无效，请重新打开预约。", 400, reservation_url, "返回预约详情"
        )
    try:
        label = change_reservation(actor=request.user, pk=pk, **form.cleaned_data)
    except ReservationConflict as exc:
        return _problem(request, exc.message, 409, reservation_url, "查看预约最新状态")
    except DatabaseError:
        logger.exception("Failed to update reservation %s", pk)
        return _problem(
            request,
            "暂时无法确认操作结果，可以检查预约最新状态。",
            503,
            reservation_url,
            "查看预约最新状态",
        )
    messages.success(request, f"已{label}。")
    return redirect("my_reservation", pk=pk)
