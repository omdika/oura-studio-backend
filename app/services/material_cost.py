"""Pure functions for material weighted-average cost and consumption-lock checks.

Kept free of DB/session dependencies so business logic can be unit-tested directly
against hand-computed numbers (see handoff Section 1.3 / Section 2 edit-lock rules).
"""

from collections.abc import Iterable


def purchase_quantity(category: str, length_cm: float | None, qty: float | None) -> float:
    """The quantity (in the material's usage_unit) a single purchase contributes.

    Fabric is tracked by length_cm; thread/hardware/packaging are tracked by qty.
    """
    value = length_cm if category == "fabric" else qty
    return value or 0.0


def compute_weighted_avg_cost(cost_qty_pairs: Iterable[tuple[float, float]]) -> float:
    """Weighted average cost per usage_unit across all purchase batches of a material.

    This is a batch-weighted average over ALL existing purchases (not remaining stock) —
    matches handoff Section 4 wording: "recalculates ... from all purchases" / "from the
    remaining purchases" (i.e. purchases remaining in the table after an edit/delete, not
    remaining quantity within each purchase).
    """
    total_cost = 0.0
    total_qty = 0.0
    for cost, qty in cost_qty_pairs:
        total_cost += cost
        total_qty += qty

    if total_qty <= 0:
        return 0.0
    return total_cost / total_qty


def is_purchase_consumed(
    category: str,
    original_length_cm: float | None,
    remaining_length_cm: float | None,
    original_qty: float | None,
    remaining_qty: float | None,
) -> bool:
    """Whether any of a purchase's stock has been consumed — gates edit/delete per Section 2.

    Fabric: compares remaining_length_cm to the originally purchased length_cm.
    Thread/hardware/packaging: compares remaining_qty to the originally purchased qty.
    """
    if category == "fabric":
        return (remaining_length_cm or 0.0) != (original_length_cm or 0.0)
    return (remaining_qty or 0.0) != (original_qty or 0.0)
