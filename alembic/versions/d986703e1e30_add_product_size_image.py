"""add_product_size_image

Revision ID: d986703e1e30
Revises: 1bbae0f66ab4
Create Date: 2026-09-05 22:07:50.848359

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd986703e1e30'
down_revision: Union[str, None] = '1bbae0f66ab4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('product_size_image',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('product_size_id', sa.UUID(), nullable=False),
    sa.Column('image_url', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['product_size_id'], ['product_size.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_product_size_image_size_id', 'product_size_image', ['product_size_id'])


def downgrade() -> None:
    op.drop_index('idx_product_size_image_size_id', table_name='product_size_image')
    op.drop_table('product_size_image')
