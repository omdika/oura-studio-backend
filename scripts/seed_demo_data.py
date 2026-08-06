"""Seeds the demo dataset from the frontend handoff Section 7 "Mock data state (v2.0)"
(frontend/oura studio frontend/doc/handoff.md) -- explicitly marked there as authoritative
for the real backend's initial seed data. Not present in the backend's own doc/handoff.md.

Seeds: 2 fabric materials + 1 purchase each, 1 product (Scrunchie) with 4 size/fabric
variants, 4 pattern specs (no hardware components -- intentionally omitted in the source
seed spec "for focused flow testing"), and the 4 settings values it names. No sales orders
are seeded: v2.3's "17 seeded orders" note describes them qualitatively only, without
itemized quantities/prices to reproduce exactly -- inventing numbers there would not be
"seed data from the handoff", so this script leaves that out rather than guessing.

Usage:
    BASE_URL=http://127.0.0.1:8123/api/v1 TOKEN=<jwt> python -m scripts.seed_demo_data

Defaults to BASE_URL=http://127.0.0.1:8123/api/v1 if unset. TOKEN is required.
"""

import os
import sys

import requests

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8123/api/v1")
TOKEN = os.environ.get("TOKEN")

if not TOKEN:
    print("Set TOKEN env var to a valid owner JWT (see scripts/mint_owner_token.py)", file=sys.stderr)
    sys.exit(1)

HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def post(path: str, body: dict) -> dict:
    resp = requests.post(f"{BASE_URL}{path}", json=body, headers=HEADERS, timeout=15)
    if not resp.ok:
        print(f"FAILED POST {path}: {resp.status_code} {resp.text}", file=sys.stderr)
        resp.raise_for_status()
    return resp.json()


def patch(path: str, body: dict) -> dict:
    resp = requests.patch(f"{BASE_URL}{path}", json=body, headers=HEADERS, timeout=15)
    if not resp.ok:
        print(f"FAILED PATCH {path}: {resp.status_code} {resp.text}", file=sys.stderr)
        resp.raise_for_status()
    return resp.json()


def main() -> None:
    print("Settings...")
    patch("/settings", {"key": "labor_rate_per_minute", "value": 50})
    patch("/settings", {"key": "default_overhead_per_unit", "value": 300})
    patch("/settings", {"key": "pooled_material_rate:thread", "value": 500})
    patch("/settings", {"key": "pooled_material_rate:packaging", "value": 200})

    print("Materials...")
    satin = post("/materials", {
        "name": "Satin Pelangi", "category": "fabric",
        "purchase_unit": "meter", "usage_unit": "cm", "fabric_width_cm": 200,
    })
    post(f"/materials/{satin['id']}/purchases", {
        "width_cm": 200, "length_cm": 100, "total_cost": 45000, "purchased_at": "2026-08-01",
    })

    waffle = post("/materials", {
        "name": "Waffle Merah", "category": "fabric",
        "purchase_unit": "meter", "usage_unit": "cm", "fabric_width_cm": 150,
    })
    post(f"/materials/{waffle['id']}/purchases", {
        "width_cm": 150, "length_cm": 100, "total_cost": 32000, "purchased_at": "2026-08-01",
    })

    print("Product...")
    product = post("/products", {"name": "Scrunchie", "sku": "SCRUNCHIE"})
    sku = product["sku"]

    variants = [
        ("M", "Satin Pelangi", satin["id"], 20, 90, 10),
        ("M", "Waffle Merah", waffle["id"], 18, 80, 10),
        ("L", "Satin Pelangi", satin["id"], 22, 100, 12),
        ("L", "Waffle Merah", waffle["id"], 21, 90, 12),
    ]

    print("Sizes + pattern specs...")
    for size_label, fabric_name, material_id, cut_width, cut_height, labor_minutes in variants:
        size = post(f"/products/{sku}/sizes", {
            "size_label": size_label, "fabric_variant_name": fabric_name,
        })
        post("/pattern-specs", {
            "product_size_id": size["id"], "fabric_material_id": material_id,
            "cut_width_cm": cut_width, "cut_height_cm": cut_height,
            "rotation_allowed": True, "est_labor_minutes": labor_minutes, "components": [],
        })
        print(f"  {size_label} x {fabric_name}: {size['id']}")

    print("Done.")


if __name__ == "__main__":
    main()
