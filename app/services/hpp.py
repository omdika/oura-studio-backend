"""Pure HPP breakdown computation (handoff Section 1.4).

Kept free of DB/session dependencies so the formula can be unit-tested directly
against hand-computed numbers.

HPP(sku, size) = fabric_cost_per_piece + pooled_material_rate + hardware_cost
               + labor_minutes * labor_rate_per_minute + overhead_per_unit
"""

from dataclasses import dataclass


@dataclass
class HppBreakdown:
    hpp_fabric: float
    hpp_pooled_material: float
    hpp_hardware: float
    hpp_labor: float
    hpp_overhead: float
    hpp_total: float


def compute_hpp(
    fabric_cost_per_piece: float,
    pooled_material_rate: float,
    hardware_cost_per_unit: float,
    est_labor_minutes: float,
    labor_rate_per_minute: float,
    overhead_per_unit: float,
) -> HppBreakdown:
    hpp_labor = est_labor_minutes * labor_rate_per_minute
    hpp_total = fabric_cost_per_piece + pooled_material_rate + hardware_cost_per_unit + hpp_labor + overhead_per_unit
    return HppBreakdown(
        hpp_fabric=fabric_cost_per_piece,
        hpp_pooled_material=pooled_material_rate,
        hpp_hardware=hardware_cost_per_unit,
        hpp_labor=hpp_labor,
        hpp_overhead=overhead_per_unit,
        hpp_total=hpp_total,
    )
