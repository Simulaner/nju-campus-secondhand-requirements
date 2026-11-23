"""Exercise state races with independent connections to an isolated SQLite file."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from functools import partial
from io import BytesIO
from threading import Barrier
from uuid import uuid4

from local_app import isolated_app
from PIL import Image


def concurrently(*operations):
    from django.db import connections

    from qiwu.market.events import WorkflowConflict

    gate = Barrier(len(operations))

    def invoke(operation):
        db = connections["default"]
        try:
            db.ensure_connection()
            identity = id(db.connection)
            gate.wait(timeout=10)
            try:
                return identity, "committed", operation()
            except WorkflowConflict as exc:
                return identity, "conflict", exc.code
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=len(operations)) as pool:
        results = list(pool.map(invoke, operations))
    assert len({identity for identity, _, _ in results}) == len(operations)
    return [(status, value) for _, status, value in results]


def run_checks():
    from django.contrib.auth import get_user_model
    from django.db.models import Q
    from django.utils import timezone

    from qiwu.market.governance import change_account_status
    from qiwu.market.models import (
        ACTIVE_RESERVATION_STATUSES,
        Category,
        ModerationAction,
        Notification,
        Product,
        ProductImage,
        ProductImageContent,
        ProductStatus,
        RequestReceipt,
        Reservation,
        ReservationStatus,
        TradeRecord,
    )
    from qiwu.market.reservations import (
        change_reservation,
        confirm_handover,
        expire_reservation,
        reserve_product,
        unpublish_product,
    )
    from qiwu.market.services import save_product

    user = get_user_model()
    seller = user.objects.create_user("race-seller@nju.edu.cn", display_name="验收卖家")
    buyer_a = user.objects.create_user("race-buyer-a@smail.nju.edu.cn", display_name="验收买家甲")
    buyer_b = user.objects.create_user("race-buyer-b@nju.edu.cn", display_name="验收买家乙")
    admin = user.objects.create_superuser("race-admin@nju.edu.cn", None, display_name="验收管理员")
    category = Category.objects.get(name="数码电子")
    with BytesIO() as stream:
        Image.new("RGB", (20, 20), "purple").save(stream, format="PNG")
        photo = stream.getvalue()

    def offer(title, owner=seller):
        item = Product.objects.create(
            title=title,
            seller=owner,
            category=category,
            price_fen=2000,
            condition=8,
            description="验收样本",
            status=ProductStatus.PUBLISHED,
            published_at=timezone.now(),
        )
        image = ProductImage.objects.create(
            product=item,
            position=1,
            storage_key=f"{item.pk}.png",
            mime_type="image/png",
            size_bytes=len(photo),
        )
        ProductImageContent.objects.create(image=image, data=photo)
        return item

    def reserve(item, buyer, key=None):
        return reserve_product(
            actor=buyer, pk=item.pk, version=item.version, request_key=key or uuid4()
        )

    def accept(reservation):
        return change_reservation(
            actor=reservation.seller,
            pk=reservation.pk,
            version=reservation.version,
            action="accept",
        )

    def accepted(title):
        item = offer(title)
        result = Reservation.objects.get(pk=reserve(item, buyer_a))
        accept(result)
        result.refresh_from_db()
        return result

    def one_transition(outcomes):
        assert sorted(state for state, _ in outcomes) == ["committed", "conflict"], outcomes

    item = offer("同时提交预约")
    one_transition(concurrently(lambda: reserve(item, buyer_a), lambda: reserve(item, buyer_b)))
    reserved = item.reservations.get()
    assert RequestReceipt.objects.filter(object_id=reserved.pk).count() == 1
    assert Notification.objects.filter(object_id=reserved.pk).count() == 1
    print("PASS two buyers competing for one item")

    retry = offer("重复提交同一请求")
    key = uuid4()
    results = concurrently(
        lambda: reserve(retry, buyer_a, key), lambda: reserve(retry, buyer_a, key)
    )
    assert results[0] == results[1] == ("committed", retry.reservations.get().pk)
    assert RequestReceipt.objects.filter(actor=buyer_a, request_key=str(key)).count() == 1
    print("PASS concurrent retries return one receipt and one reservation")

    edit = offer("修改信息与预约")
    revised = {
        "version": edit.version,
        "title": edit.title,
        "description": edit.description,
        "meeting_place": "",
        "category": category,
        "price": Decimal("32.00"),
        "condition": 8,
        "remove_images": [],
        "images": [],
    }
    one_transition(
        concurrently(
            lambda: save_product(
                actor=seller, pk=edit.pk, creating=False, action="save", values=revised
            ),
            lambda: reserve(edit, buyer_b),
        )
    )
    edit.refresh_from_db()
    assert (edit.status, edit.price_fen, edit.reservations.count()) in {
        (ProductStatus.PENDING, 3200, 0),
        (ProductStatus.RESERVED, 2000, 1),
    }
    print("PASS price edit versus booking")

    for delta in (-1, 1):
        deadline_item = offer("接受与到期")
        pending = Reservation.objects.get(pk=reserve(deadline_item, buyer_a))
        cutoff = timezone.now()
        Reservation.objects.filter(pk=pending.pk).update(
            expires_at=cutoff + timedelta(minutes=delta),
            created_at=cutoff + timedelta(minutes=delta, hours=-24),
        )
        results = concurrently(
            partial(accept, pending),
            partial(expire_reservation, pk=pending.pk, now=cutoff),
        )
        pending.refresh_from_db()
        if delta < 0:
            one_transition(results)
            assert (pending.status, pending.product.status) == (
                ReservationStatus.EXPIRED,
                ProductStatus.PUBLISHED,
            )
        else:
            assert results[1] == ("committed", False)
            assert (pending.status, pending.product.status) == (
                ReservationStatus.ACCEPTED,
                ProductStatus.RESERVED,
            )
    print("PASS seller acceptance and timeout before and after the deadline")

    down = offer("下架与超时")
    pending = Reservation.objects.get(pk=reserve(down, buyer_a))
    concurrently(
        lambda: unpublish_product(actor=seller, pk=down.pk, publication=down.published_at),
        lambda: expire_reservation(pk=pending.pk, now=pending.expires_at),
    )
    pending.refresh_from_db()
    assert pending.product.status == ProductStatus.OFFLINE
    assert pending.status in (ReservationStatus.CANCELLED, ReservationStatus.EXPIRED)
    assert not expire_reservation(pk=pending.pk, now=pending.expires_at)
    print("PASS unpublish versus expiry and replay of the expiry job")

    both = accepted("同时完成确认")
    result = concurrently(
        lambda: confirm_handover(actor=seller, pk=both.pk),
        lambda: confirm_handover(actor=buyer_a, pk=both.pk),
    )
    assert sorted(result) == [("committed", False), ("committed", True)]
    assert TradeRecord.objects.filter(reservation=both).count() == 1
    print("PASS simultaneous confirmation creates one trade")

    finish = accepted("第二方确认与取消")
    assert not confirm_handover(actor=seller, pk=finish.pk)
    finish.refresh_from_db()
    one_transition(
        concurrently(
            lambda: change_reservation(
                actor=buyer_a, pk=finish.pk, version=finish.version, action="cancel"
            ),
            lambda: confirm_handover(actor=buyer_a, pk=finish.pk),
        )
    )
    finish.refresh_from_db()
    assert (
        finish.status,
        finish.product.status,
        TradeRecord.objects.filter(reservation=finish).count(),
    ) in {
        (ReservationStatus.CANCELLED, ProductStatus.PUBLISHED, 0),
        (ReservationStatus.COMPLETED, ProductStatus.SOLD, 1),
    }
    print("PASS second confirmation versus cancellation")

    suspend = offer("停用与预约", owner=buyer_b)
    statuses = concurrently(
        lambda: change_account_status(
            actor=admin,
            pk=buyer_b.pk,
            action=ModerationAction.DISABLE,
            version=buyer_b.session_version,
            reason="并发验收",
        ),
        lambda: reserve(suspend, buyer_a),
    )
    assert statuses[0][0] == "committed"
    suspend.refresh_from_db()
    buyer_b.refresh_from_db()
    assert not buyer_b.is_active and suspend.status == ProductStatus.OFFLINE
    assert not Reservation.objects.filter(
        Q(buyer=buyer_b) | Q(seller=buyer_b), status__in=ACTIVE_RESERVATION_STATUSES
    ).exists()
    print("PASS account suspension versus new booking")


if __name__ == "__main__":
    with isolated_app():
        run_checks()
