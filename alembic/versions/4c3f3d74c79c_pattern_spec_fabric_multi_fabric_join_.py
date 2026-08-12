"""pattern_spec_fabric multi-fabric join table

Revision ID: 4c3f3d74c79c
Revises: 80ff529e7130
Create Date: 2026-08-12 12:46:59.927203

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4c3f3d74c79c'
down_revision: Union[str, None] = '80ff529e7130'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'pattern_spec_fabric',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('pattern_spec_id', sa.UUID(), nullable=False),
        sa.Column('material_id', sa.UUID(), nullable=False),
        sa.Column('cut_width_cm', sa.Numeric(), nullable=False),
        sa.Column('cut_height_cm', sa.Numeric(), nullable=False),
        sa.Column('rotation_allowed', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('fabric_label', sa.Text(), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.CheckConstraint('cut_width_cm > 0', name='ck_pattern_spec_fabric_cut_width_cm_positive'),
        sa.CheckConstraint('cut_height_cm > 0', name='ck_pattern_spec_fabric_cut_height_cm_positive'),
        sa.ForeignKeyConstraint(['pattern_spec_id'], ['pattern_spec.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['material_id'], ['material.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_pattern_spec_fabric_spec_id', 'pattern_spec_fabric', ['pattern_spec_id'])

    # 1b. Migrate existing flat fabric fields into one row per pattern_spec.
    op.execute(
        """
        INSERT INTO pattern_spec_fabric
            (id, pattern_spec_id, material_id, cut_width_cm, cut_height_cm, rotation_allowed, sort_order)
        SELECT
            gen_random_uuid(), id, fabric_material_id, cut_width_cm, cut_height_cm, rotation_allowed, 0
        FROM pattern_spec
        WHERE fabric_material_id IS NOT NULL
        """
    )

    # Consolidate legacy "gabungkan" data: pre-v2.15, one PatternSpec was created per fabric and
    # several active specs ended up sharing the same product_size_id as a workaround for
    # multi-fabric recipes. v2.15 makes that unnecessary (one spec, N fabric rows), so merge each
    # group's fabric rows onto the earliest spec and drop the rest. Specs that already have
    # production_batch_item or cutting_layout_item history are left untouched (can't be merged
    # without corrupting that history) -- if that ever happens, the product size will keep >1
    # active spec post-migration, same as it does today.
    op.execute("CREATE TEMP TABLE _spec_primary ON COMMIT DROP AS "
               "SELECT DISTINCT ON (product_size_id) product_size_id, id AS primary_id "
               "FROM pattern_spec WHERE is_active = true "
               "ORDER BY product_size_id, effective_from ASC, id ASC")

    op.execute("CREATE TEMP TABLE _spec_secondary ON COMMIT DROP AS "
               "SELECT ps.id AS secondary_id, sp.primary_id, ps.effective_from "
               "FROM pattern_spec ps "
               "JOIN _spec_primary sp ON sp.product_size_id = ps.product_size_id "
               "WHERE ps.is_active = true AND ps.id != sp.primary_id")

    op.execute("CREATE TEMP TABLE _spec_mergeable ON COMMIT DROP AS "
               "SELECT s.* FROM _spec_secondary s "
               "WHERE NOT EXISTS (SELECT 1 FROM production_batch_item pbi WHERE pbi.pattern_spec_id = s.secondary_id) "
               "AND NOT EXISTS (SELECT 1 FROM cutting_layout_item cli WHERE cli.pattern_spec_id = s.secondary_id)")

    op.execute(
        """
        WITH ranked AS (
            SELECT secondary_id, primary_id,
                   row_number() OVER (PARTITION BY primary_id ORDER BY effective_from ASC, secondary_id ASC) AS rn
            FROM _spec_mergeable
        )
        UPDATE pattern_spec_fabric psf
        SET pattern_spec_id = ranked.primary_id, sort_order = ranked.rn
        FROM ranked
        WHERE psf.pattern_spec_id = ranked.secondary_id
        """
    )

    op.execute("DELETE FROM pattern_component WHERE pattern_spec_id IN (SELECT secondary_id FROM _spec_mergeable)")
    op.execute("DELETE FROM pattern_spec WHERE id IN (SELECT secondary_id FROM _spec_mergeable)")

    # 1c. Drop the now-migrated flat fabric fields from pattern_spec.
    op.drop_column('pattern_spec', 'fabric_material_id')
    op.drop_column('pattern_spec', 'cut_width_cm')
    op.drop_column('pattern_spec', 'cut_height_cm')
    op.drop_column('pattern_spec', 'rotation_allowed')


def downgrade() -> None:
    # Best-effort: restores one fabric's worth of flat fields per remaining pattern_spec (the
    # sort_order=0 layer). Specs merged away by the gabungkan-consolidation in upgrade() are gone
    # and cannot be reconstructed -- their fabric rows survive under the spec they were merged into.
    op.add_column('pattern_spec', sa.Column('fabric_material_id', sa.UUID(), nullable=True))
    op.add_column('pattern_spec', sa.Column('cut_width_cm', sa.Numeric(), nullable=True))
    op.add_column('pattern_spec', sa.Column('cut_height_cm', sa.Numeric(), nullable=True))
    op.add_column('pattern_spec', sa.Column('rotation_allowed', sa.Boolean(), nullable=True))

    op.execute(
        """
        UPDATE pattern_spec ps
        SET fabric_material_id = psf.material_id,
            cut_width_cm = psf.cut_width_cm,
            cut_height_cm = psf.cut_height_cm,
            rotation_allowed = psf.rotation_allowed
        FROM pattern_spec_fabric psf
        WHERE psf.pattern_spec_id = ps.id AND psf.sort_order = 0
        """
    )

    op.alter_column('pattern_spec', 'fabric_material_id', nullable=False)
    op.alter_column('pattern_spec', 'cut_width_cm', nullable=False)
    op.alter_column('pattern_spec', 'cut_height_cm', nullable=False)
    op.alter_column('pattern_spec', 'rotation_allowed', nullable=False)
    op.create_foreign_key(
        'pattern_spec_fabric_material_id_fk', 'pattern_spec', 'material', ['fabric_material_id'], ['id']
    )

    op.drop_table('pattern_spec_fabric')
