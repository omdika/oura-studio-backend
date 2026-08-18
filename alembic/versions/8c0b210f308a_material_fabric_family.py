"""material fabric_family

Revision ID: 8c0b210f308a
Revises: 5407c0cc5144
Create Date: 2026-08-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8c0b210f308a'
down_revision: Union[str, None] = '5407c0cc5144'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('material', sa.Column('fabric_family', sa.String(length=100), nullable=True))
    op.create_index(
        'ix_material_fabric_family',
        'material',
        ['fabric_family'],
        postgresql_where=sa.text('fabric_family IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('ix_material_fabric_family', table_name='material')
    op.drop_column('material', 'fabric_family')
