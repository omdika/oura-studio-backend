"""Pure functions for the price-advisor feature (handoff Section 1.6).

Kept free of DB/session dependencies so the formula can be unit-tested directly
against hand-computed numbers.
"""


def compute_margin_pct(selling_price: float, hpp_total: float) -> float:
    """Margin = (price - HPP) / price."""
    if selling_price <= 0:
        return 0.0
    return (selling_price - hpp_total) / selling_price


def compute_markup_pct(selling_price: float, hpp_total: float) -> float:
    """Markup = (price - HPP) / HPP."""
    if hpp_total <= 0:
        return 0.0
    return (selling_price - hpp_total) / hpp_total


def compute_suggested_price(
    hpp_total: float,
    target_margin_pct: float,
    marketplace_fee_pct: float = 0.0,
    promo_allocation_pct: float = 0.0,
) -> float:
    """selling_price = HPP / (1 - target_margin - marketplace_fee_pct - promo_allocation_pct).

    All *_pct args are fractions in [0, 1), matching the formula's direct subtraction from 1
    (handoff Section 1.6) -- not percentages out of 100.
    """
    denominator = 1 - target_margin_pct - marketplace_fee_pct - promo_allocation_pct
    if denominator <= 0:
        raise ValueError("target_margin_pct + marketplace_fee_pct + promo_allocation_pct must be < 1")
    return hpp_total / denominator
