"""product_size manual hpp override

Revision ID: 910a5b31c84a
Revises: 95946b1f4e76
Create Date: 2026-08-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '910a5b31c84a'
down_revision: Union[str, None] = '95946b1f4e76'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('product_size', sa.Column('manual_hpp_fabric', sa.Numeric(14, 4), nullable=True))
    op.add_column('product_size', sa.Column('manual_hpp_pooled', sa.Numeric(14, 4), nullable=True))
    op.add_column('product_size', sa.Column('manual_hpp_hardware', sa.Numeric(14, 4), nullable=True))
    op.add_column('product_size', sa.Column('manual_hpp_labor', sa.Numeric(14, 4), nullable=True))
    op.add_column('product_size', sa.Column('manual_hpp_overhead', sa.Numeric(14, 4), nullable=True))


def downgrade() -> None:
    op.drop_column('product_size', 'manual_hpp_overhead')
    op.drop_column('product_size', 'manual_hpp_labor')
    op.drop_column('product_size', 'manual_hpp_hardware')
    op.drop_column('product_size', 'manual_hpp_pooled')
    op.drop_column('product_size', 'manual_hpp_fabric')
