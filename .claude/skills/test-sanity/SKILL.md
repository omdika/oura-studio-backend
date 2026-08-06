---
name: test-sanity
description: Verify that a specific, recently-built or recently-changed feature in Oura Studios actually works correctly end to end, including its business logic and computed values — not just that it doesn't crash. Use this skill right after a dev skill finishes implementing or modifying a specific screen, endpoint, or calculation, and after test-smoke has already passed. Trigger this whenever the user says things like "check if X works", "verify the HPP calc", "test the new feature", or after any change to costing/versioning/CRUD logic specifically.
---

# Oura Studios — Sanity Test

Focused, correctness-level verification of *whatever just changed* — not a full app pass (that's `test-regression`). This skill exists because Oura Studios has genuinely non-trivial business logic (joint-product costing, weighted-average cost, conditional versioning, consumption-gated edit locks), and a screen that renders correctly can still compute the wrong number or allow an action that should be blocked.

## Prerequisite

Only run this after `test-smoke` has passed for the relevant area — no point sanity-testing calculations on a screen that crashes on open.

## Step 1 — Scope the test to what actually changed

Identify what was just built or modified (from the conversation, a diff, or the user's description). Map it back to the specific handoff section(s) it corresponds to — Section 1 for the underlying logic, Section 2 for the UI behavior, Section 3/4 for schema/API, Section 5 for CRUD rules. Don't re-test unrelated areas here — that's regression's job.

## Step 2 — Verify against the handoff's own worked examples where they exist

The handoff includes concrete worked numbers specifically so they can be used as test fixtures without inventing new data:
- **HPP breakdown example** (Section 1.6): a Scrunchie M in Satin with known fabric/labor/overhead costs and a known resulting HPP total and margin %. If you just built or changed HPP calculation, run this exact scenario through the actual implementation and check the output matches.
- **Cutting optimizer example** (Section 1.5): a 100×150cm piece with S (100×20) and L (120×30) patterns, with known yield/waste results for different layout strategies. Use this to verify the optimizer's output, not made-up dimensions — the handoff already worked out the correct answer by hand.
- **Tambah Resep worked example** (Section 2, Tab Resep): adding "Scrunchie XXL" in both Satin and Waffle in one flow, resulting in exactly 2 new PatternSpec rows. Use this to verify the multi-fabric creation flow end to end, including that both `POST /pattern-specs` calls actually fire and both rows appear in Daftar Resep afterward.

If the feature you're testing doesn't have a ready-made worked example in the handoff, construct a small one yourself with round numbers, compute the expected result by hand first, then verify the implementation matches — don't just eyeball whether the output "looks plausible."

## Step 3 — Verify the specific business rule(s) that apply to this feature

Pull the relevant rule(s) from Section 5's CRUD audit table (or Section 1's core concepts) and test them directly, not just the happy path:
- If testing MaterialPurchase edit/delete: verify an *unused* purchase is fully editable/deletable, then verify a *consumed* purchase correctly locks dimension fields and blocks delete (409), not just that the happy-path edit works.
- If testing PatternSpec save: verify a *never-used* spec updates in place (no new version, no `effective_from` change), then verify a spec with at least one production batch against it correctly creates a new version instead.
- If testing HPP/cost calculations: verify `material.current_avg_cost` actually recalculates after a purchase edit/delete, not just after create.
- If testing archive/delete logic (Material, Product, ProductSize): verify an entity with zero history hard-deletes, and one with any history archives instead — check both branches, not just one.

## Step 4 — Check the UI reflects the correct state, not just that the API returns correctly

If this is a frontend or full-stack feature, verify the UI actually shows the right thing after the action — e.g. after confirming a production batch, does the Bahan tab's material Detail show the updated `remaining_length_cm` and the new stock_ledger consumption entry, not just that the API call succeeded.

## Reporting results

For each check: state what was tested, the expected value/behavior (with the calculation shown if it's a numeric check), the actual result, and pass/fail. If something fails, be specific enough that `dev-frontend` or `dev-backend` can fix it without re-deriving the expected behavior from scratch — quote the relevant handoff section if it clarifies the expected behavior.
