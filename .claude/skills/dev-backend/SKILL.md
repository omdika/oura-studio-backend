---
name: dev-backend
description: Build and modify the Oura Studios backend — database schema, migrations, and API endpoints — strictly according to the project's handoff document. Use this skill whenever the user asks to build, implement, or fix any database table, migration, API endpoint, validation rule, or business logic (HPP calculation, weighted-average cost, cutting optimizer, versioning) for the Oura Studios app. Always trigger this before writing any backend/API code for this project, since the handoff encodes non-obvious business rules (consumption-based edit locks, archive-vs-delete logic, versioning semantics) that are easy to get wrong if implemented from intuition alone. Database stack: Supabase (PostgreSQL-compatible managed BaaS) — not self-hosted PostgreSQL.
---

# Oura Studios — Backend Dev

Builds the backend (**Python + FastAPI**, **Supabase** (PostgreSQL-compatible managed BaaS) via SQLAlchemy/asyncpg or supabase-py, JWT bearer auth per Section 0/4) for Oura Studios, a custom inventory/production app for a handmade-accessories business with a genuinely non-trivial costing model (multi-fabric joint-product costing, weighted-average material cost, versioned recipes). Multi-device sync is a confirmed requirement, so every non-login endpoint requires a valid JWT. The handoff document is the single source of truth — this skill exists to make sure subtle business rules in it don't get silently simplified away during implementation.

## Step 0 — Always read the handoff first

Locate and read the project's handoff document (`doc/handoff.md`). Read at minimum, in full, before writing any backend code:
- **Section 1** (Core Concepts) — especially 1.3 (cost classification), 1.4 (HPP formula), 1.5 (cutting optimizer algorithm — two-phase shelf-packing with `bestMinLen` and orientation fallback), 1.6 (pricing formula). This is *why* the schema looks the way it does — don't implement the schema without understanding the math it supports.
- **Section 3** (Database Schema) — the literal DDL. Implement it as close to verbatim as your migration tooling allows, including every comment (`--`) — they encode constraints that aren't obvious from the column name alone (e.g. `remaining_length_cm` only applies to fabric; `fabric_width_cm` is a default/suggestion, not a hard constraint).
- **Section 4** (API Contract) — the literal endpoint list, including the validation notes under each endpoint (marked `--` prefixed comments). These are not optional/nice-to-have — e.g. the eligibility rule that a material must have at least one purchase before it can be used in a PatternSpec is a real server-side validation requirement, not a UI-only suggestion.
- **Section 5** (CRUD Audit) — the definitive table of what create/edit/delete operations are allowed per entity, and under what conditions. Treat this table as a checklist: every entity's API implementation should be checked against its row here.

## Non-negotiable business rules (these are the parts most likely to get flattened into generic CRUD if you're not careful)

1. **Weighted-average cost recalculates automatically.** Any create, edit, or delete of a `MaterialPurchase` that changes cost/qty/dimensions must trigger a full recalculation of `material.current_avg_cost` from that material's remaining purchases. This is not a one-time computation at purchase-create time — it's a derived value that must stay in sync. This was resolved as the confirmed approach in v1.2.
2. **Consumption gates editability, not time.** A `MaterialPurchase` is freely editable/deletable *only if unused* (i.e. `remaining_length_cm` still equals the original purchased length, or zero stock_ledger consumption references it for thread/hardware) — not based on how old it is. Once any `CuttingLayout` has consumed part of it, dimension fields lock (reject the write with 400) and delete is blocked (409). Implement this as a real check against `CuttingLayout`/`stock_ledger` references, not a soft UI-only restriction.
3. **PatternSpec versioning is conditional, not automatic.** `POST /pattern-specs` behaves differently depending on whether the current active spec has any `ProductionBatchItem` rows against it: zero → update in place; one or more → deactivate + insert a new versioned row. Don't default to "always version" — that was the original naive design and was deliberately corrected.
4. **StockLedger is append-only, permanently.** Never implement PATCH or DELETE for `stock_ledger` rows, under any circumstance, even for an "admin correction" feature. Corrections happen by writing a new offsetting row (see `POST /stock/adjustments` and `POST /sales-orders/{id}/cancel`).
5. **Archive vs. hard-delete depends on usage history**, for `Material`, `Product`, and `ProductSize`: if the entity has zero references in any historical record (purchases, pattern specs, stock movements, sales), hard-delete is fine; if it has any history, set `is_archived = true` instead and exclude it from active pickers, but never remove the row. Return which of the two happened so the frontend can message it correctly.
6. **Production batches lock permanently on confirm.** Once `production_batch.status = 'confirmed'`, every `production_batch_item` HPP field is immutable forever, and the batch itself cannot be deleted. Draft batches (pre-confirm) can be freely deleted since nothing has touched stock yet.
7. **Sales corrections happen via cancel + restock, never via editing line items.** `POST /sales-orders/{id}/cancel` writes offsetting `stock_ledger` rows (`reason='return'`) and marks the order cancelled — it does not delete the order or edit its items. There is intentionally no endpoint to edit `sales_order_item` after creation.
8. **`GET /reports/sales` must respect `from`/`to` params — not return all-time data.** Filter `sales_order` by `sold_at` date using the provided `from` and `to` dates (inclusive, by calendar day using server timezone). Omitting `from`/`to` should be a 400 Bad Request. This was a confirmed bug in the mock implementation (v2.3) — the real backend must not repeat it.
9. **Supabase connection — use service role key for server-side access.** Environment variables: `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`. The service role key bypasses Row Level Security (RLS) — this is correct since all auth is handled by the FastAPI JWT layer, not Supabase's own auth. Never use the `anon` key for server-to-Supabase communication.

## Critical schema details for `product_size` (v1.7 — don't implement the old uniqueness constraint)

The `product_size` table uniqueness constraint changed in v1.7. **Do not implement `UNIQUE(product_id, size_label)` — that is the old constraint and is wrong.** The correct constraint is:

```sql
UNIQUE(product_id, size_label, fabric_variant_name)
```

Because one product can have multiple rows with the same `size_label` if they differ by `fabric_variant_name` — e.g. "Scrunchie M · Satin Pelangi" and "Scrunchie M · Waffle Merah" are two separate rows, each tracking its own stock independently. `fabric_variant_name` is `TEXT` nullable; `NULL` is treated as a distinct value (i.e. "M" with no fabric variant and "M · Satin Pelangi" are two different rows and can coexist). Return 409 if a `(product_id, size_label, fabric_variant_name)` triple already exists.

The `POST /products/{sku}/sizes` body must accept `fabric_variant_name: str | None = None`.

## Critical path parameter change for size endpoints (v1.7 — sizeId UUID, not sizeLabel string)

The following endpoints use `sizeId: UUID` as their path parameter. **Never use `sizeLabel` as a URL path parameter** — with multiple sizes sharing the same sizeLabel (e.g. two "M" rows), a string-based lookup is ambiguous and will hit the wrong row:

```
GET    /products/{sku}/sizes/{sizeId}               -- UUID
PATCH  /products/{sku}/sizes/{sizeId}               -- UUID
DELETE /products/{sku}/sizes/{sizeId}               -- UUID
POST   /products/{sku}/sizes/{sizeId}/price-advisor -- UUID
```

All four must look up `product_size` by `id` (UUID primary key), not by `size_label` text. Implement a helper that fetches by `(sku, sizeId)` and returns 404 if the UUID doesn't exist under that product.

## One-spec-per-fabric invariant (v1.7)

Each `PatternSpec` has exactly one `fabric_material_id`. The frontend's Tambah Resep sheet creates one `PatternSpec` per selected fabric, each linked to its own `ProductSize` row (the one matching `(sizeLabel, fabricVariantName)`). Backend requirements:
- Accept `product_size_id` in `POST /pattern-specs` as-is — the frontend computes the correct ProductSize UUID.
- Do not attempt to "bundle" multiple fabrics into one spec — if the frontend sends two separate calls for the same (product, size) but different fabrics, both must succeed as separate rows.
- The spec's `fabric_material_id` must reference a material with at least one purchase on record (existing rule, unchanged).

**Why this matters for stock routing:** each `ProductionBatchItem` has a `product_size_id`. When a batch is confirmed, `stock_ledger` rows are written for that specific `product_size_id`. If specs share one ProductSize, confirming a Waffle Merah batch would increment the wrong (Satin) variant's stock. The one-spec-per-fabric rule guarantees each batch item maps to the correct variant.

## Optimizer candidate filter rule (v1.3)

`POST /cutting-optimizer/suggest` must validate that each candidate's `pattern_spec.fabric_material_id` matches the submitted `material_purchase`'s `material_id`. Reject mismatches with 400 Bad Request. A Waffle Merah spec must not appear as a candidate when a Satin Putih purchase is submitted — the optimizer packs one fabric roll at a time.

## Cutting optimizer algorithm (v1.5 — two-phase heuristic)

The backend must implement the two-phase shelf-packing algorithm described in handoff Section 1.5. Key requirements:
- **Phase 1:** pre-compute both orientations and `bestMinLen = min(normalMinRows × cutLength, rotMinRows × cutWidth)` per candidate. Do NOT use `primaryMinRows × primaryRowHeight` as the reservation amount — this over-reserves when the primary rowHeight is large.
- **Phase 2:** for each candidate, reserve `futureMinLength = sum(bestMinLen for remaining candidates)` before computing available space. If primary orientation yields `maxRows=0`, try alt orientation before skipping.
- Return `strategy: String` field ("min_waste" | "max_qty" | "max_profit") in layouts response — required by `POST /cutting-optimizer/layouts` body.

## Workflow for building/modifying backend functionality

1. Read the relevant handoff section(s) in full (Step 0) — especially Section 5's CRUD audit row for whatever entity you're touching.
2. Implement schema changes as Alembic migrations, not destructive edits to existing tables.
3. Implement the endpoint(s) with every validation rule from Section 4's inline comments — write these as actual server-side checks (400 for invalid input, 409 for conflicting state), not just client-side UI restrictions that a direct API call could bypass.
4. Write the derived-value recalculation logic (weighted-average cost, HPP breakdown) as pure, testable functions separate from the request handlers — these are exactly the functions `test-sanity` will verify against hand-computed numbers from the handoff's worked examples.
5. After building, hand off to `test-smoke` (endpoints don't 500) and `test-sanity` (the specific business rule you just built actually holds) before considering the work done.

## When the handoff and reality conflict

If a spec'd validation or recalculation turns out to be ambiguous in an edge case the handoff doesn't cover, don't silently pick a behavior — flag the gap explicitly so it can be resolved and reflected back into the handoff, rather than becoming an undocumented assumption baked into the code.
