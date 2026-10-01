"""v3.62 product gallery + shopee wakil per-size

Revision ID: f3a62c1910d2
Revises: c3a9f8e2b1d4
Create Date: 2026-10-01

v3.62: new product-level gallery (max 9, index 0 = cover) + single
Shopee representative photo per size (is_shopee_selected, exclusive).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3a62c1910d2'
down_revision: Union[str, None] = 'c3a9f8e2b1d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'product_image',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('product_id', sa.UUID(), nullable=False),
        sa.Column('image_url', sa.Text(), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_cover', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['product_id'], ['product.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_product_image_product', 'product_image', ['product_id', 'sort_order'])
    op.create_index(
        'uq_product_cover', 'product_image', ['product_id'],
        unique=True, postgresql_where=sa.text('is_cover'),
    )

    op.add_column(
        'product_size_image',
        sa.Column('is_shopee_selected', sa.Boolean(), nullable=False, server_default='false'),
    )
    op.create_index(
        'uq_size_shopee_selected', 'product_size_image', ['product_size_id'],
        unique=True, postgresql_where=sa.text('is_shopee_selected'),
    )


def downgrade() -> None:
    op.drop_index('uq_size_shopee_selected', table_name='product_size_image')
    op.drop_column('product_size_image', 'is_shopee_selected')
    op.drop_index('uq_product_cover', table_name='product_image')
    op.drop_index('idx_product_image_product', table_name='product_image')
    op.drop_table('product_image')
