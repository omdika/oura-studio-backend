"""add_category_to_product

Revision ID: b5d06862784d
Revises: d986703e1e30
Create Date: 2026-09-07 18:31:38.854619

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5d06862784d'
down_revision: Union[str, None] = 'd986703e1e30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('product', sa.Column('category', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('product', 'category')
