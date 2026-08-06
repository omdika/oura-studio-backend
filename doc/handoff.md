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
| v1.2 | 2026-07-31 | Frontend | **Frontend implementation complete. Dashboard endpoint added.** Added `GET /reports/dashboard` — single-call summary for Beranda screen (today_revenue, today_order_count, today_profit, low_stock_alerts). Weighted-average cost confirmed as approach (Open Decision #1 resolved). See Section 4 Reports. |
| v1.3 | 2026-07-31 | Frontend | **Optimizer candidate filter rule + cancel/delete endpoints.** (1) `POST /cutting-optimizer/suggest` must validate that each candidate's `pattern_spec.fabric_material_id` matches the submitted `material_purchase`'s `material_id` — reject mismatches with 400 Bad Request. (2) Added `POST /sales-orders/{id}/cancel` — restores stock via offsetting stock_ledger rows, sets status='cancelled', keeps record. (3) Added `DELETE /production-batches/{id}` — allowed only while status='draft'; 409 if confirmed. See Section 4 Cutting Optimizer, Production, Sales. |
| v1.4–v1.5 | 2026-07-31–2026-08-01 | Frontend | **Optimizer algorithm: two-phase heuristic with minQty + bestMinLen.** `POST /cutting-optimizer/suggest` now accepts `candidates[].min_qty`. Backend must implement two-phase shelf-packing: Phase 1 pre-computes both orientations and `bestMinLen = min(normalMinRows × cutLength, rotMinRows × cutWidth)` per candidate; Phase 2 uses `futureMinLength = sum(bestMinLen for remaining candidates)` to reserve fabric before allocating each candidate. Orientation fallback: if primary orientation yields `maxRows=0`, try alt orientation before skipping. `POST /cutting-optimizer/layouts` response must include `strategy: String` field. See Section 1.5 for full algorithm spec. |
| v1.6 | 2026-08-01 | Frontend | **Mandatory-when-selected validation rule.** No schema or API changes. Frontend-only: any field that becomes visible because the user made a selection is mandatory — incomplete entries must be named in an error and block save (not silently dropped). |
| v1.7 | 2026-08-02 | Frontend | **Fabric variant as first-class ProductSize dimension.** Schema change: `product_size` gains `fabric_variant_name TEXT` (nullable); uniqueness constraint changes from `UNIQUE(product_id, size_label)` to `UNIQUE(product_id, size_label, fabric_variant_name)`. API change: all size-scoped endpoints now use `sizeId: UUID` path param instead of `sizeLabel: String` — `GET /products/{sku}/sizes/{sizeId}`, `PATCH /products/{sku}/sizes/{sizeId}`, `DELETE /products/{sku}/sizes/{sizeId}`, `POST /products/{sku}/sizes/{sizeId}/price-advisor`. `POST /products/{sku}/sizes` body adds optional `fabric_variant_name`. Backend must enforce composite uniqueness and return 409 on duplicate. See Section 3 `product_size`, Section 4 Products/Stock, Section 5. |
| v1.8–v2.0 | 2026-08-02 | Frontend | **3-level product navigation + initial stock from bahan.** No schema changes beyond v1.7. New endpoint: `POST /products/{sku}/sizes/{sizeId}/addStockFromBahan` — optional; called when user enters Stok Awal on a new fabric variant that has a matching PatternSpec; deducts fabric length from bahan purchase (FIFO) and records stock as `manualStockQty`. `GET /pattern-specs?product_id=&size_label=&fabric_material_id=` used to check spec existence before showing Stok Awal field. |
| v2.1–v2.2 | 2026-08-02–2026-08-03 | Frontend | **FabricPickerSheet + TambahPenjualanSheet.** No new endpoints. `GET /products/{sku}/sizes` filter: backend should support `?archived=false&min_stock=1` query params (or client-side filter) for picker that shows only in-stock, non-archived variants. `GET /materials?category=fabric` drives FabricPickerSheet list. |
| v2.3 | 2026-08-05 | Frontend | **Reports date filtering confirmed + Supabase stack.** `GET /reports/sales?from=&to=&group_by=` date range params confirmed required — backend must filter by `sold_at` date (not return all-time data). **Database stack changed: PostgreSQL → Supabase** — see Section 0. No schema changes. |

---

## 0. Tech Stack (confirmed)

This section didn't exist in earlier drafts — the schema and API contract were written stack-agnostic, which left real decisions unstated. Confirmed as of this revision:

- **Frontend:** Native iOS — Swift + SwiftUI, standard Xcode project. (Follows from the original "iOS app" requirement; no cross-platform framework was requested or is in scope.)
- **Sync model:** **Multi-device sync required** — this is not a single-device/local-only app. This resolves what was previously an open question and means a real backend is in scope from the start, not a later add-on.
- **Backend:** Python + **FastAPI**. Recommended companions (standard pairing with this stack, not separately negotiated): **SQLAlchemy** as the ORM, **Alembic** for migrations, **Pydantic** for request/response schemas (FastAPI uses this natively, so the API contract in Section 4 maps directly to Pydantic models).
- **Database: Supabase** (updated v2.3 — was PostgreSQL). Supabase is a PostgreSQL-compatible managed backend-as-a-service. The SQL schema in Section 3 is valid as-is — Supabase runs PostgreSQL under the hood, so `UUID`, `TIMESTAMPTZ`, `REFERENCES`, and all constraint syntax are supported unchanged. For FastAPI server-side access use `asyncpg` or `psycopg2` with the Supabase connection string, or the `supabase-py` client library. Key environment variables: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (replaces a raw `DATABASE_URL`). Supabase also provides auth and storage — these are not used by this app (the app uses its own JWT auth per Section 4).
- **Auth (updated in v1.1 — was email+password, now Google SSO):** since data needs to sync across the owner's devices, the API needs real authentication. This app has exactly one owner (not multi-tenant SaaS), so auth is minimal: **Google Sign-In (OAuth 2.0)** on the iOS client, JWT bearer tokens on every backend endpoint. The iOS app never handles a password — it gets a Google ID token via `ASWebAuthenticationSession` and exchanges it at `POST /auth/google` for an app-level JWT. There is no self-registration endpoint. The "authorized account" is configured by the backend operator via an environment variable (`AUTHORIZED_OWNER_EMAIL`) — only a Google Sign-In whose verified email matches that env var is accepted; all others get 403. The `owner_account` table (Section 3) stores the owner's Google `sub` + `email` on their first successful sign-in, and all subsequent sign-ins must match that same `google_sub`. **See Section 4 Auth endpoints for the exact exchange protocol and token verification steps.**
- **Still open:** hosting/deployment platform for the FastAPI backend (e.g. Railway, Render, Fly.io, self-hosted) — see Section 6.

---

## 1. Core Concepts (read this before designing/building anything)

### 1.1 Entities and their relationship

```
Material (kain, benang, hardware)
   └─ bought as MaterialPurchase (batch, has cost, width, length)
        └─ consumed via PatternSpec (recipe: how much material a SKU+size+material-type needs)
             └─ realized in CuttingLayout (optimizer output: how a specific purchase gets cut)
                  └─ becomes ProductionBatch (actual units produced, actual HPP)
                       └─ added to StockLedger (finished goods stock)
                            └─ sold via SalesOrder (deducts stock, records margin)
```

### 1.2 Why HPP isn't a fixed number per SKU

Fabric consumption depends on **(SKU, size, fabric type)** — satin and waffle need different cut dimensions for the same finished size because of stretch behavior. So `PatternSpec` is keyed by all three, not just SKU+size.

Fabric cost per piece depends on **how the cutting layout nests on the specific fabric roll/piece width** — not on cm² alone. Two patterns with the same area can have very different cost-per-piece depending on how well they tile against the fabric's width. So HPP for fabric is computed **per purchase batch + per layout**, not as a flat rate.

### 1.3 Cost classification (drives how precisely each is tracked)

| Class | Examples | Tracking method |
|---|---|---|
| `direct_precise` | Fabric, hardware (clips, rings) | Tracked per unit via PatternSpec + CuttingLayout |
| `direct_pooled` | Thread, needles, glue | NOT tracked per color/unit. Pooled cost ÷ estimated yield = flat rate per finished unit |
| `labor` | Maker's time | Rate/minute × estimated minutes per size (from PatternSpec) |
| `overhead` | Packaging, electricity, platform fees | Flat rate per unit or % of price |

### 1.4 HPP formula per unit

```
HPP(sku, size) =
    fabric_cost_per_piece(from CuttingLayout of the batch used)
  + pooled_material_rate (thread etc., flat rate, updated periodically)
  + hardware_cost (direct, from PatternSpec qty × Material unit cost)
  + labor_minutes(sku,size) × labor_rate_per_minute
  + overhead_per_unit
```

### 1.5 Cutting optimizer (nesting problem)

Given: a fabric purchase's dimensions (width × length — every purchase, whether a large bolt or a small remnant, is entered as one bounded rectangle; there's no separate "roll" purchase mode, see Section 2 Tab Bahan), and a set of candidate PatternSpecs (each with cut W×H, and whether rotation is allowed — some prints are directional and can't rotate).

Goal: for a given MaterialPurchase, compute one or more candidate **CuttingLayouts** — combinations of {size, qty, orientation} — and their resulting waste % and cost-per-piece.

Approach: **shelf-packing heuristic** (not a perfect solver — heuristic is enough here):
1. For each candidate size, compute pieces-per-shelf-row and cm-of-fabric-length used per row, trying both orientations (if rotation allowed).
2. Generate a few candidate layouts: "max qty of one size", "max total value", "min waste", and any user-specified minimum quantities per size.
3. For each layout, compute leftover strip dimensions and recursively try to fit smaller sizes into the leftover.
4. Return top 2-3 layouts ranked by: waste %, and by profit (price − HPP) if price is known.

This runs server-side (or on-device) as a pure function — no ML needed, just geometry + combinatorics over a small size set (typically 4-6 sizes).

**Important:** the optimizer's output is a **suggestion**. The actual cut always gets user-confirmed/edited after physical cutting (fabric defects, human error), and that confirmed data — not the suggestion — is what the final HPP and stock entry are based on.

### 1.6 Pricing formula (for the "price advisor" feature)

```
selling_price = HPP ÷ (1 − target_margin − marketplace_fee_pct − promo_allocation_pct)
```

Margin vs markup — must be labeled unambiguously in UI:
- Margin = (price − HPP) / price
- Markup = (price − HPP) / HPP

> **Note:** profit-per-labor-minute ranking was considered as a decision metric but is deprioritized for now (not in current scope). `est_labor_minutes` stays in the schema since it's needed for HPP labor cost, but no ranking feature is built on top of it yet.

---

## 2. Screens & Flows (for Claude Design mockups)

### Bottom navigation (confirmed by mockup)
5 tabs: `Beranda` (Dashboard) · `Produksi` (the 4-tab screen below) · `Produk` (Products/Finished Goods, screen #3) · `Penjualan` (Sales, screen #4) · `Lainnya` (likely houses Reports + Settings, screens #5-6). Note that **Produk is its own bottom-nav destination, separate from the Produksi tabs** — don't nest Products inside the Produksi screen.

### Screen list

0. **Login (updated in v1.1 — was email+password, now Google SSO)** — gates every other screen; the app opens here if no valid stored JWT exists in the iOS Keychain. The screen shows: the Oura Studios brand mark (scissors icon, app name, tagline), and a single **"Sign in with Google"** button styled per Google's button design guidelines (white background, Google G icon, "Sign in with Google" label, rounded rectangle border). No email or password fields.

   **Button tap flow:**
   1. iOS presents a Google OAuth consent screen via `ASWebAuthenticationSession` (the standard system browser sheet — no external SDK required).
   2. Owner authenticates with their Google account.
   3. iOS receives a Google `id_token` in the OAuth callback URL fragment.
   4. App calls `POST /auth/google` with `{ "id_token": "<google_id_token>" }`.
   5. Backend verifies the token with Google, checks the email against `AUTHORIZED_OWNER_EMAIL`, and returns `{ access_token, expires_at }`.
   6. App stores the JWT in the **iOS Keychain** (never UserDefaults or plaintext) and navigates to Beranda.

   **Error handling:** if the backend returns 403 (unauthorized Google account), show an inline error "Akun Google ini tidak diizinkan". If the user cancels the consent screen, silently return to the login screen (no error shown). Any network error shows an inline message.

   **No password, no "Buat akun", no "Lupa password"** — these concepts don't exist in this auth model. Recovery (e.g. owner changes Google account) is handled by updating `AUTHORIZED_OWNER_EMAIL` on the backend and clearing the `owner_account` row.

   A **"Keluar" (log out)** action exists in the Lainnya tab — clears the stored Keychain token and returns to this screen.
1. **Dashboard** — today's sales, low-stock alerts for finished products (product+size below minimum stock), quick actions
2. **Produksi** (core differentiator area) — one screen, **4 tabs**: `Bahan` · `Resep` · `Optimasi` · `Produksi`. These were originally spec'd as 4 separate screens; the mockup consolidates them into one tabbed flow, which is the correct IA — keep this structure. The 4 tabs form a left-to-right working sequence (bahan masuk → resep dipakai → dioptimasi → jadi batch), but each tab is also independently accessible (e.g. to just check material stock without starting a production run).

   **Tab: Bahan** (Materials)
   - **Correction from earlier draft:** this is NOT three equal sub-tabs. `Daftar` → `Detail` is a hierarchical drill-down (list → item), and `Tambah Pembelian` is a contextual action, not a peer destination. Modeling all three as same-level tabs forces the tab order (left–middle–right) to fight the actual usage order (list → detail → add, which visually means left → right → middle) — that mismatch is what made the earlier version feel like backtracking. Fix: use push navigation for the drill-down, and a modal/sheet for the action.

   - **`Daftar`** (the only persistent view under the Bahan tab):
     - Search box at top: textbox with live autocomplete dropdown as the user types (inline, not a separate screen).
     - Picking a suggestion, or tapping any row → **pushes** a Detail screen on top (standard list-detail navigation, with a **back** button/gesture returning to Daftar — not a tab switch).
     - Typing without picking a suggestion filters the list in place.
     - A **"+" button in the header** (top-right) opens the **Tambah Pembelian modal** with the Bahan field empty/searchable — this is the entry point for logging a purchase without already having a specific material open.
     - List: name, category, unit cost, stock qty, Menipis/Rendah color coding — unchanged from before.

   - **`Detail`** (pushed screen, not a tab):
     - Scoped to one material. Shows `avg Rp X/m` badge (live `material.current_avg_cost`), **Riwayat Pembelian** and **Pergerakan Stok** sections (both reverse-chronological; tapping a consumption row jumps to that Production Batch, tapping a purchase row opens it for edit — full edit/delete behavior spec'd below, since this was previously just a one-line mention with no real spec).
     - A **"+ Tambah Pembelian" button** here opens the same modal as above, but with the Bahan field **pre-filled and locked** (small "Ubah" link to override if needed) — this is the expected default path when logging a restock for a material you're already looking at.
     - Back navigation returns to `Daftar` at its prior scroll position (don't reset the list/search).

   - **`Tambah Pembelian`** (modal/bottom sheet, presented over Daftar or Detail — not a tab):
     - Triggered only from the two "+" entry points above, never a standalone tab destination.
     - Bahan field: same searchable dropdown pattern; pre-filled+locked when opened from Detail, empty+searchable when opened from Daftar's header "+".

     - **"Buat bahan baru" flow — exact states (this is where the mockup currently overlaps two elements that should never be visible at the same time):**
       1. **Typing state:** user types in Bahan field → dropdown appears below it showing matching existing materials (if any) + a **"+ Buat bahan baru: '[nama yang diketik]'"** row at the bottom of that same dropdown list, live-updating as they type.
       2. **Tap "+ Buat bahan baru...":** the dropdown **closes completely** (it does not stay open or float above what comes next — this is the bug in the current mockup, where the dropdown suggestion and the new-material panel are stacked on top of each other). The Bahan field itself now shows the typed name in a **locked/confirmed state** (same visual treatment as an existing material being selected), with a small **"Baru"** badge next to it so it's clear this isn't an existing record.
       3. **New-material panel expands inline**, pushing the rest of the form down (not overlapping it): a **Kategori** selector (Kain / Benang / Hardware, single-select, no default — user must choose) appears directly below the confirmed Bahan field.
       4. **Conditional field by category:** if `Kategori = Kain`, a **"Lebar Kain (cm)"** field appears — this sets `material.fabric_width_cm`, a **default/typical width for this fabric**, not a hard lock (see the field-linkage note below for why it's a suggestion, not a constraint). If `Benang` or `Hardware`, no extra field appears here — Kategori alone is enough to create the material record.
       5. Once Kategori (and Lebar Kain, if Kain) is filled, the rest of the form below (dimensions, Total Biaya, Tanggal, Supplier) becomes active/scrollable into view — it was always present, just visually de-emphasized while the new-material panel is incomplete.
       - **Field linkage:** if the user just set "Lebar Kain (cm)" in step 4, the **"Lebar (cm)"** field further down in the same form **auto-fills from it as a starting suggestion** (they usually describe the same fabric) — but stays **fully editable**, since an individual purchase (e.g. a remnant or an odd cut) can legitimately be narrower than the fabric's typical width. Don't lock it.
       - This "+ Buat bahan baru" path is the **only** way new materials get created — there is no separate "add material" screen anywhere else in the app.

     - **No Potong/Roll purchase-type toggle** — this was in an earlier draft and is now removed per explicit direction: every fabric purchase is entered the same way, as one bounded **Lebar (cm) × Panjang (cm)** rectangle, whether it's a small remnant or a large bolt. This also simplifies the cutting optimizer (Section 1.5) — it no longer needs to special-case "fixed-width roll" vs "bounded piece," every purchase is just a bounded rectangle.

     - **Category-conditional fields (full spec):**

       **If Kategori = Kain:**
       - `Lebar (cm)` and `Panjang (cm)` — both plain numeric fields, no toggle, no mode switching. Pre-filled from the material's typical `fabric_width_cm` if set (see field linkage above), fully editable.
       - Common fields below: Total Biaya, Tanggal, Supplier.

       **If Kategori = Benang:**
       - `Ukuran Kemasan` toggle: **Kecil** | **Besar** — purely descriptive metadata to help the owner recognize which purchase record is which later in Riwayat Pembelian; it does not feed into HPP math, since thread stays a pooled/flat-rate cost regardless of spool size (see Section 1.3).
       - `Jumlah (gulung)` — a single numeric quantity field.
       - Common fields: Total Biaya, Tanggal, Supplier.

       **If Kategori = Hardware:**
       - `Jumlah (pcs)` — a single numeric quantity field, nothing else category-specific.
       - Common fields: Total Biaya, Tanggal, Supplier.
       - (No package-size toggle by default — most hardware like clips/rings is bought by simple piece count. Only add one for a specific real item that needs it; don't build it as a default field.)

     - **Component-level behavior — spec'd precisely so Claude Design can build these as functioning, not just visual, elements:**
       - **Numeric fields (Lebar, Panjang, Jumlah):** numeric keypad only (no letters), decimal allowed with comma as the separator per Indonesian convention (e.g. "5,2"), must be > 0 to allow saving — show inline validation (field border/text turns to an error color, small message below) rather than blocking keystrokes.
       - **Total Biaya:** numeric keypad, auto-formats live as the user types into Rupiah grouping (typing "712500" displays as "Rp 712.500" progressively, not just on blur) — this is a currency-input component, not a plain number field.
       - **Tanggal (date picker):** tapping the field opens the platform-native iOS date picker (wheel or calendar sheet, whichever matches the rest of the app's date pickers — keep consistent across the whole app, don't mix styles). Defaults to **today's date**. **Cannot select a future date** (a purchase can't happen after today) — future dates should be disabled/greyed in the picker, not just rejected after selection. Displayed format matches the rest of the app: "27 Jul 2026" (day, abbreviated month, year).
       - **Bahan and Supplier fields:** both use the identical searchable-dropdown-with-inline-create pattern (see Supplier spec directly below) — same component, reused, not two different implementations.
       - **Close ("×") button:** if any field in the sheet has been changed from its opening state, tapping "×" shows a confirm-discard prompt ("Buang perubahan?" / batal / buang) before closing — don't silently discard a half-filled purchase entry. If nothing was changed, close immediately with no prompt.
       - **"Simpan Pembelian" button:** disabled (visually muted, not tappable) until all required fields for the current category are valid. On tap: brief loading state on the button itself, then either success (sheet dismisses per the flow already described) or an inline error if the save fails (don't lose the user's entered data on failure — keep the sheet open with the error shown).

     - **Supplier field — same pattern as Bahan, now fully specified:**
       - This requires treating Supplier as its own record, not a free-text string — otherwise "Toko Kain Abadi" and "toko kain abadi" fragment into separate suppliers with no way to see combined purchase history per supplier later.
       - Same interaction as Bahan: tap/type in the Supplier field → dropdown shows matching existing suppliers as the user types, live-filtered → tap one to select. If no match, an inline **"+ Tambah supplier baru: '[nama]'"** row at the bottom of the dropdown creates a new Supplier record on tap — no separate screen, no extra fields required (just the name; unlike materials, suppliers don't need a category or dimensions to be usable).
       - Supplier is **optional** — unlike Bahan, a purchase can be saved without one (some purchases might not have a clear supplier on record).

     - **"Simpan Pembelian"** → sheet dismisses, returning to Detail (creating it via push if the sheet was opened from Daftar) with the new purchase at the top of Riwayat Pembelian and avg cost already updated. No tab-hopping at any point in this flow.

   - **Edit & Delete Pembelian (previously undocumented — full spec):**
     - **This is not simple CRUD.** A purchase's `total_cost`/`qty`/dimensions feed into `material.current_avg_cost` (a running weighted average), and once any of that purchase's fabric length has been consumed by a `CuttingLayout`, changing its dimensions retroactively would make the consumption math inconsistent. So editability depends on **whether the purchase has already been used**.
     - **Tapping a row in Riwayat Pembelian** opens the same `Tambah Pembelian` sheet UI, pre-filled with that purchase's data, titled "Edit Pembelian" instead of "Tambah Pembelian" and with a "Simpan Perubahan" button instead of "Simpan Pembelian".
     - **If the purchase is untouched** (fabric: `remaining_length_cm` still equals the original purchased length, i.e. nothing has been cut from it yet; thread/hardware: no stock_ledger consumption rows reference it) — **all fields are freely editable**, including Lebar/Panjang/Jumlah. Saving triggers a full recalculation of `material.current_avg_cost` from all that material's purchases.
     - **If the purchase has already been partially or fully consumed** — `Lebar (cm)` / `Panjang (cm)` / `Jumlah` become **locked** (greyed, not editable) with a short explanatory note ("Sudah dipakai di produksi — dimensi tidak bisa diubah"). **Total Biaya, Tanggal, and Supplier remain editable** regardless of consumption state, since correcting a mistyped price or wrong supplier doesn't affect physical stock tracking. Editing Total Biaya still triggers the avg-cost recalculation.
     - **Delete:** a trash/delete icon on the Edit Pembelian sheet (or a swipe-to-delete gesture on the Riwayat Pembelian row — pick one, be consistent with other list-delete patterns in the app).
       - **If untouched** (same condition as above): delete is allowed, behind a confirm dialog ("Hapus pembelian ini? Tindakan ini tidak bisa dibatalkan."). On confirm: removes the `MaterialPurchase` row and recalculates `material.current_avg_cost` from the remaining purchases.
       - **If already consumed at all:** delete is **disabled**, not just discouraged — removing it would orphan the `CuttingLayout`/`ProductionBatch` records that reference it and corrupt their locked HPP history. Show the same explanatory note as the dimension-lock case above, and don't offer a "force delete" escape hatch; a wrong purchase entry that's already been used should be corrected via the editable fields (cost/supplier/date) rather than removed.

   - **Pagination:** this list grows continuously (every purchase can introduce cost changes; long-running shops will have 30+ materials). Design with pagination/infinite-scroll in mind, not a single static screen — show at least 15-20 sample rows across a couple of fabric types, thread colors, and hardware types when mocking this up, so the scroll/pagination behavior is visible, not just the 6 items in the current mockup.


   **Tab: Resep** (Pattern Specs)
   - **Same structural fix as Tab Bahan:** `Daftar Resep` and `Editor` are not equal peer tabs. `Daftar Resep` is the persistent view; `Editor` is always entered *with context* (either "add new" or "edit this specific row"), never opened blank without knowing why. Use push navigation, not tab-switching, for the same reason explained in Tab: Bahan above.

   - **`Daftar Resep`** (persistent view):
     - Grouped by product name (e.g. "SCRUNCHIE", "IKAT RAMBUT" headers). Each row under a group = one **(size, fabric type)** combination — e.g. "M · Satin" and "M · Waffle" are separate rows since consumption differs by fabric (see Section 1.1–1.2). Row shows cut dimensions (W×H cm), estimated labor minutes, and **"Aktif sejak [date]"**.
     - **"Riwayat Versi" link per row** → pushes to a read-only version-history screen for that specific (size, fabric) pattern spec: a reverse-chronological list of every past version (`effective_from` → `effective_to`, dimensions, labor minutes at that time). Old versions are view-only — you can't edit history, only see what a batch from that period was costed against. No action buttons here except back.
     - Tapping the row itself (not the Riwayat Versi link) → **pushes** to `Editor`, pre-filled with that pattern spec's current active values — this is the "edit" entry point, and saving here creates a new version (see Editor behavior below).
     - **"+" button in the header** → starts the **Tambah Resep** flow (see below) — this is the only "create new" entry point, consistent with how Tambah Pembelian works in Tab Bahan.

   - **"Tambah Resep" flow — full spec, including multi-fabric selection** (the earlier draft only allowed picking one fabric at a time and left component behavior too vague for a real implementation; both are fixed below, and a full worked example is included so the flow is unambiguous end-to-end).

     - **Presentation:** one scrollable bottom sheet with numbered sections, not a multi-screen wizard. All sections are visible at once (progressive disclosure — later sections are visually de-emphasized/inactive until earlier ones are filled, but the user can scroll and see the whole shape of the form). **"Simpan Resep"** stays pinned at the bottom, disabled until every required field is valid.

     - **1. Pilih Produk — full spec & layout:**
       - **Rule:** exactly one product must be selected — this is a hard single-select, never multiple. A `PatternSpec` always belongs to one `Product` via one `ProductSize`; there's no such thing as a recipe shared across two different products.
       - **Layout:** section header "1. PILIH PRODUK" (label style matches the mockup's numbered-section convention), followed by a **horizontally-wrapping row of chips** — existing products first, in the order returned by `GET /products` (suggest: alphabetical or most-recently-used), then the **"+ Tambah Produk Baru"** chip always last in the row so it doesn't shift position as products are added over time.
       - **Chip states:**
         - *Unselected:* light/outline fill, dark text (matches "Ikat Rambut" styling in the reference screenshot).
         - *Selected:* solid filled background (matches "Scrunchie" styling in the reference screenshot) — only one chip in this row can be in this state at a time.
         - *"+ Tambah Produk Baru":* dashed border, muted text — visually distinct from real data chips so it always reads as an action, not a 3rd product choice.
       - **Tap behavior:** tapping an unselected existing chip selects it and deselects whatever was previously selected (radio-button semantics — not a toggle, you can't "deselect" down to zero once one is chosen, since Ukuran and Bahan below depend on a product being set). Tapping the already-selected chip does nothing (no-op, not a deselect).
       - **"+ Tambah Produk Baru" expansion:** tapping it does **not** navigate to a new screen or open a separate modal — it expands **inline, directly below the chip row**, pushing section 2 further down. The expansion shows: a **Nama Produk** text field (autofocaused, keyboard opens immediately), and directly below it a smaller **SKU** field that live-auto-fills as an uppercase slug of whatever's typed in Nama Produk (e.g. typing "Pouch Serut" → SKU field shows "POUCH-SERUT" automatically), with the SKU field remaining manually editable if the user wants to override it. Two small buttons close this expansion: **"Batal"** (collapses the expansion, discards the typed name, no chip is added) and **"Tambah"** (only enabled once Nama Produk is non-empty and SKU is non-empty/unique — validate SKU uniqueness against `GET /products` before enabling; show inline error under the SKU field if a duplicate is typed, e.g. "SKU sudah dipakai"). Confirming with "Tambah" collapses the expansion, inserts a new selected chip with that name into the chip row (positioned just before the "+" chip), and this new product becomes the current selection for the rest of the sheet.
       - Nothing below section 1 is enabled/visible in an interactive state until a product is selected (existing or newly created) — sections 2-4 stay visually muted/disabled, consistent with the earlier progressive-disclosure note.

     - **2. Pilih Ukuran — full spec & layout:**
       - **Rule:** exactly one size must be selected — also a hard single-select, same reasoning as Produk (one `PatternSpec` = one `ProductSize`).
       - **Layout:** identical structural pattern to section 1 — header "2. PILIH UKURAN", horizontally-wrapping chip row scoped to **only the currently-selected product's existing sizes** (re-fetch/filter this list every time the Produk selection in section 1 changes — if the user switches products after having picked a size, the size selection resets, since sizes don't carry across products).
       - **Chip contents, in order:** existing sizes for that product (in whatever order they were created, or a recognized-progression order XS→S→M→L→XL→XXL if all labels match that convention), then a **suggested next-size chip** if the existing sizes form a recognizable progression with a gap at the top (e.g. XS/S/M/L present → show "+ Buat ukuran 'XL'"; if sizes are already XS-XXL, no suggestion chip appears since there's no obvious next size), then a plain **"+ Ukuran lain..."** chip last.
       - **Selected/unselected chip states:** identical visual treatment to section 1's chips (filled = selected, outline = unselected).
       - **"+ Buat ukuran '[X]'" (the auto-suggested one):** single tap immediately creates and selects that size — no further text entry needed, since the label is already fully determined by the suggestion.
       - **"+ Ukuran lain..." expansion:** tapping it expands inline below the chip row (same pattern as Produk's "+ Tambah Produk Baru") with a single **Label Ukuran** text field (e.g. for entering "Kecil", "Sedang", "XXXL", or any custom label) and **"Batal"**/**"Tambah"** buttons, same validation pattern (non-empty, and unique within this product's existing sizes — reject "M" if "M" already exists for this product, with inline error "Ukuran ini sudah ada").
       - Sections 3-4 stay disabled/muted until a size is selected, same progressive-disclosure rule as before.


     - **3. Pilih Bahan (Kain) — full spec & layout, multi-select search+dropdown (this is the part that most needs precise spec):**
       - **Rule:** one *or more* fabrics may be selected — this is the one field in the whole sheet that's intentionally multi-select, because a single Tambah Resep session commonly needs to define the recipe for more than one fabric at once (e.g. Satin *and* Waffle for the same new size). Do not build this as a single-select chip row like sections 1-2 above.
       - **Layout:** section header "3. PILIH BAHAN (KAIN)", followed by **one field** (not a chip row) that looks like a text input but behaves as a tokenized multi-select — visually: a rounded-rectangle container matching the other text-input fields in this app (same border/fill as e.g. the Lebar/Panjang fields elsewhere), tall enough to wrap to 2+ lines once several tokens are added. A small search icon at the field's left edge signals it's searchable, not just typed free text.
       - This field is a **tokenized multi-select search input** (the same interaction family as an email "To:" field with recipient chips), not single-select chips.
       - **Exact interaction sequence:**
         1. User taps the Bahan field → keyboard opens, an empty text input is focused (any already-selected fabrics show as small removable chips/tokens to the left inside the same field, e.g. "Satin Putih ×").
         2. User types (e.g. "sat") → a dropdown list appears directly below the field, live-filtered to matching **Kain** materials that already have at least one purchase on record (same eligibility rule as before — a recipe can't be defined against a fabric with no cost history). Materials already selected as tokens are excluded from this list (or shown greyed/disabled) so they can't be picked twice.
         3. Tapping a result in the dropdown **adds it as a token/chip inside the field** (does not close the field or navigate away) and **clears the text input**, which stays focused so the user can immediately type the next fabric name. This repeats for as many fabrics as needed.
         4. Tapping the "×" on any existing token removes just that fabric.
         5. If the typed text matches no existing (purchased) material, show a short inline note ("Bahan belum ditemukan — tambahkan dulu di Tab Bahan") instead of a "+ Buat baru" option — fabric creation is intentionally not available inline here, same rule as the single-select version had, now just restated for the multi-select case.
       - **Minimum 1 fabric required** to proceed to section 4; no maximum.

     - **4. Detail Resep — one card per selected fabric (this is the section that changes shape based on step 3, and needs the most explicit spec for Claude Code to get right):**
       - If 2 fabrics were selected in step 3 (e.g. "Satin Putih" and "Waffle Biru Dongker"), this section renders **2 separate cards**, each headed by that fabric's name, each fully independent with its own: Lebar Potong (cm), Tinggi Potong (cm), Rotasi Diizinkan toggle, Komponen Hardware list (add/remove rows), Estimasi Waktu Kerja (menit). This mirrors why fabric-specific recipes exist in the first place (Section 1.2) — satin and waffle behave differently when cut, so their dimensions are never shared, even though they're being defined in the same sitting.
       - **Convenience action, not a default:** below the first card, offer a small **"Salin Komponen Hardware ke bahan lain"** button — hardware (e.g. "Ring Elastis 3mm × 1 pcs") is the one part that's usually identical across fabrics for the same size, so this copies just the Komponen Hardware rows from card 1 into any other cards that are still empty. It never touches Lebar/Tinggi/Rotasi/Waktu Kerja, since those are exactly the values expected to differ per fabric — auto-copying them would undermine the reason multi-fabric cards exist.
       - Each card validates independently; **"Simpan Resep" stays disabled until every card's required fields are filled**, not just the first one.

     - **Save behavior (client-side batching — spec'd explicitly since the backend has one `PatternSpec` per fabric, not a bulk endpoint):** tapping **"Simpan Resep"** issues one `POST /pattern-specs` call per card (see Section 4 API contract) — e.g. 2 fabrics selected → 2 calls, one for Satin's card and one for Waffle's card, each carrying that card's own dimensions/rotation/hardware/labor-minutes plus the shared `product_size_id`. Show one combined loading state while all calls are in flight. **If all succeed:** dismiss the sheet, return to `Daftar Resep` with both new rows visible under the product group. **If some fail and some succeed:** don't discard the whole sheet — keep it open, mark which card(s) failed with an inline error, keep the successful card(s) visually confirmed, and let the user retry just the failed one(s) rather than re-entering everything.

     - **Contextual pre-fill:** if "+ Tambah Resep" is opened while already viewing a specific pattern spec (e.g. from the "Scrunchie · M · Satin" Editor, or from a row in Daftar Resep), **Produk and Ukuran pre-select to that context** and show as already-chosen (e.g. "M (sudah ada)" highlighted) — the common real case is "same product and size, just need recipes for one or more different fabrics," so only Bahan (step 3) and Detail Resep (step 4) need fresh input. Opening "+ Tambah Resep" from the bare `Daftar Resep` list (not from within a specific spec) starts with nothing pre-selected.

     - **Worked example, to make the whole flow concrete:** adding a brand-new size to an existing product, for two fabrics at once —
       1. Pilih Produk: tap the existing **"Scrunchie"** chip.
       2. Pilih Ukuran: existing sizes show as chips (XS, S, M, L, XL); tap **"+ Buat ukuran 'XXL'"** (auto-suggested since XL is the current largest) → confirms **"XXL"** as a new ProductSize.
       3. Pilih Bahan: type "sat" → tap **"Satin Putih"** from the dropdown (becomes a token) → type "waf" → tap **"Waffle Biru Dongker"** (becomes a second token). Field now shows two chips: "Satin Putih ×" "Waffle Biru Dongker ×".
       4. Detail Resep shows two cards:
          - **Satin Putih card:** Lebar Potong `22`, Tinggi Potong `18`, Rotasi Diizinkan on, Komponen Hardware: "Ring Elastis 3mm × 1 pcs", Estimasi Waktu Kerja `12` menit.
          - **Waffle Biru Dongker card:** Lebar Potong `19`, Tinggi Potong `17`, Rotasi Diizinkan on, Komponen Hardware: (tap "Salin Komponen Hardware ke bahan lain" → "Ring Elastis 3mm × 1 pcs" appears here too), Estimasi Waktu Kerja `12` menit.
       5. Tap **"Simpan Resep"** → two `POST /pattern-specs` calls fire (XXL·Satin, XXL·Waffle) → both succeed → sheet dismisses → `Daftar Resep` now shows two new rows under "SCRUNCHIE": "XXL · Satin" and "XXL · Waffle".

     - **Component-level behavior — spec'd so both Claude Design (visual states) and Claude Code (functional implementation) build the same thing:**
       - **Chip/pill single-select (Produk, Ukuran):** tap toggles selection; selecting a new chip in the same group visually deselects the previous one (radio-button semantics, not checkbox). The "+" chip is visually distinct (dashed border or lighter fill, matching the existing mockup convention) from real data chips.
       - **Tokenized multi-select search field (Bahan):** functionally a combobox + token list, not a plain `<select>`. Needs: a text input that stays mounted and focused after each token is added (don't dismiss keyboard or blur), a dropdown/listbox positioned directly below the input that updates on every keystroke (debounce ~200-300ms if hitting a live search endpoint), token chips rendered inline within the same input's bounding box (wrapping to a second line if needed, input continues after the last token), and a per-token "×" tap target large enough for touch (not a tiny inline glyph).
       - **Numeric fields (Lebar Potong, Tinggi Potong, Estimasi Waktu Kerja):** reuse the exact same numeric-input component already spec'd under Tab Bahan → Tambah Pembelian's "Component-level behavior" (numeric keypad, comma decimal, must be > 0) — do not build a second implementation with different behavior.
       - **Rotasi Diizinkan:** standard on/off switch/toggle component, default **on** (matches `pattern_spec.rotation_allowed DEFAULT true`).
       - **Komponen Hardware — see the dedicated "Tambah Hardware — full spec & layout" block right after this list for complete detail (row layout, picker behavior, validation).** Summary: each fabric card's Komponen Hardware section is an add/remove list of {material, qty} rows, reusing the same search+dropdown picker pattern as Bahan but single-select per row.
       - **"Salin Komponen Hardware ke bahan lain" button:** only appears once at least 2 fabric cards exist and the first card has at least 1 hardware component filled; disabled/hidden otherwise. A single tap, not a toggle — running it twice after further edits just re-copies the current state of card 1 into the others (overwriting, with a brief confirm if the target cards already have their own entries, so a copy doesn't silently clobber manually-entered data).

     - **Tambah Hardware — full spec & layout (this was previously a single vague bullet; here's the complete technical doc for Claude Code and Claude Design):**
       - **Where this lives:** inside **each fabric card** in section 4 (Detail Resep) — not a separate top-level section of the sheet. If 2 fabrics are selected, there are 2 independent Komponen Hardware lists, one per card, since hardware needs can differ by fabric too (rare, but e.g. a lined satin variant might need an extra snap that an unlined waffle variant doesn't).
       - **Section layout within a card, top to bottom:**
         1. A small label **"KOMPONEN HARDWARE"** (matches the muted all-caps label style used for "LEBAR POTONG (CM)" etc. elsewhere in the same card).
         2. **Zero or more hardware rows** (see row layout below), stacked vertically, each separated by a thin divider line (same divider style as the rest of the card's fields).
         3. An **"+ Tambah Komponen"** row at the bottom of the list — full-width, dashed border, centered text, matching the visual style already used for "+ Tambah Komponen" in the reference Editor screenshot. This stays pinned as the last item in the list — new rows insert above it, never below.
       - **Empty state:** if zero hardware rows exist yet, the section shows just the label and the "+ Tambah Komponen" row (no "no components" placeholder text needed — the add row itself communicates the empty state clearly enough). This is a valid, save-able state — hardware is optional (see validation below).
       - **Row layout (each existing hardware row), left to right:**
         - **Material picker** (left, flexible width — takes up most of the row): shows the selected hardware material's name once picked (e.g. "Ring Elastis 3mm"), or a muted placeholder "Pilih hardware..." before anything is picked. Tapping it opens the **same search+dropdown pattern as the Bahan field** (search box appears, typing filters a dropdown of Hardware-category materials with at least one purchase on record — same eligibility rule as fabric), but this picker is **single-select per row** (not tokenized/multi-select like Bahan) — picking one result fills this row's material and closes the dropdown, it does not add a token, since a hardware row is exactly one material.
         - **Qty field** (right, fixed narrow width, e.g. "× 1 pcs" as shown in the mockup): reuses the same numeric-input component spec'd elsewhere (numeric keypad, must be > 0), with the unit ("pcs") shown as a fixed suffix since hardware is always counted in whole pieces.
         - **Remove affordance** (far right, small "×" or trash icon, minimum comfortable touch target ~44×44pt): tapping it removes this row immediately — no confirmation needed, since re-adding a row is trivial and this isn't a destructive action against saved data (the whole sheet hasn't been submitted yet).
       - **"+ Tambah Komponen" tap behavior:** appends a new empty row (picker shows placeholder, qty field empty) directly above the "+ Tambah Komponen" row itself, and immediately opens that new row's material picker (auto-focus) so the user can start typing right away without an extra tap.
       - **Duplicate prevention:** once a material is picked in one row, it's excluded from the dropdown in every other row within the *same card* (a recipe shouldn't list "Ring Elastis 3mm" twice with two different quantities — if more is needed, that's one row with a higher qty). This exclusion is per-card only — the same hardware material can appear in both the Satin card and the Waffle card independently, since those are entirely separate recipes.
       - **Validation:** a row with a picked material but empty/zero qty is invalid (blocks "Simpan Resep", inline error under that row's qty field). A row that's still at the placeholder/unpicked state is only invalid if the user tries to leave it half-filled (e.g. qty typed but no material picked) — an untouched empty row from "+ Tambah Komponen" that's simply removed again causes no error. Zero hardware rows total is valid (optional, as stated above).



   - **`Editor`** (pushed screen, entered only via a tapped row in `Daftar Resep` — never a freestanding tab, and distinct from the "Tambah Resep" sheet above):
     - Header shows context: "Scrunchie · M · Satin" (as in the mockup).
     - Same fields as "Detail Resep" above: Lebar Potong (cm), Tinggi Potong (cm), Rotasi Diizinkan toggle, Komponen Hardware list (add/remove), Estimasi Waktu Kerja (minutes) — plus Produk/Ukuran/Bahan shown as fixed context (not editable — changing any of those means a different pattern spec, i.e. use Tambah Resep instead).
     - **Behavior now depends on whether this version has been used yet** (refined during the CRUD audit in Section 5 — see `POST /pattern-specs`):
       - **If zero production batches have used this version:** it's a plain correction — no dotted-box warning, button reads **"Simpan Perubahan"**, and saving updates the same row (no new version number, no change to "Aktif sejak" date).
       - **If at least one production batch has used this version:** show the dotted-box notice exactly as in the mockup: *"Resep aktif sejak [date] — batch produksi sebelumnya tetap memakai biaya versi lama."* Button reads **"Simpan Versi Baru"**, and saving creates a new version as originally designed.
     - A **delete option** is available only in the first case (zero batches used) — see Section 5's `DELETE /pattern-specs/{id}`; once any batch has used a version, it can only be superseded by a new version, never deleted.

   - **Pagination:** grows with every SKU × size × fabric-type combination (e.g. 2 products × 5 sizes × 2 fabrics = 20 rows already). Mock with enough rows to require scrolling within the grouped list, and to exercise the "Muat Lebih Banyak" pattern already shown in the mockup.

   **Tab: Optimasi** (Cutting Optimizer)
   - Select a **Pembelian Bahan** (a specific MaterialPurchase — shown with material name, dimensions, cost, purchase date) as the input roll/piece to optimize.
   - **Ukuran Kandidat**: chip selector per size showing that size's minimum-qty constraint if the user wants to guarantee a floor (e.g. "M · min 60" meaning "give me at least 60 M's, optimize the rest"; "S · min –" meaning no floor set, purely optimizer's choice). Chips should be toggleable/editable, not just static labels — tapping one lets the user set/clear its min-qty constraint before running.
   - `Jalankan Optimasi` button → calls `POST /cutting-optimizer/suggest`, returns candidate layouts (e.g. "Waste Minimum", "Max Qty", "Max Profit" strategies) for the user to compare and pick one, which then creates a `CuttingLayout` via `POST /cutting-optimizer/layouts`.
   - This tab's output feeds directly into the Produksi tab below — should be a clear next-step affordance (e.g. "Lanjut ke Produksi") once a layout is chosen, not a dead-end screen.

   **Tab: Produksi** (Production Batch Confirm)
   - Header shows draft provenance: **"Draft · dari Layout '[strategy name]'"** — e.g. "dari Layout 'Waste Minimum'" — so the user always knows which optimizer suggestion this batch came from (or "Manual" if no optimizer was used).
   - **Qty Aktual per Ukuran**: one row per (product, size) in this batch, showing the optimizer's suggested qty struck through, with an editable actual-qty field next to it — this is where defects/miscounts get corrected before locking.
   - **Rincian HPP** (per size, shown for the size currently selected/expanded): stacked bar + legend breaking down Kain (fabric), Bahan Pooled (thread), Hardware, Tenaga Kerja (labor, with minutes shown), Overhead, and HPP Total. This is the "trust-building" breakdown from Section 1.4 — always show all 5 components, never just the total.
   - `Konfirmasi & Tambah ke Stok` button → calls `POST /production-batches/{id}/confirm`. Must show the warning text ("Setelah dikonfirmasi, biaya batch ini terkunci dan tidak dapat diubah") before/at this action, since this is an irreversible cost-lock, not just a stock update.

3. **Products (Finished Goods)**
   - List of SKU+size with current stock qty, current HPP (latest batch), selling price, margin %
   - Each product+size row/detail is editable for **minimum stock (`reorder_min_qty`)** — this is what Dashboard low-stock alerts compare against. Suggest exposing it as a small inline-editable field next to stock qty (e.g. tap "Min: 20 pcs" → edit), rather than burying it in a separate settings screen, since it's a per-size value, not global.
   - Price Advisor: slider for target margin + marketplace fee % + promo % → suggested price
   - **Pagination:** same pattern as Bahan/Resep — mock with enough SKU×size rows (10+) to show scroll/pagination.
4. **Sales**
   - New sale: pick product(s)+qty, discount, payment method → auto-deducts stock, computes profit using HPP at time of sale
   - Sales history / invoice list — this one especially needs realistic pagination in mockups, since sales entries accumulate daily; show at least 20-30 sample invoices spanning several days/weeks so date-grouping and pagination/infinite-scroll are both visible.
5. **Reports**
   - Sales by period, best/worst margin products, stock card (kartu stok) per product, waste rate by material, low-stock alerts (finished products)
6. **Settings**
   - Labor rate/minute, overhead rate, pooled material rates (thread etc.), marketplace fee defaults

### Sample data & pagination — general note for Claude Design
Several screens above (Bahan, Resep, Products, Sales) are lists that will realistically grow well past what fits on one screen. When generating mockups, use enough sample rows (15-30, varied dates/values/categories) to actually exercise scrolling and pagination UI (e.g. "load more", numbered pages, or infinite scroll — pick one pattern and use it consistently across all list screens), rather than the 2-6 item examples used earlier in this doc for illustration. The 2-6 item examples in Section 1 and the screenshots referenced during design review are illustrative only, not the target data volume for final mockups.

### Key UX principles to carry into mockups
- The **Optimasi tab** (within Produksi) is the hero feature — give it the most visual/design attention (a simple 2D layout preview, even schematic, helps trust the numbers).
- Always show HPP breakdown (fabric / hardware / labor / overhead) stacked, not just a total — builds user trust in the number.
- Distinguish clearly in UI copy between "suggested/estimated" values (pre-confirm) and "actual/locked" values (post-confirm) — different visual treatment (e.g. dashed vs solid, "draft" badge). The mockup's strikethrough-suggested / editable-actual pattern in the Produksi tab is a good implementation of this — keep it.
- Margin vs markup must never be ambiguous — always label explicitly.

---

## 3. Database Schema

```sql
-- ===== AUTH (updated v1.1 — Google SSO replaces email+password) =====

CREATE TABLE owner_account (
    id          UUID PRIMARY KEY,
    google_sub  TEXT NOT NULL UNIQUE,   -- Google's stable user ID ('sub' claim from the verified ID token)
    email       TEXT NOT NULL UNIQUE,   -- from Google, for display/logging only — not the auth lookup key
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Still a single-row-in-practice table. No password_hash column — auth is fully delegated to Google.
-- Row is auto-provisioned on the owner's FIRST successful Google Sign-In (when the verified email matches
-- the AUTHORIZED_OWNER_EMAIL env var). On subsequent sign-ins, the backend verifies google_sub matches
-- the existing row (google_sub is stable; email can change in Google, so don't use email as lookup key).
-- No roles/permissions table: every authenticated request has full access to everything.
-- Recovery (owner changes Google account): update AUTHORIZED_OWNER_EMAIL in env + DELETE the owner_account
-- row — the next sign-in will re-provision with the new google_sub.

-- MIGRATION NOTE (v1.0 → v1.1):
-- DROP COLUMN password_hash FROM owner_account (if table already exists from v1.0)
-- ADD COLUMN google_sub TEXT NOT NULL UNIQUE
-- (or rebuild via Alembic — see alembic/versions/ for the migration file)

-- ===== MATERIALS =====

CREATE TABLE material (
    id              UUID PRIMARY KEY,
    name            TEXT NOT NULL,              -- "Satin Putih", "Benang Merah"
    category        TEXT NOT NULL,               -- 'fabric' | 'thread' | 'hardware' | 'packaging'
    cost_class      TEXT NOT NULL,               -- 'direct_precise' | 'direct_pooled'
    purchase_unit   TEXT NOT NULL,               -- 'meter' | 'roll' | 'pack' | 'pcs'
    usage_unit      TEXT NOT NULL,               -- 'cm' | 'cm2' | 'pcs'
    fabric_width_cm NUMERIC,                     -- nullable, only for category='fabric': typical/default width, used to pre-fill (not lock) new purchase entries
    current_avg_cost NUMERIC NOT NULL DEFAULT 0, -- weighted average cost per usage_unit
    reorder_min_qty NUMERIC,
    is_archived     BOOLEAN NOT NULL DEFAULT false, -- soft-delete: see CRUD audit in Section 6
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE supplier (
    id          UUID PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE material_purchase (
    id              UUID PRIMARY KEY,
    material_id     UUID NOT NULL REFERENCES material(id),
    width_cm        NUMERIC,          -- fabric only ("Lebar (cm)") — every fabric purchase is a bounded rectangle, no piece/roll distinction
    length_cm       NUMERIC,          -- fabric only ("Panjang (cm)")
    qty             NUMERIC,          -- thread/hardware only: jumlah gulung / jumlah pcs
    package_label   TEXT,             -- thread only, optional: 'Kecil' | 'Besar' or similar — descriptive only, not used in cost calc
    total_cost      NUMERIC NOT NULL,
    supplier_id     UUID REFERENCES supplier(id),   -- nullable, optional field
    purchased_at    DATE NOT NULL,
    remaining_length_cm NUMERIC,     -- decremented as CuttingLayouts consume it (fabric only)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ===== PRODUCTS & RECIPES =====

CREATE TABLE product (
    id          UUID PRIMARY KEY,
    sku         TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,           -- "Scrunchie"
    is_archived BOOLEAN NOT NULL DEFAULT false, -- soft-delete: see CRUD audit in Section 6
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE product_size (
    id                  UUID PRIMARY KEY,
    product_id          UUID NOT NULL REFERENCES product(id),
    size_label          TEXT NOT NULL,           -- 'XS','S','M','L','XL','XXL'
    fabric_variant_name TEXT,                    -- nullable (v1.7); e.g. 'Satin Pelangi', 'Waffle Merah'
                                                 -- NULL = no fabric-specific variant. NULL treated as distinct value.
    reorder_min_qty     NUMERIC,                 -- minimum finished-goods stock before low-stock alert triggers
    is_archived         BOOLEAN NOT NULL DEFAULT false,
    UNIQUE(product_id, size_label, fabric_variant_name)
    -- v1.7: changed from UNIQUE(product_id, size_label). Backend must enforce and return 409 on duplicate.
);

-- Recipe: keyed by (product_size, fabric material) since consumption differs per fabric type.
-- Versioned: don't hard-delete, deactivate + insert new row, so historical batches keep original costing basis.
CREATE TABLE pattern_spec (
    id                  UUID PRIMARY KEY,
    product_size_id     UUID NOT NULL REFERENCES product_size(id),
    fabric_material_id  UUID NOT NULL REFERENCES material(id),
    cut_width_cm        NUMERIC NOT NULL,
    cut_height_cm       NUMERIC NOT NULL,
    rotation_allowed    BOOLEAN NOT NULL DEFAULT true,
    est_labor_minutes   NUMERIC NOT NULL,
    is_active           BOOLEAN NOT NULL DEFAULT true,
    effective_from      TIMESTAMPTZ NOT NULL DEFAULT now(),
    effective_to        TIMESTAMPTZ
);

-- Non-fabric components needed per product_size (hardware: clips, rings; direct_precise class)
CREATE TABLE pattern_component (
    id                  UUID PRIMARY KEY,
    pattern_spec_id     UUID NOT NULL REFERENCES pattern_spec(id),
    material_id         UUID NOT NULL REFERENCES material(id),
    qty_per_unit        NUMERIC NOT NULL
);

-- ===== CUTTING OPTIMIZER =====

CREATE TABLE cutting_layout (
    id                  UUID PRIMARY KEY,
    material_purchase_id UUID NOT NULL REFERENCES material_purchase(id),
    status              TEXT NOT NULL DEFAULT 'suggested', -- 'suggested' | 'used' | 'discarded'
    waste_pct           NUMERIC,
    total_fabric_cost   NUMERIC NOT NULL,   -- cost allocated across this layout (usually = purchase total_cost)
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE cutting_layout_item (
    id                  UUID PRIMARY KEY,
    cutting_layout_id   UUID NOT NULL REFERENCES cutting_layout(id),
    product_size_id     UUID NOT NULL REFERENCES product_size(id),
    pattern_spec_id     UUID NOT NULL REFERENCES pattern_spec(id),
    orientation         TEXT NOT NULL,      -- 'normal' | 'rotated'
    qty_suggested       INTEGER NOT NULL,
    fabric_length_used_cm NUMERIC NOT NULL, -- length of roll consumed for this row/group
    cost_per_piece      NUMERIC NOT NULL    -- derived: (share of total_fabric_cost) / qty_suggested
);

-- ===== PRODUCTION =====

CREATE TABLE production_batch (
    id                  UUID PRIMARY KEY,
    cutting_layout_id   UUID REFERENCES cutting_layout(id), -- nullable: batch can exist without optimizer (manual entry)
    produced_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    status              TEXT NOT NULL DEFAULT 'draft',      -- 'draft' | 'confirmed'
    notes               TEXT
);

CREATE TABLE production_batch_item (
    id                  UUID PRIMARY KEY,
    production_batch_id UUID NOT NULL REFERENCES production_batch(id),
    product_size_id     UUID NOT NULL REFERENCES product_size(id),
    pattern_spec_id     UUID NOT NULL REFERENCES pattern_spec(id),
    qty_actual          INTEGER NOT NULL,        -- user-confirmed, may differ from cutting_layout_item.qty_suggested
    hpp_fabric          NUMERIC NOT NULL,
    hpp_pooled_material NUMERIC NOT NULL,        -- thread etc.
    hpp_hardware        NUMERIC NOT NULL,
    hpp_labor           NUMERIC NOT NULL,
    hpp_overhead        NUMERIC NOT NULL,
    hpp_total           NUMERIC NOT NULL         -- sum of above; locked once batch confirmed
);

-- ===== STOCK =====

CREATE TABLE stock_ledger (
    id                  UUID PRIMARY KEY,
    product_size_id     UUID NOT NULL REFERENCES product_size(id),
    change_qty          INTEGER NOT NULL,        -- + for production/return, - for sale/damage
    reason              TEXT NOT NULL,           -- 'production' | 'sale' | 'adjustment' | 'damage' | 'return'
    ref_type            TEXT,                    -- 'production_batch' | 'sales_order'
    ref_id              UUID,
    unit_hpp_snapshot   NUMERIC,                 -- HPP at time of this movement (for COGS on sale)
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ===== SALES =====

CREATE TABLE sales_order (
    id              UUID PRIMARY KEY,
    invoice_no      TEXT NOT NULL UNIQUE,
    customer_name   TEXT,
    payment_method  TEXT,                        -- 'cash' | 'transfer' | 'qris' | 'marketplace'
    marketplace_fee_pct NUMERIC DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'unpaid', -- 'unpaid' | 'paid'
    sold_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE sales_order_item (
    id                  UUID PRIMARY KEY,
    sales_order_id      UUID NOT NULL REFERENCES sales_order(id),
    product_size_id      UUID NOT NULL REFERENCES product_size(id),
    qty                  INTEGER NOT NULL,
    unit_price           NUMERIC NOT NULL,
    discount             NUMERIC NOT NULL DEFAULT 0,
    unit_hpp_snapshot    NUMERIC NOT NULL,        -- pulled from stock_ledger at sale time
    line_profit          NUMERIC NOT NULL         -- (unit_price - discount - unit_hpp_snapshot) * qty
);

-- ===== SETTINGS =====

CREATE TABLE settings (
    key         TEXT PRIMARY KEY,   -- 'labor_rate_per_minute' | 'default_overhead_per_unit' | 'pooled_material_rate:thread' etc.
    value       NUMERIC NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## 4. API Contract

Base path: `/api/v1`. All bodies JSON. **Auth is required on every endpoint below except `/auth/google`** — see Section 0 for why. Every request other than the Google sign-in exchange must carry `Authorization: Bearer <token>`; a missing/invalid/expired token returns 401.

### Auth (updated v1.1)

```
POST   /auth/google
  body: { "id_token": "<google_id_token>" }
  → returns { access_token, expires_at } (app-issued JWT bearer token)

  Backend verification steps (must happen in this order):
  1. Verify the id_token is authentic and not expired using Google's public keys.
     Recommended: google-auth-library (Python):
       from google.oauth2 import id_token as google_id_token
       from google.auth.transport import requests as google_requests
       payload = google_id_token.verify_oauth2_token(
           id_token, google_requests.Request(), GOOGLE_CLIENT_ID
       )
     This verifies signature, expiry, and aud claim in one call.
  2. Verify payload['aud'] == GOOGLE_CLIENT_ID env var (the iOS OAuth client ID).
     This prevents tokens issued to other apps being replayed here.
  3. Extract payload['sub'] (stable Google user ID) and payload['email'].
  4. Check payload['email'] against AUTHORIZED_OWNER_EMAIL env var → 403 Forbidden if no match.
  5. Upsert owner_account:
       - If no row exists: INSERT (google_sub=payload['sub'], email=payload['email'])
         First-time provisioning — the owner's account is created automatically on first sign-in.
       - If a row exists: verify stored google_sub == payload['sub'] → 403 if mismatch
         (blocks a different Google account whose email happens to match AUTHORIZED_OWNER_EMAIL).
  6. Issue an app-level JWT (signed with JWT_SECRET, not Google's key) containing owner_account.id.
     Recommended payload: { sub: owner_id, iat: now, exp: now + 30 days }
     Return { "access_token": "<jwt>", "expires_at": "<iso8601 timestamp>" }

  Error responses:
  → 400  id_token missing or malformed (fails to parse before verification)
  → 401  Google reports token invalid, expired, or wrong audience
  → 403  Verified email doesn't match AUTHORIZED_OWNER_EMAIL, or google_sub conflicts with stored row

  Required environment variables:
    AUTHORIZED_OWNER_EMAIL   The owner's Google email (e.g. owner@gmail.com)
    GOOGLE_CLIENT_ID         The iOS OAuth 2.0 client ID from Google Cloud Console
                             (used to verify the 'aud' claim — must match what the iOS app sends)
    JWT_SECRET               Secret key for signing app-level JWTs (min 32 chars, random)

  Python dependency: google-auth>=2.0 (add to requirements.txt)
    pip install google-auth

  Token lifetime: 30-day expiry recommended. No refresh token needed for a single-owner app.
  Owner re-authenticates via Google when the app-JWT expires. See Section 6 item 5.
```

### Materials

```
POST   /materials
GET    /materials
GET    /materials/{id}
PATCH  /materials/{id}

POST   /materials/{id}/purchases
  body: { width_cm?, length_cm?, qty?, package_label?, total_cost, supplier_id?, supplier_name?, purchased_at }
  -- fabric materials: width_cm + length_cm required (plain bounded rectangle, no purchase-type distinction), qty/package_label omitted
  -- thread materials: qty required, package_label optional, width_cm/length_cm omitted
  -- hardware materials: qty required, width_cm/length_cm/package_label omitted
  -- supplier: pass supplier_id if an existing supplier was picked from the dropdown; pass supplier_name instead to create
     a new Supplier inline (same "+ Tambah supplier baru" pattern as material creation); omit both if left blank (optional)
  → creates MaterialPurchase, updates material.current_avg_cost (weighted average)

GET    /materials/{id}/purchases

PATCH  /materials/{id}/purchases/{purchase_id}
  body: { width_cm?, length_cm?, qty?, total_cost?, supplier_id?, supplier_name?, purchased_at? }
  -- if the purchase is untouched (remaining_length_cm == original length, or no consumption for thread/hardware):
     all fields may be updated.
  -- if the purchase has any recorded consumption: width_cm/length_cm/qty are rejected (400) if present in the
     body — only total_cost, supplier_id/supplier_name, purchased_at may be changed.
  → recalculates material.current_avg_cost from all purchases after saving

DELETE /materials/{id}/purchases/{purchase_id}
  -- allowed only if untouched (same condition as above); returns 409 Conflict if the purchase has any
     recorded consumption (referenced by a CuttingLayout or stock_ledger entry), since deleting it would
     orphan locked production-batch cost history.
  → on success, recalculates material.current_avg_cost from remaining purchases
```

### Suppliers

```
POST   /suppliers
  body: { name }
  → creates Supplier. Also creatable inline via material_purchase's supplier_name shortcut above — this
    standalone endpoint exists for cases where Claude Design wants a direct create action outside that flow.

GET    /suppliers?search=
  → for the Supplier field's autocomplete dropdown, same pattern as material search
```

### Pattern Specs (Recipes)

```
POST   /pattern-specs
  body: { product_size_id, fabric_material_id, cut_width_cm, cut_height_cm,
          rotation_allowed, est_labor_minutes, components: [{material_id, qty_per_unit}] }
  → if no active spec exists yet for this (product_size_id, fabric_material_id): creates it directly
  → if an active spec exists and has zero ProductionBatchItem rows against it: updates that same row in place
  → if an active spec exists and has at least one ProductionBatchItem row against it: deactivates it and
    inserts a new versioned row (the original "always version forward" behavior)
  -- see Section 5's CRUD audit for why the middle case (in-place update) was added
  -- validation: fabric_material_id and every components[].material_id must reference a Material with at least
     one MaterialPurchase on record (no cost basis = can't compute HPP). The Bahan/Hardware picker in the
     Tambah Resep UI should only list materials satisfying this, per Section 2's "must already have a purchase
     on record" rule.

GET    /pattern-specs?product_id=&size=&fabric_material_id=
GET    /pattern-specs/{id}
```

### Cutting Optimizer

```
POST   /cutting-optimizer/suggest
  body: {
    material_purchase_id,
    candidates: [ { product_size_id, pattern_spec_id, min_qty? } ]
  }
  → runs shelf-packing heuristic, returns candidate layouts (not persisted yet)
  response: {
    layouts: [
      {
        strategy: "max_qty" | "min_waste" | "max_profit",
        waste_pct: number,
        items: [
          { product_size_id, pattern_spec_id, orientation, qty_suggested,
            fabric_length_used_cm, cost_per_piece }
        ]
      }
    ]
  }

POST   /cutting-optimizer/layouts
  body: { material_purchase_id, chosen from one of the suggested layouts (items array) }
  → persists as CuttingLayout + CuttingLayout_items, status='suggested'
  → returns cutting_layout_id

POST   /cutting-optimizer/layouts/{id}/discard
```

### Production

```
POST   /production-batches
  body: { cutting_layout_id? }
  → creates draft ProductionBatch, pre-filled items from layout if provided (qty_actual = qty_suggested initially)

PATCH  /production-batches/{id}/items/{item_id}
  body: { qty_actual }
  → user edits actual qty after physical cutting

POST   /production-batches/{id}/confirm
  → computes hpp_fabric / hpp_pooled_material / hpp_hardware / hpp_labor / hpp_overhead / hpp_total per item
    using: cutting_layout_item.cost_per_piece (fabric), settings pooled rates, pattern_component costs,
           est_labor_minutes × labor_rate_per_minute, overhead settings
  → writes stock_ledger rows (reason='production', unit_hpp_snapshot = hpp_total)
  → decrements material_purchase.remaining_length_cm and hardware material stock
  → sets production_batch.status = 'confirmed' (immutable after this)

GET    /production-batches/{id}
GET    /production-batches?status=
```

### Products / Stock

```
POST   /products
  body: { name, sku? }   -- sku auto-suggested from name (slug) if omitted, but must end up unique
  → creates Product. This is the only path new product types (e.g. "Pouch") get created — triggered from the "Tambah Resep" flow's "+ Buat produk baru" step, not a standalone product-creation screen.

POST   /products/{sku}/sizes
  body: { size_label, fabric_variant_name?, reorder_min_qty? }
  -- fabric_variant_name (v1.7): optional. Multiple sizes can share size_label as long as fabric_variant_name differs.
  -- Uniqueness: (product_id, size_label, fabric_variant_name) — return 409 if duplicate.
  → creates ProductSize. Also auto-created by TambahResepSheet when a (sizeLabel + fabric) combo doesn't exist.

GET    /products
GET    /products/{sku}/sizes
  -- response: array of ProductSize objects, each including fabric_variant_name (nullable)
  -- client computes display label: fabric_variant_name != null ? "{size_label} · {fabric_variant_name}" : size_label

GET    /products/{sku}/sizes/{sizeId}
  -- (v1.7) path param is sizeId: UUID — not sizeLabel: String — required because multiple sizes share the same label
  response includes: current_stock_qty, reorder_min_qty, latest_hpp_breakdown, selling_price, margin_pct, fabric_variant_name

PATCH  /products/{sku}/sizes/{sizeId}
  -- (v1.7) path param is sizeId: UUID
  body: { selling_price?, reorder_min_qty? }

POST   /products/{sku}/sizes/{sizeId}/price-advisor
  -- (v1.7) path param is sizeId: UUID
  body: { target_margin_pct, marketplace_fee_pct?, promo_allocation_pct? }
  response: { suggested_price, resulting_margin_pct, resulting_markup_pct }

POST   /stock/adjustments
  body: { product_size_id, change_qty, reason, note? }
  → manual stock opname / damage adjustment
```

### Sales

```
POST   /sales-orders
  body: {
    customer_name?, payment_method, marketplace_fee_pct?,
    items: [ { product_size_id, qty, unit_price, discount? } ]
  }
  → validates stock availability, pulls unit_hpp_snapshot from current stock cost,
    writes stock_ledger (reason='sale', negative qty), computes line_profit per item

GET    /sales-orders
GET    /sales-orders/{id}
PATCH  /sales-orders/{id}   body: { status }   -- mark paid

POST   /sales-orders/{id}/cancel
  body: { reason? }
  → writes offsetting stock_ledger rows (reason='return', positive qty) for every item,
    restoring finished-goods stock; sets sales_order.status = 'cancelled'
    (record is kept — not deleted — so history/reports stay accurate)
```

### Reports

```
GET /reports/dashboard
  -- added v1.2: single-call summary for Beranda (Dashboard) screen
  response: {
    today_revenue, today_order_count, today_profit,
    low_stock_alerts: [ { product_size_id, product_name, size_label,
                           fabric_variant_name, current_stock_qty, reorder_min_qty } ]
  }
  -- "today" determined by server timezone; no query params needed

GET /reports/sales?from=&to=&group_by=day|week|month
  -- from/to are REQUIRED and must be respected — backend must filter sales_order by sold_at date range.
  -- Returning all-time data and ignoring from/to is a confirmed bug (found in mock, v2.3).
  response: { points: [{ date, totalRevenue, totalProfit, orderCount }], totalRevenue, totalProfit }

GET /reports/margin-ranking?sort=margin_pct
GET /reports/stock-card/{product_size_id}
GET /reports/waste-by-material?from=&to=
GET /reports/low-stock          -- finished products (product_size) below reorder_min_qty
```

### Settings

```
GET    /settings
PATCH  /settings   body: { key, value }
```

---

## 5. CRUD Audit — completeness check across all entities

Prompted by finding that `MaterialPurchase` had create fully spec'd but edit/delete completely missing. This section audits every entity in the schema the same way, so the same gap doesn't exist elsewhere. General principle applied throughout: **an entity that has already had real-world consequences (stock consumed, cost locked, money moved) should never be silently editable or hard-deletable** — it's either restricted to safe fields, archived instead of deleted, or requires an explicit reversing action instead of an edit.

| Entity | Create | Edit | Delete |
|---|---|---|---|
| Material | ✅ inline via first purchase | ✅ `PATCH /materials/{id}` (name, reorder_min_qty, etc.) | **No hard delete.** Added `is_archived` flag — see below. |
| MaterialPurchase | ✅ spec'd earlier | ✅ full if unused, cost/date/supplier-only if consumed — spec'd earlier | ✅ if unused / 🚫 blocked (409) if consumed — spec'd earlier |
| Supplier | ✅ inline via purchase, or `POST /suppliers` | ⚠️ was missing — added `PATCH /suppliers/{id}` below | ⚠️ was missing — added below, allowed only if unused |
| Product | ✅ inline via Tambah Resep | ⚠️ was missing — added `PATCH /products/{sku}` below (rename only; sku immutable) | **No hard delete once any size has history.** Added `is_archived` — see below. |
| ProductSize | ✅ inline via Tambah Resep (auto-creates one row per fabric, v1.7); or `POST /products/{sku}/sizes` with optional `fabric_variant_name` | ✅ `PATCH .../sizes/{sizeId}` — path param is UUID since v1.7 (was sizeLabel String); `(product_id, size_label, fabric_variant_name)` composite uniqueness enforced | ✅ `DELETE .../sizes/{sizeId}` — archive if used, hard-delete if unused; path param is UUID since v1.7 |
| PatternSpec (Resep) | ✅ spec'd earlier | ✅ always creates a new version — **refined below**: if the current version has zero production batches against it, editing updates it in place instead of spawning a version, to avoid version-number noise from typo fixes | ⚠️ was missing — added below, allowed only if the version has zero production batches against it |
| PatternComponent (hardware in a recipe) | ✅ part of the `components` array in `POST /pattern-specs` | ✅ same — the whole array is replaced on each save, no per-row endpoint needed | ✅ same — omitting a row from the array on save removes it |
| CuttingLayout | ✅ spec'd earlier | N/A (layouts aren't edited, just re-run via suggest) | ✅ `POST /cutting-optimizer/layouts/{id}/discard` already existed — **clarified below**: only valid while `status='suggested'` |
| ProductionBatch | ✅ spec'd earlier | ✅ `qty_actual` editable pre-confirm; fully locked post-confirm | ⚠️ was missing for the draft (pre-confirm) case — added below |
| StockLedger | ✅ system-written on every production/sale/adjustment | 🚫 **by design, never** | 🚫 **by design, never** — append-only audit log; corrections happen via a new offsetting adjustment, not by touching history. Worth stating explicitly rather than leaving as an apparent oversight. |
| SalesOrder | ✅ spec'd earlier | 🚫 **by design, not supported** — line items aren't edited after creation (money/stock have already moved); correct via cancel below, then re-enter | ⚠️ was missing entirely — added `POST /sales-orders/{id}/cancel` below (reverses stock, doesn't hard-delete the record so history isn't lost) |
| Settings | ✅ fixed key set, no creation needed | ✅ `PATCH /settings` already existed | 🚫 not applicable — fixed set of known keys, nothing to delete |

### New/updated endpoints from this audit

```
PATCH  /suppliers/{id}
  body: { name }

DELETE /suppliers/{id}
  -- allowed only if zero MaterialPurchase rows reference this supplier_id; 409 Conflict otherwise

PATCH  /products/{sku}
  body: { name }
  -- sku itself is immutable after creation (it's the stable identifier used throughout the API)

DELETE /products/{sku}/sizes/{size}
  -- if this ProductSize has zero PatternSpec, zero stock_ledger, and zero sales_order_item history:
     hard-deletes it
  -- otherwise: sets is_archived = true instead (hides it from Tambah Resep pickers and Products list going
     forward, but keeps historical reports/HPP intact); returns which of the two happened so the UI can
     message it correctly ("Ukuran dihapus" vs "Ukuran diarsipkan karena sudah punya riwayat")

DELETE /products/{sku}
  -- same archive-vs-delete logic as above, applied across all of that product's sizes

PATCH  /materials/{id}
  -- (already existed) now also accepts { is_archived }
  -- archiving hides the material from Tambah Pembelian / Tambah Resep pickers, but existing purchases,
     pattern specs, and reports referencing it are unaffected

DELETE /pattern-specs/{id}
  -- allowed only if zero ProductionBatchItem rows reference this exact pattern_spec version; 409 otherwise
  -- note the refined edit behavior: POST /pattern-specs, when updating a spec that currently has zero
     ProductionBatchItem rows against it, updates that same row in place (no new version row inserted).
     Once at least one batch has been produced against a version, further edits always insert a new
     versioned row as originally spec'd — this avoids meaningless version numbers from same-day typo fixes.

POST   /cutting-optimizer/layouts/{id}/discard
  -- (already existed) clarified: only valid while status='suggested'; returns 409 if status='used'
     (a ProductionBatch already exists from this layout)

DELETE /production-batches/{id}
  -- allowed only while status='draft' (nothing has been written to stock_ledger or remaining_length_cm yet,
     since that only happens at confirm) — lets the user abandon a batch they started by mistake
  -- once status='confirmed', this always 409s — already-established immutability rule

POST   /sales-orders/{id}/cancel
  body: { reason? }
  → writes offsetting stock_ledger rows (reason='return', positive qty) for every item in the order,
    restoring finished-goods stock; sets sales_order.status = 'cancelled' (record is kept, not deleted,
    so sales history/reports stay accurate)
```

---

## 6. Open Decisions for Design/Dev to Confirm

1. ~~**Weighted-average vs FIFO**~~ — **resolved v1.2**: weighted average confirmed. `material.current_avg_cost` is the authoritative field; recalculate after every purchase create/edit/delete.
2. **Pooled material rate recompute cadence** — manual (settings screen) vs. auto-recalculated from monthly thread purchases ÷ monthly output.
3. **Rounding rule for prices** — round to nearest 500/1000 IDR in the price advisor output.
4. **Backend hosting/deployment platform** — e.g. Railway, Render, Fly.io, or self-hosted (see Section 0; stack itself is now confirmed, hosting is not).
5. ~~**JWT token lifetime and refresh strategy**~~ — **resolved in v1.1**: 30-day expiry, no refresh token. Owner re-authenticates via Google on expiry (acceptable for a single-owner app).
6. **(New — v1.1) Google Cloud Console setup required before deploy:**
   - Create a project in [Google Cloud Console](https://console.cloud.google.com)
   - Enable the "Google Identity" / OAuth 2.0 API
   - Create an OAuth 2.0 client ID: type **iOS**, bundle ID = `handika.oura-studio-frontend`
   - Copy the resulting **iOS client ID** → set as `GOOGLE_CLIENT_ID` in backend `.env`
   - Also paste it into `LoginView.swift` line ~104 (replacing `YOUR_GOOGLE_CLIENT_ID.apps.googleusercontent.com`)
   - No "Web application" client ID needed — the iOS client ID covers both the iOS app and backend token verification
7. **(New — v1.1) Set `AUTHORIZED_OWNER_EMAIL` in backend `.env`** before first deploy. This is the only Google account that will be accepted. Example `.env`:
   ```
   AUTHORIZED_OWNER_EMAIL=owner@gmail.com
   GOOGLE_CLIENT_ID=123456789-abcdefg.apps.googleusercontent.com
   JWT_SECRET=<random 32+ char string>
   ```
   Changing this value requires a backend redeploy + deleting the `owner_account` row so the next sign-in re-provisions with the new `google_sub`.
