diff
--- a/doc/handoff.md
+++ b/doc/handoff.md
@@ -1,6 +1,6 @@
 # Oura Studios — Product & Engineering Handoff
 
-Custom iOS inventory app for a self-production accessories business (scrunchies, dst).
+Custom iOS inventory app for a self-production accessories business (scrunchies, etc.).
 Core problem this app solves: **accurate HPP (COGS) calculation when raw fabric is cut into multiple product sizes with different fabric types**, plus sales, stock, and margin tracking.
 
 This doc is structured for three audiences reading in parallel:
@@ -21,6 +21,10 @@
      (was previously untracked -- produced_at is set at batch *creation*, not confirmation, so it
      can't be used as a proxy). Existing confirmed batches (pre-migration) have confirmed_at=NULL
      and are not counted; there were none in the DB at migration time so no backfill was needed.
+   -- v2.14: `production_batch_item.pattern_spec_id` and `fabric_cost_per_piece` are now nullable
+      (migration `f8a7b9c1d2e3`) to support manual batch items without a linked cutting layout
+      or pattern spec. `POST /production-batches/{id}/items` (new) auto-resolves `pattern_spec_id`
+      to the latest active spec, or `null` if none.
    -- avg_margin_pct (Double) -- average of compute_margin_pct(selling_price, hpp_total) across all
    -- product_size rows with a selling_price and at least one *confirmed* production_batch_item
    -- (same confirmed-only lookup as GET /products/{sku}/sizes, to avoid the v2.8 stale-draft bug);
@@ -34,7 +38,7 @@
      -- (shared ProductSizeOut base) since they're cheap to compute alongside current_stock_qty.
 
 | v2.14 | 2026-08-10 | Frontend | **Rencana: POST + DELETE /production-batches/{id}/items (belum diimplementasikan).** Saat ini tidak ada endpoint untuk menambah atau menghapus item dari batch yang sudah ada. Gap ini membuat batch manual (tanpa cutting layout) tidak bisa digunakan — selalu kosong saat dikonfirmasi. Dua endpoint baru diperlukan: `POST /production-batches/{id}/items` (body: `product_size_id`, `qty_actual`; auto-resolve `pattern_spec_id` ke spec aktif terbaru; semua `hpp_* = 0.0` diisi saat confirm; hanya draft; 409 jika confirmed) dan `DELETE /production-batches/{id}/items/{item_id}` (hard delete; hanya draft; 204 on success). Tidak ada migrasi skema — semua kolom sudah ada. Pastikan `qty_suggested` nullable di Alembic model. Lihat `doc/versions/v2.14.md`. |
-| v2.14 | 2026-08-10 | Backend | **IMPLEMENTED: POST + DELETE /production-batches/{id}/items.** Per `doc/versions/v2.14.md`. `production_batch_item.fabric_cost_per_piece` dan `pattern_spec_id` dibuat nullable via migration `f8a7b9c1d2e3` untuk mendukung item batch manual. `POST /production-batches/{id}/items` menambahkan item ke batch draft: `product_size_id`, `qty_actual` dari body; `pattern_spec_id` di-resolve ke spec aktif terbaru atau `null`; `qty_suggested`, `cutting_layout_item_id`, `material_purchase_id`, `fabric_cost_per_piece`, `fabric_length_per_unit_cm` diset `null`. `DELETE /production-batches/{id}/items/{item_id}` menghapus item dari batch draft. Kedua endpoint hanya berlaku untuk batch `draft`, 409 jika `confirmed`. `POST /production-batches/{id}/confirm` di-update untuk menangani `pattern_spec_id` dan `fabric_cost_per_piece` yang `null` pada item batch manual (menggunakan `0.0` untuk perhitungan HPP). Diverifikasi live: batch manual dibuat, item ditambahkan, dikonfirmasi, dan HPP dihitung dengan benar (0 untuk komponen yang tidak ada spec/fabric cost). |
+| v2.14 | 2026-08-21 | Backend | **IMPLEMENTED: POST + DELETE /production-batches/{id}/items.** Per `doc/versions/v2.14.md`. `production_batch_item.fabric_cost_per_piece` dan `pattern_spec_id` dibuat nullable via migration `f8a7b9c1d2e3` untuk mendukung item batch manual. `POST /production-batches/{id}/items` menambahkan item ke batch draft: `product_size_id`, `qty_actual` dari body; `pattern_spec_id` di-resolve ke spec aktif terbaru atau `null`; `qty_suggested`, `cutting_layout_item_id`, `material_purchase_id`, `fabric_cost_per_piece`, `fabric_length_per_unit_cm` diset `null`. `DELETE /production-batches/{id}/items/{item_id}` menghapus item dari batch draft. Kedua endpoint hanya berlaku untuk batch `draft`, 409 jika `confirmed`. `POST /production-batches/{id}/confirm` di-update untuk menangani `pattern_spec_id` dan `fabric_cost_per_piece` yang `null` pada item batch manual (menggunakan `0.0` untuk perhitungan HPP). Diverifikasi live: batch manual dibuat, item ditambahkan, dikonfirmasi, dan HPP dihitung dengan benar (0 untuk komponen yang tidak ada spec/fabric cost). |
 | v2.13 | 2026-08-10 | Backend | **HPP confirm verification (requested via doc/check-hpp-confirm.txt): one real gap fixed, one requested formula flagged as wrong.** Verified all 5 HPP components computed at `POST /production-batches/{id}/confirm` end-to-end against the live DB with isolated test data. Found `hpp_hardware` summed *every* `PatternComponent` row for a spec with no material-type filter — `POST /pattern-specs` never enforced server-side that component materials are actually `category='hardware'` (only the iOS picker UI restricts this) — fixed by filtering `material.category='hardware' AND material.cost_class='direct_precise'` in `_hardware_cost_per_unit()`. Also fixed `_latest_production_item`/`_latest_hpp_map` (products.py) and `_avg_margin_pct` (reports.py) to order by `confirmed_at DESC NULLS LAST, produced_at DESC` instead of `produced_at DESC` alone — found via live testing that every batch confirmed through the currently-deployed (pre-v2.12) API has `confirmed_at=NULL`, and Postgres defaults `NULLS FIRST` on `DESC`, which without the explicit `nullslast()` would wrongly rank an old unmigrated batches ahead of a genuinely more recent confirm). **Flagged, not implemented as requested:** the request's formula `hpp_fabric = cutting_layout_item.cost_per_piece × item.qty_actual` would double-count qty — `cost_per_piece` is already a per-piece rate (`allocate_cost_per_piece()` divides by `qty_suggested`), and `hpp_total` is used as a per-unit cost everywhere else (`stock_ledger.unit_hpp_snapshot`, margin_pct against per-unit `selling_price`, Price Advisor, sales line_profit) — multiplying again would corrupt all of those for any qty > 1. Current code (no re-multiplication) is correct; left unchanged. **Also flagged, not fixed:** `pooled_material_rate:thread`/`pooled_material_rate:packaging` are not currently set in the live `settings` table, so `hpp_pooled_material` computes to 0 for any batch confirmed right now — needs the business owner to configure real values. See `doc/versions/v2.13.md` for full detail (numbered v2.13, not v2.12 as the request doc said, since v2.12 was already claimed by the same-day dashboard/sales-order work below). |
 | v2.12 | 2026-08-09 | Backend | **v2.11 implemented: dashboard month fields + sales-order enrichment.** (1) `GET /reports/dashboard` adds `month_revenue`, `month_orders`, `month_units_sold` (same shape as `today_*` but bucketed from the 1st of the current month), `month_batches_confirmed` (new `production_batch.confirmed_at` column, set in `POST /production-batches/{id}/confirm`; migration added the column but did not backfill since 0 confirmed batches existed at migration time), and `avg_margin_pct` (reuses the confirmed-only "latest item per size" lookup, not the unfiltered one `GET /reports/margin-ranking` still uses — see note below). (2) `POST/GET /sales-orders`, `GET /sales-orders/{id}` items now include `sales_order_id`, `product_name`, `size_label`, `line_revenue`; root adds `total_revenue`/`total_profit`. Response construction changed from plain ORM `from_attributes` mapping to explicit per-request enrichment (batched product/size lookup), since the new fields aren't real columns. **Flagged, not fixed:** `GET /reports/margin-ranking` computes "latest production item per size" without filtering `production_batch.status='confirmed'` — the same stale-draft bug `GET /products/{sku}/sizes` had before the v2.8 fix. Out of scope for this task (not in the v2.11 backlog); left as-is pending a decision on whether to fix. See Section 4 Reports, Sales. |
 | v2.10 | 2026-08-08 | Backend | **Sizes list endpoint: full v2.9 field spec implemented, incl. reopened production_stock_qty/manual_stock_qty conflict.** `GET /products/{sku}/sizes` now returns every field from the v2.9 spec: `selling_price` and `current_stock_qty` (already present via v2.8's `ProductSizeDetailOut`, now confirmed explicit), plus new `production_stock_qty` (sum `stock_ledger.change_qty` where `reason='production'`) and `manual_stock_qty` (sum where `reason IN ('initial','adjustment')`) — both plain `int`, computed the same way for the by-ID endpoint and PATCH/POST responses too, since they live on the shared `ProductSizeOut` base. **Conflict resolution:** v2.4 §1.2 said this breakdown was unneeded; v2.9 reopened it as a product question; this revision's request explicitly asked for the computed fields with fallback semantics matching iOS's `Int?` (nil→0) declaration, so treating that as the product decision — stock-source breakdown ships. **Also fixed while in this endpoint:** `latest_hpp_breakdown` was actually being serialized with the DB column names (`hpp_fabric`, `hpp_pooled_material`, `hpp_hardware`, `hpp_labor`, `hpp_overhead`, `hpp_total`) instead of the v2.8-documented contract (`fabric`, `pooled_material`, `hardware`, `labor`, `overhead`, `total`) — this was reported as "already correct" but the code did not match Section 4; renamed `HppBreakdownOut` fields to match spec (this is a response-shape-only rename, no DB/model change — `production_batch_item`'s own `hpp_*` field names, used by `POST /production-batches/{id}/confirm` etc., are untouched). See Section 4 Products/Stock. |
@@ -375,6 +379,16 @@
   -- note the refined edit behavior: POST /pattern-specs, when updating a spec that currently has zero
      ProductionBatchItem rows against it, updates that same row in place (no new version row inserted).
      Once at least one batch has been produced against a version, further edits always insert a new
-     versioned row as originally spec'd — this avoids meaningless version numbers from same-day typo fixes.
+     versioned row as originally spec'd — this avoids meaningless version numbers from same-day typo fixes.
+
+POST   /production-batches/{id}/items
+  body: { product_size_id: UUID, qty_actual: Int }
+  -- only allowed while batch status='draft'; 409 if confirmed
+  -- pattern_spec_id auto-resolved to latest active spec for product_size_id, or null if none
+  -- all hpp_* fields initialized to 0.0 (computed at confirm)
+  → 201: ProductionBatchItemOut shape
+
+DELETE /production-batches/{id}/items/{item_id}
+  -- only allowed while batch status='draft'; 409 if confirmed
+  -- hard-delete: safe because confirm hasn't run yet (no stock_ledger/remaining_length_cm touched)
+  → 204 No Content
 
 POST   /cutting-optimizer/layouts/{id}/discard
   -- (already existed) clarified: only valid while status='suggested'; returns 409 if status='used'
@@ -384,7 +398,7 @@
      since that only happens at confirm) — lets the user abandon a batch they started by mistake
   -- once status='confirmed', this always 409s — already-established immutability rule
 
-POST   /sales-orders/{id}/cancel
+POST   /sales-orders/{id}/cancel 
   body: { reason? }
   → writes offsetting stock_ledger rows (reason='return', positive qty) for every item in the order,
     restoring finished-goods stock; sets sales_order.status = 'cancelled' (record is kept, not deleted,
@@ -409,7 +423,7 @@
 | ProductionBatch | ✅ spec'd earlier | ✅ `qty_actual` editable pre-confirm; fully locked post-confirm | ⚠️ was missing for the draft (pre-confirm) case — added below |
 | StockLedger | ✅ system-written on every production/sale/adjustment | 🚫 **by design, never** | 🚫 **by design, never** — append-only audit log; corrections happen via a new offsetting adjustment, not by touching history. Worth stating explicitly rather than leaving as an apparent oversight. |
 | SalesOrder | ✅ spec'd earlier | 🚫 **by design, not supported** — line items aren't edited after creation (money/stock have already moved); correct via cancel below, then re-enter | ⚠️ was missing entirely — added `POST /sales-orders/{id}/cancel` below (reverses stock, doesn't hard-delete the record so history isn't lost) |
-| Settings | ✅ fixed key set, no creation needed | ✅ `PATCH /settings` already existed | 🚫 not applicable — fixed set of known keys, nothing to delete |
+| Settings | ✅ fixed key set, no creation needed | ✅ `PATCH /settings` already existed | 🚫 not applicable — fixed set of known keys, nothing to delete |
 
 ### New/updated endpoints from this audit
 
@@ -448,6 +462,16 @@
   -- (already existed) clarified: only valid while status='suggested'; returns 409 if status='used'
      (a ProductionBatch already exists from this layout)
 
+POST   /production-batches/{id}/items
+  body: { product_size_id: UUID, qty_actual: Int }
+  -- only allowed while batch status='draft'; 409 if confirmed
+  -- pattern_spec_id auto-resolved to latest active spec for product_size_id, or null if none
+  -- all hpp_* fields initialized to 0.0 (computed at confirm)
+  → 201: ProductionBatchItemOut shape
+
+DELETE /production-batches/{id}/items/{item_id}
+  -- only allowed while batch status='draft'; 409 if confirmed
+  → 204 No Content
+
 DELETE /production-batches/{id}
   -- allowed only while status='draft' (nothing has been written to stock_ledger or remaining_length_cm yet,
      since that only happens at confirm) — lets the user abandon a batch they started by mistake
