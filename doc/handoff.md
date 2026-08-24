# Oura Studios — Product & Engineering Handoff

Custom iOS inventory app for a self-production accessories business (scrunchies, dst).
Core problem this app solves: **accurate HPP (COGS) calculation when raw fabric is cut into multiple product sizes with different fabric types**, plus sales, stock, and margin tracking.

This doc is structured for three audiences reading in parallel:
- **Design** → Section 2 (screens/flows) for mockups in Claude Design
- **Backend** → Section 3 (DB schema) + Section 4 (API contract)
- **Product/QA** → Section 1 (concepts) to sanity-check logic

---

## Revision History

| Version | Date | Changed by | Summary |
|---|---|---|---|
| v1.0 | 2026-07-29 | Initial | Full initial spec — all screens, schema, API contract |
| v1.1 | 2026-07-30 | Frontend | **Auth method changed: email+password → Google SSO (OAuth 2.0).** Login screen replaced with "Sign in with Google" button. Backend impact: `POST /auth/login` replaced with `POST /auth/google`, `owner_account` table drops `password_hash`, adds `google_sub`. Setup step changes from credential seed to authorized-email env config. See Section 0 Auth, Section 2 Screen 0, Section 3 `owner_account`, Section 4 Auth endpoints. |
| v1.2 | 2026-07-31 | Frontend | **Frontend implementation complete (all 11 screens built).** Material name corrected: "Waffle Merah" → "Waffle Merah" throughout spec examples and mock data. Added `GET /reports/dashboard` endpoint (used by Beranda screen — returns today's sales summary + low-stock alerts in one call). Mock seed data simplified to 2 fabric purchases only: Satin Putih (150×100 cm) and Waffle Merah (150×100 cm). Open decision #1 (weighted-average cost) confirmed as implemented approach. See Section 4 Reports, Section 6. |
| v1.3 | 2026-07-31 | Frontend | **Three bug fixes + Optimasi → Produksi flow completed.** (1) Optimasi Step 2 candidate filter fixed: now only shows PatternSpecs whose `fabrics[].materialId` matches the selected purchase's material — previously showed all active specs regardless of fabric. (2) MockAPIService `_productionBatches` now persists across calls — `getProductionBatches`, `confirmBatch`, `updateBatchItem`, `deleteProductionBatch` all operate on the same in-memory store; previously `getProductionBatches` always returned `[]`. (3) `deleteProductionBatch` now correctly blocks confirmed batches with 409. Optimasi → Produksi navigation wired: after "Gunakan Layout Ini", a success screen appears showing batch details with "Lanjut ke Produksi" (switches to Produksi tab) and "Mulai Optimasi Baru" (resets to Step 1). See Section 2 Tab: Optimasi (filter spec clarification added), Section 7 (updated). |
| v2.3 | 2026-08-05 | Frontend | Four UI/UX fixes (picker sheet layout, keyboard "Done" toolbar, InlineSearchDropdownField behavior), new `DateRangeField` component in Reports, proper sales report date filtering in `MockAPIService`, and a backend stack change to Supabase. |
| v2.4 | 2026-08-07 | Frontend | First live connection of the iOS app to the FastAPI backend. Documented all discovered format differences, client-side enrichment pattern, and three additional UX fixes. Critical contract corrections for `POST /pattern-specs` (flat body, `est_labor_minutes > 0`, one fabric per spec) and soft archive via `PATCH /products/{sku}`. |
| v2.5 | 2026-08-07 | Frontend | Spec change: hardware materials can now have BOTH `qty` AND `length_cm` (e.g., elastic band). Backend logic for POST/PATCH material purchases updated to handle this; no DDL migration needed. |
| v2.6 | 2026-08-08 | Frontend + Backend | Three API contract clarifications: `GET /reports/dashboard` now includes `today_units_sold`; `PATCH /production-batches/{id}/items/{item_id}` response is only the updated item, not the whole batch; `product_size_id` must always be present in `GET /production-batches` items. |
| v2.7 | 2026-08-08 | Frontend + Backend | Two bug fixes in `POST /cutting-optimizer/suggest`: primary orientation selection now compares total pieces on full roll (not just pieces-per-row); `waste_pct` is now a decimal fraction (0.0-1.0), not 0-100 percentage. |
| v2.8 | 2026-08-09 | Frontend | Two changes in `TambahPenjualanSheet`: stepper up/down qty buttons fixed with `.buttonStyle(.borderless)`; real-time stock validation with inline warning and "Tambah Stok" button to open `QuickAdjustStokSheet`. |
| v2.9 | 2026-08-09 | Frontend + Backend | Backend `/reports/dashboard` now includes five month-level fields (`month_revenue`, `month_orders`, `month_units_sold`, `month_batches_confirmed`, `avg_margin_pct`) as optional fields. |
| v3.0 | 2026-08-09 | Frontend | User can now see remaining fabric length explicitly. `BahanDetailView` header shows "Sisa X cm" badge, and `Riwayat Pembelian` cards show granular status tags (`[Habis]`, `[Sisa X cm]`). |
| v3.1 | 2026-08-10 | Frontend | FAB "+" in Produk tab now opens `TambahProdukLengkapSheet`, a two-step sheet to create a product and its initial recipe in one flow. |
| v3.2 | 2026-08-10 | Frontend (plan only) | Adds functionality to manually add/delete items to/from a draft production batch via a "+ Tambah Item" button in `BatchCard` and a `TambahItemBatchSheet`. |
| v3.12 | 2026-08-17 | Frontend | Fixed three gaps in `ProduksiBatchView`: HPP client-side enrichment for draft batches, `ConfirmedBatchCard` now displays HPP details, and Price Advisor is inline per variant. |
| v3.13 | 2026-08-18 | Backend | Implemented `GET /materials/{id}/usage` endpoint to provide fabric consumption history derived from `cutting_layout_item` rows. Hardware/thread consumption is not logged. |
| v3.14 | 2026-08-18 | Frontend | Four UI/UX improvements: multi-size recipe input tabs in `TambahResepSheet`, fix for stale `NumericInputField` text on tab change, "Tipe Produk" selector in step 1 (replacing confusing "Gabungkan dalam satu resep" toggle), and updated size presets. |
| v3.15 | 2026-08-18 | Frontend + Backend | Added `fabric_family` field to `Material` to group fabric materials by type (e.g., Satin, Waffle). Backend API (`GET/POST/PATCH /materials`, `GET /materials/families`) updated to support this. Frontend `TambahPembelianSheet` and `TambahResepSheet` updated for grouped input. |
| v3.16 | 2026-08-19 | Frontend only | `TambahResepSheet` now includes an `isFabricVariant` selector, identical to `TambahProdukLengkapSheet`, allowing users to choose between "Varian Kain" (one spec per size x fabric) or "Kombo Kain" (all fabrics in one spec per size). |
| v3.17 | 2026-08-19 | Frontend + Backend | Implemented QR code generation and scanning. New backend endpoint `GET /product-sizes/{id}` for direct `ProductSize` lookup by UUID. `QRGeneratorView` for printing labels, `QRScannerSheet` for fast stock-in/sales. |
| v3.18 | 2026-08-20 | Frontend + Backend | Implemented QR Cart Mode in `QRScannerSheet` for `.sellOnly` mode, allowing scanning multiple items into a virtual cart for single checkout. Backend `POST /sales-orders` now includes a 4-tier HPP fallback (batch -> PatternSpec -> manual -> 0) to prevent sales from being blocked due to missing HPP. |
| v3.19 | 2026-08-20 | Frontend + Backend | Enhanced `ScanToStockSheet` to allow manual HPP input per component (fabric, pooled, hardware, labor, overhead) for products without confirmed batch HPP. This manual HPP is stored in `product_sizes` and used as a fallback. Material deduction toggle added for `addStockFromBahan`. |
| v3.20 | 2026-08-20 | Frontend + Backend | Bugfix session: corrected HPP formula (was 100x too small), fixed QR Generator displaying parent sizes, resolved `EditResepSheet` race condition, corrected Dashboard margin display, and added error/empty states to Reports. Backend fixed `margin-ranking` and `avg_margin_pct` for manual HPP products. |
| v3.21 | 2026-08-21 | Design | Implemented material archiving. Users can now soft-archive materials via `PATCH /materials/{id} {is_archived: true}` from `BahanDetailView` or `BahanListView`. Archived materials are hidden by default but can be viewed with a toggle. Existing recipes referencing archived materials remain valid. |

---

## 0. Tech Stack (confirmed)

This section didn't exist in earlier drafts — the schema and API contract were written stack-agnostic, which left real decisions unstated. Confirmed as of this revision:

- **Frontend:** Native iOS — Swift + SwiftUI, standard Xcode project. (Follows from the original "iOS app" requirement; no cross-platform framework was requested or is in scope.)
- **Sync model:** **Multi-device sync required** — this is not a single-device/local-only app. This resolves what was previously an open question and means a real backend is in scope from the start, not a later add-on.
- **Backend:** Python + **FastAPI**. Recommended companions (standard pairing with this stack, not separately negotiated): **SQLAlchemy** as the ORM, **Alembic** for migrations, **Pydantic** for request/response schemas (FastAPI uses this natively, so the API contract in Section 4 maps directly to Pydantic models).
- **Database: PostgreSQL** — this was already an implicit assumption (Section 3's schema uses `UUID` and `TIMESTAMPTZ`, both Postgres-specific types), now made explicit as a confirmed decision rather than something a reader had to infer from the SQL dialect.
- **Auth (updated in v1.1 — was email+password, now Google SSO):** since data needs to sync across the owner's devices, the API needs real authentication. This app has exactly one owner (not multi-tenant SaaS), so auth is minimal: **Google Sign-In (OAuth 2.0)** on the iOS client, JWT bearer tokens on every backend endpoint. The iOS app never handles a password — it gets a Google ID token via `ASWebAuthenticationSession` and exchanges it at `POST /auth/google` for an app-level JWT. There is no self-registration endpoint. The "authorized account" is configured by the backend operator via an environment variable (`AUTHORIZED_OWNER_EMAIL`) — only a Google Sign-In whose verified email matches that env var is accepted; all others get 403. The `owner_account` table (Section 3) stores the owner's Google `sub` + `email` on their first successful sign-in, and all subsequent sign-ins must match that same `google_sub`. **See Section 4 Auth endpoints for the exact exchange protocol and token verification steps.**
- **Still open:** hosting/deployment platform for the FastAPI backend (e.g. Railway, Render, Fly.io, self-hosted) — see Section 6.

---

## 1. Core Concepts (read this before designing/building anything)

### 1.1 Entities and their relationship
