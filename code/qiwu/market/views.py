from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from .models import Product, ProductStatus, Reservation


def _participant_reservations(user):
    return Reservation.objects.filter(Q(buyer=user) | Q(seller=user)).select_related("product")


@login_required
@require_GET
def my_records(request):
    products = Product.objects.filter(seller=request.user).order_by("-created_at")
    reservations = _participant_reservations(request.user).order_by("-created_at")
    return render(
        request, "market/records.html", {"products": products, "reservations": reservations}
    )


@login_required
@require_GET
def my_product(request, pk):
    product = get_object_or_404(Product, pk=pk, seller=request.user)
    return render(request, "market/product.html", {"product": product})


@login_required
@require_GET
def my_reservation(request, pk):
    reservation = get_object_or_404(_participant_reservations(request.user), pk=pk)
    return render(request, "market/reservation.html", {"reservation": reservation})


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
