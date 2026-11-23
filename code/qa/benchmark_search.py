"""Measure the actual search page against a deterministic fixture on loopback."""

import argparse
import csv
import hashlib
import http.client
import json
import math
import platform
import re
import sqlite3
import statistics
import subprocess
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from threading import Barrier
from urllib.parse import urlencode
from uuid import UUID

import django
from local_app import isolated_app, serve
from PIL import Image

CONCURRENCY = 100
TARGET_SECONDS = 2
MINIMUM_PERCENT = 95
SOCKET_TIMEOUT = 8
PAGE_SIZE = 24
CARD_PATHS = re.compile(rb'<a class="product-card" href="(/products/[a-f0-9-]+/)">')
CATEGORY_TITLES = {
    "数码电子": "相机",
    "图书教材": "教材",
    "生活用品": "台灯",
    "运动户外": "球拍",
}


@dataclass(frozen=True)
class Scenario:
    name: str
    path: str
    total: int
    cards: tuple


def seed_catalog():
    from django.contrib.auth import get_user_model
    from django.db import transaction
    from django.utils import timezone

    from qiwu.market.models import (
        Category,
        Product,
        ProductImage,
        ProductImageContent,
        Reservation,
    )

    users = get_user_model()
    products, pictures, contents, reservations = [], [], [], []
    buyer = users.objects.create_user("benchmark-buyer@smail.nju.edu.cn", display_name="压测买家")
    fixed = datetime(2026, 1, 1, tzinfo=UTC)
    booked_at = timezone.now()
    states = ["published"] * 16 + ["reserved"] * 4 + ["draft", "pending", "rejected", "offline"]
    categories = {}
    for category_no, (category_name, word) in enumerate(CATEGORY_TITLES.items(), start=1):
        category_id = UUID(int=category_no)
        if Category.objects.filter(name=category_name).update(id=category_id) != 1:
            raise RuntimeError(f"Missing seeded category: {category_name}")
        categories[category_name] = category_id
        seller = users.objects.create_user(
            f"benchmark-seller-{category_no}@nju.edu.cn", display_name=f"压测卖家{category_no}"
        )
        with BytesIO() as buffer:
            Image.new("RGB", (240, 180), (105, 50 + category_no * 20, 120)).save(
                buffer, format="JPEG"
            )
            photo = buffer.getvalue()
        for index in range(600):
            identity = (category_no - 1) * 600 + index + 1
            status = states[index % len(states)]
            item = Product(
                pk=UUID(int=identity),
                seller=seller,
                category_id=category_id,
                title=f"{word} {identity:04d} 校园样本",
                description="本地性能验收用固定样本。",
                price_fen=1000 + ((index * 897 + category_no * 631) % 24900),
                condition=1 + (index % 9),
                created_at=fixed + timedelta(minutes=identity),
                published_at=fixed + timedelta(minutes=identity),
                status=status,
            )
            picture = ProductImage(
                pk=UUID(int=identity),
                product=item,
                storage_key=f"benchmark/{identity}.jpg",
                position=1,
                mime_type="image/jpeg",
                size_bytes=len(photo),
            )
            products.append(item)
            pictures.append(picture)
            contents.append(ProductImageContent(image=picture, data=photo))
            if status == "reserved":
                reservations.append(
                    Reservation(
                        product=item,
                        buyer=buyer,
                        seller=seller,
                        status="accepted",
                        created_at=booked_at,
                        expires_at=booked_at + timedelta(hours=24),
                        product_snapshot={"title": item.title, "price_fen": item.price_fen},
                    )
                )
    with transaction.atomic():
        Product.objects.bulk_create(products)
        ProductImage.objects.bulk_create(pictures)
        ProductImageContent.objects.bulk_create(contents)
        Reservation.objects.bulk_create(reservations)
    return (
        products,
        categories,
        {
            "products": len(products),
            "covers": len(pictures),
            "active_reservations": len(reservations),
            "sellers": len(categories),
            "categories": len(categories),
            "statuses": dict(Counter(item.status for item in products)),
        },
    )


def search_cases(products, categories):
    public = [item for item in products if item.status in {"published", "reserved"}]
    books = categories["图书教材"]
    definitions = [
        (
            "keyword_new",
            {"q": "相机", "sort": "newest"},
            [item for item in public if item.title.startswith("相机")],
            lambda item: (-item.published_at.timestamp(), item.pk),
        ),
        (
            "combined",
            {
                "q": "教材",
                "category": str(books),
                "min_price": "25.00",
                "max_price": "220.00",
                "condition": "7",
                "status": "published",
                "sort": "price_up",
            },
            [
                item
                for item in public
                if item.category_id == books
                and item.status == "published"
                and item.condition == 7
                and 2500 <= item.price_fen <= 22000
            ],
            lambda item: (item.price_fen, item.pk),
        ),
        (
            "reserved_page_two",
            {"q": "台灯", "status": "reserved", "sort": "price_down", "page": "2"},
            [
                item
                for item in public
                if item.status == "reserved" and item.title.startswith("台灯")
            ],
            lambda item: (-item.price_fen, item.pk),
        ),
        ("all_oldest", {"sort": "oldest"}, public, lambda item: (item.published_at, item.pk)),
    ]
    cases = []
    for label, filters, matches, sort_key in definitions:
        offset = (int(filters.get("page", 1)) - 1) * PAGE_SIZE
        page = sorted(matches, key=sort_key)[offset : offset + PAGE_SIZE]
        if not page:
            raise RuntimeError(f"Fixture has no matches for {label}.")
        expected = tuple(f"/products/{item.pk}/".encode("ascii") for item in page)
        cases.append(Scenario(label, "/?" + urlencode(filters), len(matches), expected))
    return cases


def request_page(port, case):
    started = time.perf_counter()
    status, body, error = None, b"", ""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=SOCKET_TIMEOUT)
    try:
        conn.request("GET", case.path)
        with conn.getresponse() as response:
            status = response.status
            body = response.read()
    except (OSError, http.client.HTTPException) as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        conn.close()
    elapsed = time.perf_counter() - started
    matches = (
        status == 200
        and not error
        and f"共 {case.total} 件".encode() in body
        and tuple(CARD_PATHS.findall(body)) == case.cards
    )
    if status == 200 and not matches and not error:
        error = "Search count, card identifiers or ordering differed from the fixture."
    return {
        "scenario": case.name,
        "path": case.path,
        "elapsed_ms": round(elapsed * 1000, 3),
        "http_status": status,
        "response_bytes": len(body),
        "matches": matches,
        "within_2s": matches and elapsed <= TARGET_SECONDS,
        "error": error,
    }


def run_wave(port, cases, number):
    gate = Barrier(CONCURRENCY)

    def send(index):
        gate.wait(timeout=12)
        response = request_page(port, cases[index % len(cases)])
        return {"wave": number, "request": index + 1, **response}

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        return list(executor.map(send, range(CONCURRENCY)))


def describe(results):
    times = sorted(item["elapsed_ms"] for item in results)
    matched = sum(item["matches"] for item in results)
    within = sum(item["within_2s"] for item in results)
    return {
        "requests": len(results),
        "http_200": sum(item["http_status"] == 200 for item in results),
        "matched": matched,
        "within_2s": within,
        "percent_within_2s": round(within * 100 / len(results), 3),
        "median_ms": statistics.median(times),
        "p95_ms": times[math.ceil(0.95 * len(times)) - 1],
        "max_ms": times[-1],
        "passes": within * 100 >= MINIMUM_PERCENT * len(results),
    }


def describe_machine():
    result = {
        "os": platform.system(),
        "os_version": platform.mac_ver()[0] or platform.release(),
        "arch": platform.machine(),
        "python": platform.python_version(),
        "django": django.get_version(),
        "sqlite": sqlite3.sqlite_version,
    }
    if result["os"] == "Darwin":
        for label, key in (
            ("cpu", "machdep.cpu.brand_string"),
            ("logical_cpus", "hw.logicalcpu"),
            ("memory_bytes", "hw.memsize"),
        ):
            value = subprocess.run(
                ["sysctl", "-n", key], capture_output=True, text=True, check=True
            ).stdout.strip()
            result[label] = int(value) if value.isdigit() else value
    return result


def snapshot_hash(root):
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.suffix in {".py", ".html", ".css", ".js"} and path.is_file():
            digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New output directory")
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("--rounds must be positive")
    output = args.output.resolve()
    output.mkdir(parents=True)
    results = []
    with isolated_app() as app:
        products, categories, fixture = seed_catalog()
        cases = search_cases(products, categories)
        with serve(app, output / "server.log") as port:
            for scenario in cases:
                sample = request_page(port, scenario)
                if not sample["matches"]:
                    raise RuntimeError(f"Preflight failed: {sample}")
            for number in range(1, args.rounds + 1):
                results.extend(run_wave(port, cases, number))
        source_hash = snapshot_hash(app)
    with (output / "requests.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(results[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(results)
    waves = [
        {"wave": number, **describe([item for item in results if item["wave"] == number])}
        for number in range(1, args.rounds + 1)
    ]
    summary = {
        "concurrency": CONCURRENCY,
        "threshold_seconds": TARGET_SECONDS,
        "target_percent": MINIMUM_PERCENT,
        "socket_timeout_seconds": SOCKET_TIMEOUT,
        "environment": describe_machine(),
        "source_sha256": source_hash,
        "fixture": fixture,
        "warmup_requests": len(cases),
        "scenarios": [
            {
                "name": case.name,
                "path": case.path,
                "expected_total": case.total,
                "expected_cards": len(case.cards),
            }
            for case in cases
        ],
        "waves": waves,
        "all": describe(results),
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"waves": waves, "all": summary["all"]}, ensure_ascii=False, indent=2))
    return 0 if all(wave["passes"] for wave in waves) else 1


if __name__ == "__main__":
    raise SystemExit(main())
