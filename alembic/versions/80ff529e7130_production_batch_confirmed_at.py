"""production batch confirmed_at column

Revision ID: 80ff529e7130
Revises: d6a2a5df6f64
Create Date: 2026-08-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '80ff529e7130'
down_revision: Union[str, None] = 'd6a2a5df6f64'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('production_batch', sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('production_batch', 'confirmed_at')
