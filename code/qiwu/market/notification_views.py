from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .models import Notification, Product
from .reservations import participant_reservations
from .services import public_products


@never_cache
@login_required
@require_GET
def inbox(request):
    items = Notification.objects.filter(recipient=request.user)
    unread = items.filter(read_at__isnull=True).count()
    showing_unread = request.GET.get("status") == "unread"
    if showing_unread:
        items = items.filter(read_at__isnull=True)
    page = Paginator(items.order_by("-created_at", "-pk"), 20).get_page(request.GET.get("page"))
    return render(
        request,
        "market/notifications.html",
        {
            "page": page,
            "unread": unread,
            "showing_unread": showing_unread,
        },
    )


@login_required
@require_POST
def read_notice(request, pk):
    items = Notification.objects.filter(recipient=request.user)
    notice = get_object_or_404(items, pk=pk)
    items.filter(pk=pk, read_at__isnull=True).update(read_at=timezone.now())
    target = notice.object_id
    if notice.object_type == "reservation":
        if participant_reservations(request.user).filter(pk=target).exists():
            return redirect("my_reservation", pk=target)
    elif notice.object_type == "product":
        if Product.objects.filter(pk=target, seller=request.user).exists():
            return redirect("my_product", pk=target)
        if public_products().filter(pk=target).exists():
            return redirect("public_product", pk=target)
    elif notice.object_type == "account" and request.user.pk == target:
        return redirect("my_records")
    return redirect("notifications")


@login_required
@require_POST
def read_all(request):
    Notification.objects.filter(recipient=request.user, read_at__isnull=True).update(
        read_at=timezone.now()
    )
    return redirect("notifications")
