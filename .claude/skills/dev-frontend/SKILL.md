---
name: dev-frontend
description: Build and modify the Oura Studios iOS frontend (SwiftUI) strictly according to the project's handoff document. Use this skill whenever the user asks to build, implement, wire up, or fix any screen, sheet, form, or UI component for the Oura Studios app — e.g. "build the Bahan tab", "implement Tambah Pembelian", "add the Resep editor", "make the dashboard". Always trigger this before writing any SwiftUI view for this project, even for small tweaks, since screen-level consistency (shared components, navigation patterns) matters more than any single screen looking right in isolation.
---

# Oura Studios — Frontend Dev

Builds the iOS frontend (SwiftUI) for Oura Studios, a custom inventory/production app for a handmade-accessories business. The handoff document is the single source of truth for every screen, field, and interaction — this skill exists to make sure that document actually gets followed precisely, not just used as loose inspiration.

## Step 0 — Always read the handoff first

Before touching any UI code, locate and read the project's handoff document (commonly `docs/handoff.md`, `handoff.md`, or `oura-studios-handoff.md` in the project root — search for it if unsure). Read at minimum:
- **Section 1** (Core Concepts) — for *why* a screen works the way it does. Don't skip this even for "just UI" work — e.g. you cannot build the Optimasi tab correctly without understanding the nesting/cutting-optimizer logic in 1.5, and you cannot build Tambah Pembelian correctly without understanding cost classes in 1.3.
- **Section 2** (Screens & Flows) — the literal spec for whatever screen you're building. Read the *entire* subsection for that screen/tab, not just the part that seems relevant — component-level behavior notes are often at the end of a subsection and easy to miss.

If the handoff is ambiguous or silent on something you need (a spacing value, an animation, a copy string), make a reasonable choice consistent with the rest of the app and **say so explicitly** in your response — don't silently invent something that contradicts an established pattern elsewhere.

## Non-negotiable structural rules (violating these breaks consistency across the whole app)

1. **Hierarchical relationships use push navigation, action relationships use modals/sheets — never equal tabs.** If screen A is "a list" and screen B is "one item from that list," B is pushed, not tabbed. If screen C is "an action taken from A or B" (like adding a purchase), C is a sheet, not a tab. This was a real bug found during design review (Tab Bahan's Daftar/Tambah Pembelian/Detail) — don't reintroduce it elsewhere.
2. **Build shared components once, reuse everywhere.** The handoff explicitly calls out several components that must be implemented a single time and reused, not reimplemented per-screen:
   - Numeric input (decimal keypad, comma as decimal separator, must validate > 0)
   - Currency input (live Rupiah-grouping formatting as the user types, not just on blur)
   - Native date picker (defaults to today, disables future dates)
   - Searchable dropdown with inline "+ create new" (used for Bahan, Supplier)
   - Tokenized multi-select search field (used for multi-fabric selection in Tambah Resep)
   - Chip/pill single-select (used for Produk, Ukuran, Kategori)
   Before writing a new field/input for any screen, check whether one of these already exists in the codebase. If it does, use it. If the handoff describes behavior for a field type that doesn't have a component yet, build it as a standalone reusable component first, then use it — don't inline one-off logic into a single screen's view file.
3. **Progressive disclosure in creation sheets.** Multi-section "add new X" sheets (Tambah Pembelian, Tambah Resep) show all sections at once but visually de-emphasize/disable later sections until earlier ones are complete. Don't build these as multi-screen wizards (no "Next" button navigating between full screens) unless the handoff says otherwise.
4. **Respect the CRUD audit table (handoff Section 5).** Every entity has explicit rules about what's editable/deletable and under what conditions (e.g. a MaterialPurchase's dimensions lock once consumed; a PatternSpec can only be hard-deleted if zero production batches used it). Don't build a generic "edit" or "delete" button without checking this table first — the correct behavior is usually conditional, not a flat yes/no.
5. **Bottom navigation is fixed:** Beranda · Produksi · Produk · Penjualan · Lainnya. "Produk" (Products/Finished Goods) is its own tab, not nested inside "Produksi". Don't restructure this without the user explicitly asking.

## Workflow for building/modifying a screen

1. Read the relevant handoff subsection(s) in full (Step 0).
2. Check what shared components already exist in the project that this screen needs — reuse before building new.
3. Implement the screen's states explicitly: empty state, loading state, populated state, error state — the handoff usually describes at least the populated state in detail; infer the others consistently with how other screens in the app handle them.
4. Wire up the actual API calls per the handoff's API contract (Section 4) — use real endpoint shapes, not placeholder data, unless the backend isn't built yet (in which case, mock at the network layer, not by hardcoding UI state).
5. After building, hand off to the `test-smoke` skill (or run it yourself) before considering the screen done — a screen that compiles isn't the same as a screen that works.

## When the handoff and reality conflict

If implementing a spec'd behavior turns out to be impossible or badly-suited to SwiftUI/iOS conventions (e.g. a specific gesture pattern doesn't map well to a native component), don't silently deviate. Implement the closest reasonable native equivalent, and clearly flag the deviation and why, so it can be reflected back into the handoff document rather than becoming an undocumented drift between spec and app.
