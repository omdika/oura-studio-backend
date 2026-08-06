"""Pure decision logic for PatternSpec versioning (handoff Section 4/5 -- POST /pattern-specs).

Kept free of DB/session dependencies so the three-way branch can be unit-tested directly
against the handoff's worked scenarios.
"""

from enum import Enum


class SpecSaveAction(str, Enum):
    CREATE = "create"  # no active spec exists yet for (product_size_id, fabric_material_id)
    UPDATE_IN_PLACE = "update_in_place"  # active spec exists, zero ProductionBatchItem rows against it
    NEW_VERSION = "new_version"  # active spec exists, at least one ProductionBatchItem row against it


def decide_spec_save_action(*, active_spec_exists: bool, has_production_batch_items: bool) -> SpecSaveAction:
    if not active_spec_exists:
        return SpecSaveAction.CREATE
    if not has_production_batch_items:
        return SpecSaveAction.UPDATE_IN_PLACE
    return SpecSaveAction.NEW_VERSION
