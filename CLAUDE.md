# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands
- **Run local backend server:** `.venv/bin/uvicorn app.main:app --reload --port 8123`
- **Database migrations (Alembic):**
  - Run pending migrations: `alembic upgrade head`
  - Generate a new migration: `alembic revision --autogenerate -m "description_here"`
- **Development/Test utilities:**
  - Run debug script (FastAPI TestClient): `python scripts/debug_test.py`
  - Mint a JWT token for testing: `python -m scripts.mint_owner_token <email> --days 30`
  - Seed mock demo data: `BASE_URL=http://127.0.0.1:8123/api/v1 TOKEN=<token> python -m scripts.seed_demo_data`
- **Frontend App:** Located in `../frontend` (SwiftUI iOS application).

## Tech Stack & Architecture
- **Backend:** Python 3.11 + FastAPI + SQLAlchemy + Alembic + Pydantic.
- **Database:** Supabase (PostgreSQL-compatible managed BaaS). Environment variables are loaded from `.env` (principally `SUPABASE_DB_URL`).
- **Auth Model:** Google Sign-In (OAuth 2.0) on the iOS app. Tokens are exchanged at `POST /api/v1/auth/google`. Only emails matching the `AUTHORIZED_OWNER_EMAIL` environment variable are permitted. Backend routes use JWT validation (`app.deps.get_current_owner`).
  *Note: While the design handoff mentions /auth/login with password hashes, the actual implementation delegates auth entirely to Google Sign-In.*
- **Testing:** Local Python script `scripts/debug_test.py` using FastAPI's `TestClient` and bypassing auth via dependency overrides (`app.dependency_overrides`).

## Entity Model & Relationships
```
Material (kain, benang, hardware, packaging)
   └─ bought as MaterialPurchase (batch, has cost, width, length)
        └─ consumed via PatternSpec (recipe: how much material a SKU+size+fabric-type needs)
             └─ realized in CuttingLayout (optimizer output: how a specific purchase gets cut)
                  └─ becomes ProductionBatch (actual units produced, actual HPP)
                       └─ added to StockLedger (finished goods stock)
                            └─ sold via SalesOrder (deducts stock, records margin)
```

## Core Business Rules & Costing
- **Cost Classification**:
  - `direct_precise`: Fabric, hardware (clips, rings). Tracked precisely via `PatternSpec` (using `PatternSpecFabric` and `PatternComponent`) and `CuttingLayout`.
  - `direct_pooled`: Thread, needles, packaging. Flat rate per unit from settings (`pooled_material_rate:thread`, `pooled_material_rate:packaging` keys).
  - `labor`: Time-based rate per minute (`labor_rate_per_minute` setting).
  - `overhead`: Flat per-unit cost (`default_overhead_per_unit` setting).
- **HPP Per Unit Formula**:
  `HPP = FabricCost + PooledMaterialRate + HardwareCost + (LaborMinutes * LaborRatePerMinute) + OverheadPerUnit`
- **HPP Calculation Fallback (4 Tiers for Sales/Pricing)**:
  1. `batch`: Snapshot HPP from `stock_ledger` of the latest confirmed batch (reason='production').
  2. `manual`: User-provided manual overrides on `ProductSize` (`manual_hpp_*` fields, sum in `manual_hpp_total`).
  3. `pattern_spec`: Estimated HPP computed dynamically by summing components/fabric cost per piece on active `PatternSpec`.
  4. `none`: Default to 0.0.
  *Note: Explicit manual overrides (Tier 2) take precedence over dynamic PatternSpec estimation (Tier 3) to prevent estimates with missing default dimensions from inflating costing calculations.*
- **Uniqueness & API Routing for Product Sizes**:
  - `product_size` enforces composite uniqueness constraint: `UNIQUE(product_id, size_label, fabric_variant_name)`. Because Postgres allows multiple NULL values for `fabric_variant_name`, uniqueness of NULL variants must be checked at the application layer.
  - Size-scoped endpoints use UUID `sizeId` (represented as `size_id` in path), NOT string labels.
- **FIFO Deductions & Usage Logging**:
  - `POST /products/{sku}/sizes/{size_id}/stock-from-bahan` is the path (kebab-case) for manual/QR stock entry bypassing the optimizer.
  - Fabric consumption is estimated as `qty * fabric.cut_height_cm`.
  - Deducts fabric and hardware using FIFO logic.
  - To make manual deductions visible in Pergerakan Stok (`/materials/{id}/usage`), it writes a row to the `material_usage_log` table which is merged into the usage response.
- **QR-Code Scan Lookup**:
  - `GET /product-sizes/{size_id}` allows scanning a QR code containing `oura:{productSizeId}` to retrieve full details (product, size, stock, HPP) without requiring the SKU up front.

## Safe Modifications & CRUD Constraints
- **Material**: Soft-delete via `is_archived = True`. Archiving hides materials from selectors but keeps historical references intact.
- **MaterialPurchase**:
  - Dimension and qty fields are locked (400) once consumed (fabric length decreased or hardware logged as used).
  - Deletion is blocked (409) if the purchase has any recorded consumption.
  - Weighted average cost (`material.current_avg_cost`) is automatically recalculated upon create, update, or delete.
- **Supplier**:
  - Renaming is allowed via `PATCH /suppliers/{id}`.
  - Deletion `DELETE /suppliers/{id}` is blocked (409) if referenced by any purchase.
- **Product**:
  - Renaming is allowed via `PATCH /products/{sku}` (SKU is immutable).
  - Deleting `DELETE /products/{sku}` archives the product if any size has history; otherwise, it performs a hard delete.
- **ProductSize**:
  - Deleting `DELETE /products/{sku}/sizes/{size_id}` hard-deletes if there is zero history (no PatternSpec, stock ledger, or sales order item); otherwise, it soft-deletes via `is_archived = True`.
- **PatternSpec (Resep)**:
  - Supports multiple fabrics (`PatternSpecFabric`) and hardware components (`PatternComponent`).
  - Saved via `POST /pattern-specs`.
  - If no active spec exists for this size/fabric, creates one.
  - If an active spec exists and has zero batches, updates in place (avoids version noise for typo corrections).
  - If an active spec exists and has batches, deactivates old version (`is_active=False`, `effective_to=now()`) and creates a new version.
  - Deleting `DELETE /pattern-specs/{id}` is blocked (409) if referenced by any production batch.
  - Validation: Fabrics and components must have at least one purchase on record to provide a cost basis.
- **CuttingLayout**:
  - Can only be discarded (`POST /cutting-optimizer/layouts/{id}/discard`) when `status = 'suggested'`.
  - Once status changes to `'used'`, discard/deletion is blocked (409).
- **ProductionBatch**:
  - Draft batches can be deleted.
  - Once confirmed (`POST /production-batches/{id}/confirm`), the batch, items, and HPP components are locked (immutable).
- **SalesOrder**:
  - Cannot be edited or deleted.
  - Cancellation via `POST /sales-orders/{id}/cancel` writes positive offsetting stock ledger entries (reason='return') and sets status to `'cancelled'`.
- **StockLedger**:
  - Append-only audit log. Never edit or delete entries.
