---
name: test-regression
description: Run a comprehensive pass across every previously-verified feature and entity in Oura Studios to catch anything a recent change silently broke. Use this skill before considering a milestone, a tab, or a significant chunk of work "done" — not after every small change (that's test-smoke and test-sanity's job). Trigger this when the user says things like "full test", "regression test", "before I move on", "is everything still working", or when preparing to consider a screen/feature complete.
---

# Oura Studios — Regression Test

The comprehensive pass. Where `test-smoke` checks "does it crash" and `test-sanity` checks "does the thing I just built work correctly," this skill checks "did building that thing break something else that used to work." This is the most expensive of the three tests to run — use it at milestones, not after every edit.

## Prerequisite

Only run this after `test-smoke` passes. There's no point running a full regression pass on a build that doesn't even launch.

## Structure: derive the test matrix from the handoff, don't improvise it

This skill's checklist should be *generated from* the handoff document's Section 5 (CRUD Audit table) and Section 2 (Screens & Flows), not written from memory — the handoff is the definitive list of what "working" means for this app, and it gets updated as the app evolves. Re-read both sections fresh at the start of each regression pass rather than reusing a stale mental checklist from last time.

## Part 1 — Entity CRUD matrix (from Section 5)

For every entity in the CRUD audit table, test every operation marked ✅ in its row, including the conditional ones:

| Entity | What to verify this pass |
|---|---|
| Material | Create (inline via purchase), edit (PATCH), archive (not hard-delete once used) |
| MaterialPurchase | Create; edit+delete both branches (unused = full access, consumed = locked fields/blocked delete) |
| Supplier | Create (inline + standalone), edit (rename), delete (blocked if referenced) |
| Product | Create (inline via Tambah Resep), rename, archive/delete both branches |
| ProductSize | Create (inline), edit (reorder_min_qty, selling_price), delete both branches |
| PatternSpec | Create (new combo), in-place edit (unused version), new-version edit (used version), delete (only if unused) |
| PatternComponent | Add/remove rows within a PatternSpec save (full-array-replace behavior) |
| CuttingLayout | Suggest, persist, discard (only while status='suggested', blocked once 'used') |
| ProductionBatch | Create draft, edit qty_actual pre-confirm, confirm (locks), delete draft only (blocked once confirmed) |
| StockLedger | Confirm it is NEVER directly editable/deletable anywhere in the app — this should fail loudly if you find a way to do it |
| SalesOrder | Create, mark paid, cancel (verify stock actually restocks) |
| Settings | Edit existing keys; confirm no create/delete UI exists (none should) |

Don't just test the "allowed" branch of each conditional rule — deliberately try the disallowed branch too (e.g. try to delete a consumed MaterialPurchase, try to hard-delete a Product with sales history) and confirm it's correctly blocked with the right error, not silently succeeding or crashing.

## Part 2 — Screen-level pass (from Section 2)

Walk every screen and sub-tab listed in Section 2 end to end with realistic data volume (not just 1-2 items — the handoff specifically calls for 15-30 sample rows on list screens to exercise pagination; use that same volume here so pagination/scroll regressions are actually catchable):

- Dashboard: low-stock alerts reflect actual current stock state
- Produksi → Bahan: search/filter, drill into Detail, add purchase (both fresh and pre-filled-from-Detail paths), edit/delete
- Produksi → Resep: Daftar Resep list + pagination, Tambah Resep full flow (single fabric AND multi-fabric), Editor both branches (in-place vs versioned), Riwayat Versi read-only view
- Produksi → Optimasi: run an optimization, review candidate layouts, persist one, discard one
- Produksi → Produksi: confirm a batch, verify locked-forever behavior afterward
- Produk: list, price advisor, min-stock editing
- Penjualan: create a sale, verify stock deduction and HPP snapshot, cancel a sale, verify restock
- Lainnya (Reports/Settings): each report renders with real data, settings persist after edit

## Part 3 — Cross-cutting checks (things that span multiple screens)

- Edit a MaterialPurchase's cost → confirm `material.current_avg_cost` updates → confirm that updated cost flows into a *new* production batch's HPP, and does **not** retroactively change an already-confirmed batch's locked HPP
- Create a PatternSpec new version → confirm old ProductionBatch records still reference the old version's cost, not the new one
- Cancel a SalesOrder → confirm the restocked quantity shows up correctly in the Bahan/Produk stock views, not just in the raw stock_ledger

## Reporting results

Organize the report by Part 1 / Part 2 / Part 3, pass/fail per row, with enough detail on any failure to reproduce it (exact steps, exact data used). Flag anything that passed in a previous regression run but fails now as a **regression** specifically (not just "a bug") so it's clear something that used to work broke — that distinction matters for prioritizing the fix. End with a clear overall verdict: ready for the milestone, or not yet, and why.
