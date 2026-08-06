"""Pure shelf-packing heuristic for the cutting optimizer (handoff Section 1.5, refined v1.4-v1.5).

Kept free of DB/session dependencies so the geometry and allocation logic can be unit-tested
directly. No DB objects are imported here -- the router converts SQLAlchemy rows into the
dataclasses below and back.

IMPORTANT -- documentation gap in the handoff, flagged rather than silently resolved:
Section 1.5 (Core Concepts) only describes the algorithm at a high level ("shelf-packing
heuristic... generate a few candidate layouts... recursively try to fit smaller sizes into
the leftover"). The precise two-phase algorithm with `bestMinLen`/`futureMinLength` referenced
by the v1.4-v1.5 revision-history entry ("See Section 1.5 for full algorithm spec") does NOT
actually appear in Section 1.5 as written -- that section was never updated with the promised
detail. What follows is a reconstruction from the one-line revision-history description plus
the skill's restatement of it, not a transcription of a fully-specified algorithm. Geometry
conventions, the "primary orientation" tie-break, the 3 strategies' exact ordering heuristics,
and the waste_pct definition are this implementation's own reasonable choices where the
handoff is silent -- flagged inline below.
"""

import math
import uuid
from dataclasses import dataclass, field

# Geometry convention (undocumented in the handoff, chosen here): a fabric purchase is a
# rectangle of width_cm (across the roll, fixed) x length_cm (along the roll, consumed by
# rows). "Normal" orientation places cut_width_cm across the roll and cut_height_cm along it
# (row_length_cm = cut_height_cm, matching production_batch_item.fabric_length_per_unit_cm
# semantics elsewhere). "Rotated" swaps the two.


@dataclass
class OrientationGeometry:
    pieces_per_row: int
    row_length_cm: float  # fabric length (along the roll) consumed per row in this orientation


@dataclass
class Candidate:
    product_size_id: uuid.UUID
    pattern_spec_id: uuid.UUID
    cut_width_cm: float
    cut_height_cm: float
    rotation_allowed: bool
    min_qty: int = 0
    # Only used by the max_profit strategy ordering; None if unknown (selling price not set yet).
    profit_per_piece_hint: float | None = None


@dataclass
class AllocatedItem:
    product_size_id: uuid.UUID
    pattern_spec_id: uuid.UUID
    orientation: str  # "normal" | "rotated"
    qty_suggested: int
    fabric_length_used_cm: float


@dataclass
class LayoutResult:
    strategy: str
    items: list[AllocatedItem] = field(default_factory=list)
    waste_pct: float = 0.0


def normal_geometry(fabric_width_cm: float, cut_width_cm: float, cut_height_cm: float) -> OrientationGeometry:
    pieces_per_row = int(fabric_width_cm // cut_width_cm) if cut_width_cm > 0 else 0
    return OrientationGeometry(pieces_per_row=pieces_per_row, row_length_cm=cut_height_cm)


def rotated_geometry(fabric_width_cm: float, cut_width_cm: float, cut_height_cm: float) -> OrientationGeometry:
    pieces_per_row = int(fabric_width_cm // cut_height_cm) if cut_height_cm > 0 else 0
    return OrientationGeometry(pieces_per_row=pieces_per_row, row_length_cm=cut_width_cm)


def is_feasible(fabric_width_cm: float, c: Candidate) -> bool:
    """A candidate is feasible if at least one allowed orientation fits >=1 piece across the roll width."""
    normal = normal_geometry(fabric_width_cm, c.cut_width_cm, c.cut_height_cm)
    if normal.pieces_per_row > 0:
        return True
    if c.rotation_allowed:
        rotated = rotated_geometry(fabric_width_cm, c.cut_width_cm, c.cut_height_cm)
        return rotated.pieces_per_row > 0
    return False


def _min_rows_for_qty(pieces_per_row: int, qty: int) -> float:
    if pieces_per_row <= 0:
        return math.inf
    if qty <= 0:
        return 0.0
    return math.ceil(qty / pieces_per_row)


def compute_best_min_len(fabric_width_cm: float, c: Candidate) -> float:
    """bestMinLen = min(normalMinRows x cutLength, rotMinRows x cutWidth) -- v1.4-v1.5.

    The minimum fabric length needed to guarantee this candidate's min_qty floor, using
    whichever orientation is more length-efficient for meeting that floor specifically
    (independent of which orientation ends up being used for the bulk allocation in Phase 2).
    Returns 0 if min_qty is unset/zero (nothing to reserve for) or unreachable in either
    orientation (nothing sensible to reserve).
    """
    if c.min_qty <= 0:
        return 0.0

    normal = normal_geometry(fabric_width_cm, c.cut_width_cm, c.cut_height_cm)
    normal_rows = _min_rows_for_qty(normal.pieces_per_row, c.min_qty)
    normal_len = normal_rows * c.cut_height_cm

    if not c.rotation_allowed:
        return 0.0 if math.isinf(normal_len) else normal_len

    rotated = rotated_geometry(fabric_width_cm, c.cut_width_cm, c.cut_height_cm)
    rot_rows = _min_rows_for_qty(rotated.pieces_per_row, c.min_qty)
    rot_len = rot_rows * c.cut_width_cm

    best = min(normal_len, rot_len)
    return 0.0 if math.isinf(best) else best


def _primary_orientation(fabric_width_cm: float, c: Candidate) -> tuple[str, OrientationGeometry, str, OrientationGeometry | None]:
    """Primary = denser orientation (more pieces per row); ties favor normal. This is this
    implementation's own tie-break choice -- the handoff doesn't define "primary" explicitly,
    only that a fallback to the alternate orientation happens when primary yields maxRows=0.
    """
    normal = normal_geometry(fabric_width_cm, c.cut_width_cm, c.cut_height_cm)
    if not c.rotation_allowed:
        return "normal", normal, "rotated", None
    rotated = rotated_geometry(fabric_width_cm, c.cut_width_cm, c.cut_height_cm)
    if rotated.pieces_per_row > normal.pieces_per_row:
        return "rotated", rotated, "normal", normal
    return "normal", normal, "rotated", rotated


def _density(fabric_width_cm: float, c: Candidate) -> float:
    """Pieces produced per cm of fabric length consumed, in the primary orientation --
    the correct metric for "which candidate advances total quantity fastest" (raw
    pieces_per_row alone ignores how much length each row costs, which under/over-values
    candidates whose row_length_cm differs).
    """
    _, geom, _, _ = _primary_orientation(fabric_width_cm, c)
    if geom.pieces_per_row <= 0 or geom.row_length_cm <= 0:
        return 0.0
    return geom.pieces_per_row / geom.row_length_cm


def allocate(fabric_width_cm: float, fabric_length_cm: float, ordered_candidates: list[Candidate]) -> tuple[list[AllocatedItem], float]:
    """Two-phase shelf-packing over candidates in the given processing order.

    Phase 1: bestMinLen is pre-computed per candidate (independent of processing order).
    Phase 2: for each candidate in order, reserve futureMinLength = sum(bestMinLen of candidates
    still to come) before computing how much space this candidate may use; fall back to the
    alternate orientation if the primary orientation can't fit a single row in what's left.
    Returns (items, leftover_length_cm).
    """
    feasible = [c for c in ordered_candidates if is_feasible(fabric_width_cm, c)]
    best_min_lens = [compute_best_min_len(fabric_width_cm, c) for c in feasible]

    remaining_length = fabric_length_cm
    items: list[AllocatedItem] = []

    for i, c in enumerate(feasible):
        future_min_length = sum(best_min_lens[i + 1 :])
        available = max(0.0, remaining_length - future_min_length)

        primary_name, primary_geom, alt_name, alt_geom = _primary_orientation(fabric_width_cm, c)

        rows = math.floor(available / primary_geom.row_length_cm) if primary_geom.pieces_per_row > 0 else 0
        used_name, used_geom = primary_name, primary_geom

        if rows == 0 and alt_geom is not None and alt_geom.pieces_per_row > 0:
            alt_rows = math.floor(available / alt_geom.row_length_cm)
            if alt_rows > 0:
                rows, used_name, used_geom = alt_rows, alt_name, alt_geom

        qty = rows * used_geom.pieces_per_row
        length_used = rows * used_geom.row_length_cm
        remaining_length -= length_used

        if qty > 0:
            items.append(
                AllocatedItem(
                    product_size_id=c.product_size_id,
                    pattern_spec_id=c.pattern_spec_id,
                    orientation=used_name,
                    qty_suggested=qty,
                    fabric_length_used_cm=length_used,
                )
            )

    return items, max(0.0, remaining_length)


def _top_up_leftover(fabric_width_cm: float, leftover_cm: float, candidates: list[Candidate], items: list[AllocatedItem]) -> tuple[list[AllocatedItem], float]:
    """Section 1.5 point 3: 'recursively try to fit smaller sizes into the leftover.' Tries
    every candidate, smallest primary row-length first, greedily adding whole rows until the
    leftover strip can't fit another row of anything. Used by the min_waste strategy only.
    """
    by_id = {item.product_size_id: item for item in items}
    remaining = leftover_cm
    ordered = sorted(
        (c for c in candidates if is_feasible(fabric_width_cm, c)),
        key=lambda c: -_density(fabric_width_cm, c),
    )
    progress = True
    while progress and remaining > 0:
        progress = False
        for c in ordered:
            primary_name, primary_geom, alt_name, alt_geom = _primary_orientation(fabric_width_cm, c)
            for name, geom in ((primary_name, primary_geom), (alt_name, alt_geom)):
                if geom is None or geom.pieces_per_row <= 0 or geom.row_length_cm <= 0:
                    continue
                if geom.row_length_cm <= remaining:
                    remaining -= geom.row_length_cm
                    existing = by_id.get(c.product_size_id)
                    if existing is not None and existing.orientation == name:
                        existing.qty_suggested += geom.pieces_per_row
                        existing.fabric_length_used_cm += geom.row_length_cm
                    else:
                        new_item = AllocatedItem(
                            product_size_id=c.product_size_id,
                            pattern_spec_id=c.pattern_spec_id,
                            orientation=name,
                            qty_suggested=geom.pieces_per_row,
                            fabric_length_used_cm=geom.row_length_cm,
                        )
                        items.append(new_item)
                        by_id[c.product_size_id] = new_item
                    progress = True
                    break
    return items, remaining


def build_layout(
    strategy: str,
    fabric_width_cm: float,
    fabric_length_cm: float,
    candidates: list[Candidate],
) -> LayoutResult:
    """Builds one strategy's layout. Candidate processing order is this strategy's own
    ordering heuristic -- the handoff specifies the 3 strategy names but not how each one
    orders/prioritizes candidates, so these are reasonable choices, not transcribed spec.
    """
    if strategy == "max_qty":
        # Highest pieces-per-cm-of-length first advances total quantity fastest across the
        # fixed roll length (raw pieces_per_row alone ignores each row's length cost).
        ordered = sorted(candidates, key=lambda c: -_density(fabric_width_cm, c))
        items, leftover = allocate(fabric_width_cm, fabric_length_cm, ordered)
    elif strategy == "min_waste":
        ordered = sorted(candidates, key=lambda c: -_density(fabric_width_cm, c))
        items, leftover = allocate(fabric_width_cm, fabric_length_cm, ordered)
        items, leftover = _top_up_leftover(fabric_width_cm, leftover, candidates, items)
    elif strategy == "max_profit":
        ordered = sorted(
            candidates,
            key=lambda c: -(c.profit_per_piece_hint or 0.0),
        )
        items, leftover = allocate(fabric_width_cm, fabric_length_cm, ordered)
    else:
        raise ValueError(f"unknown strategy: {strategy}")

    total_area = fabric_width_cm * fabric_length_cm
    used_area = sum(item.qty_suggested * next(c.cut_width_cm * c.cut_height_cm for c in candidates if c.product_size_id == item.product_size_id) for item in items) if items else 0.0
    waste_pct = round(100.0 * (1 - used_area / total_area), 2) if total_area > 0 else 0.0

    return LayoutResult(strategy=strategy, items=items, waste_pct=max(0.0, waste_pct))


def allocate_cost_per_piece(total_fabric_cost: float, items: list[AllocatedItem]) -> list[float]:
    """cutting_layout_item.cost_per_piece DDL comment: 'derived: (share of total_fabric_cost) /
    qty_suggested'. Share is allocated proportionally to fabric_length_used_cm among the items
    of ONE layout -- i.e. the whole purchase cost is treated as consumed by whatever actually
    got cut in this layout (waste is absorbed into cost-per-piece, not costed separately).
    """
    total_length = sum(item.fabric_length_used_cm for item in items)
    if total_length <= 0:
        return [0.0 for _ in items]
    return [
        (total_fabric_cost * (item.fabric_length_used_cm / total_length)) / item.qty_suggested
        if item.qty_suggested > 0
        else 0.0
        for item in items
    ]


def estimate_fabric_cost_per_piece(
    fabric_width_cm: float, purchase_total_cost: float, purchase_length_cm: float, c: Candidate
) -> float:
    """Suggest-time-only estimate (fabric cost share alone, not full HPP) used purely to rank
    candidates for the max_profit strategy before any layout is actually built -- not the
    authoritative cost_per_piece, which is only known after allocate_cost_per_piece runs
    against an actual layout.
    """
    if purchase_length_cm <= 0:
        return 0.0
    cost_per_cm = purchase_total_cost / purchase_length_cm
    _, geom, _, _ = _primary_orientation(fabric_width_cm, c)
    if geom.pieces_per_row <= 0 or geom.row_length_cm <= 0:
        return 0.0
    return (cost_per_cm * geom.row_length_cm) / geom.pieces_per_row
