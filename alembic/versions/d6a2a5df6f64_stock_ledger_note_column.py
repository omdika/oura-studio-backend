"""stock ledger note column

Revision ID: d6a2a5df6f64
Revises: d7f5015901ba
Create Date: 2026-08-05 22:16:09.054042

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd6a2a5df6f64'
down_revision: Union[str, None] = 'd7f5015901ba'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('stock_ledger', sa.Column('note', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('stock_ledger', 'note')
