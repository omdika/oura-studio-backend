"""production_batch_layout multi-layout batch

Revision ID: 5407c0cc5144
Revises: 4c3f3d74c79c
Create Date: 2026-08-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5407c0cc5144'
down_revision: Union[str, None] = '4c3f3d74c79c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'production_batch_layout',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('production_batch_id', sa.UUID(), nullable=False),
        sa.Column('cutting_layout_id', sa.UUID(), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['production_batch_id'], ['production_batch.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['cutting_layout_id'], ['cutting_layout.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('production_batch_id', 'cutting_layout_id', name='uq_pbl_batch_layout'),
    )
    op.create_index('idx_pbl_batch', 'production_batch_layout', ['production_batch_id'])

    # 1b. Migrate existing single-layout links into the join table.
    op.execute(
        """
        INSERT INTO production_batch_layout (id, production_batch_id, cutting_layout_id, sort_order)
        SELECT gen_random_uuid(), id, cutting_layout_id, 0
        FROM production_batch
        WHERE cutting_layout_id IS NOT NULL
        """
    )

    # Beyond the implement doc's literal 1a-1c: the doc's target POST/GET response shape
    # (section 3b) includes cutting_layout_strategy and material_name on ProductionBatch, and
    # assumes they're "unchanged" pre-existing fields -- they aren't, neither column nor a
    # CuttingLayout.strategy source column exists yet. Adding both here since they're required
    # to actually serve that response shape; nullable/best-effort backfill, no data loss risk.
    op.add_column('cutting_layout', sa.Column('strategy', sa.Text(), nullable=True))
    op.add_column('production_batch', sa.Column('cutting_layout_strategy', sa.Text(), nullable=True))
    op.add_column('production_batch', sa.Column('material_name', sa.Text(), nullable=True))

    op.execute(
        """
        UPDATE production_batch pb
        SET material_name = m.name,
            cutting_layout_strategy = cl.strategy
        FROM production_batch_layout pbl
        JOIN cutting_layout cl ON cl.id = pbl.cutting_layout_id
        JOIN material_purchase mp ON mp.id = cl.material_purchase_id
        JOIN material m ON m.id = mp.material_id
        WHERE pbl.production_batch_id = pb.id AND pbl.sort_order = 0
        """
    )

    # 1c. Drop the now-migrated flat layout link from production_batch.
    op.drop_column('production_batch', 'cutting_layout_id')

    # Also beyond the doc's literal migration section: a multi-layout ProductionBatchItem
    # aggregates across N CuttingLayoutItems (different pattern_spec fabric layers, different
    # material_purchase rows), so it no longer has one meaningful cutting_layout_item_id /
    # material_purchase_id / fabric_length_per_unit_cm. Those become nullable (populated for
    # single-layout items exactly as before, left NULL for aggregated multi-layout items).
    # qty_suggested changes from a computed property (derived from a single cutting_layout_item)
    # to a real column set directly at creation, since that single-item derivation no longer
    # applies to multi-layout items either.
    op.add_column('production_batch_item', sa.Column('qty_suggested', sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE production_batch_item pbi
        SET qty_suggested = cli.qty_suggested
        FROM cutting_layout_item cli
        WHERE cli.id = pbi.cutting_layout_item_id
        """
    )
    op.alter_column('production_batch_item', 'material_purchase_id', nullable=True)
    op.alter_column('production_batch_item', 'fabric_length_per_unit_cm', nullable=True)


def downgrade() -> None:
    op.alter_column('production_batch_item', 'fabric_length_per_unit_cm', nullable=False)
    op.alter_column('production_batch_item', 'material_purchase_id', nullable=False)
    op.drop_column('production_batch_item', 'qty_suggested')

    op.add_column('production_batch', sa.Column('cutting_layout_id', sa.UUID(), nullable=True))
    op.execute(
        """
        UPDATE production_batch pb
        SET cutting_layout_id = pbl.cutting_layout_id
        FROM production_batch_layout pbl
        WHERE pbl.production_batch_id = pb.id AND pbl.sort_order = 0
        """
    )
    op.create_foreign_key(
        'production_batch_cutting_layout_id_fk', 'production_batch', 'cutting_layout', ['cutting_layout_id'], ['id']
    )

    op.drop_column('production_batch', 'material_name')
    op.drop_column('production_batch', 'cutting_layout_strategy')
    op.drop_column('cutting_layout', 'strategy')

    op.drop_table('production_batch_layout')
