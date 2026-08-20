"""sales_order_item hpp_source

Revision ID: 95946b1f4e76
Revises: 8c0b210f308a
Create Date: 2026-08-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '95946b1f4e76'
down_revision: Union[str, None] = '8c0b210f308a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'sales_order_item',
        sa.Column('hpp_source', sa.String(length=20), nullable=False, server_default='batch'),
    )


def downgrade() -> None:
    op.drop_column('sales_order_item', 'hpp_source')
