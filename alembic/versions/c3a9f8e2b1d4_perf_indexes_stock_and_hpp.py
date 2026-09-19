"""perf indexes for stock_ledger, production_batch_item, material_purchase

Revision ID: c3a9f8e2b1d4
Revises: e7a21f4c9b30
Create Date: 2026-09-19

Why: POST /products/{sku}/sizes/{size_id}/stock-from-bahan and
     PATCH /products/{sku}/sizes/{size_id} were slow.
     Profiling showed:
     - stock_ledger SUM(change_qty) filtered by product_size_id did Seq Scan
       (no index, see 900130a6de91 initial schema).
     - production_batch_item latest-HPP lookup JOIN + ORDER BY did Seq Scan
     - material_purchase FIFO deduction SELECT ... ORDER BY purchased_at did scan+sort

Cross-region RTT (Jakarta Cloud Run -> Seoul Supabase ~100ms/rt) amplifies
every extra round-trip, so besides raw scan cost the waterfall 10-13 queries
in stock-from-bahan dominated p95.

This migration adds the minimal B-Tree indexes to make those hot paths
Index Scan / Index Only Scan. No column/constraint change, safe online.
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'c3a9f8e2b1d4'
down_revision: Union[str, None] = 'e7a21f4c9b30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # stock_ledger — every stock qty / breakdown / list call filters by product_size_id
    op.create_index('ix_stock_ledger_product_size_id', 'stock_ledger', ['product_size_id'])
    op.create_index('ix_stock_ledger_product_size_reason', 'stock_ledger', ['product_size_id', 'reason'])
    op.create_index('ix_stock_ledger_created_at', 'stock_ledger', ['created_at'])

    # production_batch_item — latest HPP map + detail lookups
    op.create_index('ix_pbi_product_size_id', 'production_batch_item', ['product_size_id'])
    op.create_index('ix_pbi_batch_id', 'production_batch_item', ['production_batch_id'])

    # production_batch — _latest_* filters status='confirmed', orders by confirmed_at
    op.create_index('ix_production_batch_status', 'production_batch', ['status'])
    op.create_index('ix_production_batch_confirmed_at', 'production_batch', ['confirmed_at'])

    # material_purchase — FIFO deduction: WHERE material_id = ? ORDER BY purchased_at
    op.create_index('ix_material_purchase_material_id', 'material_purchase', ['material_id'])
    op.create_index('ix_material_purchase_material_purchased', 'material_purchase', ['material_id', 'purchased_at'])

    # product_size — list/bulk endpoints join via product_id
    op.create_index('ix_product_size_product_id', 'product_size', ['product_id'])

    # pattern_spec — filtered by product_size_id often
    op.create_index('ix_pattern_spec_product_size_id', 'pattern_spec', ['product_size_id'])


def downgrade() -> None:
    op.drop_index('ix_pattern_spec_product_size_id', table_name='pattern_spec')
    op.drop_index('ix_product_size_product_id', table_name='product_size')
    op.drop_index('ix_material_purchase_material_purchased', table_name='material_purchase')
    op.drop_index('ix_material_purchase_material_id', table_name='material_purchase')
    op.drop_index('ix_production_batch_confirmed_at', table_name='production_batch')
    op.drop_index('ix_production_batch_status', table_name='production_batch')
    op.drop_index('ix_pbi_batch_id', table_name='production_batch_item')
    op.drop_index('ix_pbi_product_size_id', table_name='production_batch_item')
    op.drop_index('ix_stock_ledger_created_at', table_name='stock_ledger')
    op.drop_index('ix_stock_ledger_product_size_reason', table_name='stock_ledger')
    op.drop_index('ix_stock_ledger_product_size_id', table_name='stock_ledger')
