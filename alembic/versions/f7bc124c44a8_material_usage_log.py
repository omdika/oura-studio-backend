"""material_usage_log table

Revision ID: f7bc124c44a8
Revises: 910a5b31c84a
Create Date: 2026-08-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f7bc124c44a8'
down_revision: Union[str, None] = '910a5b31c84a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'material_usage_log',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('material_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('material.id'), nullable=False),
        sa.Column(
            'material_purchase_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('material_purchase.id'), nullable=True
        ),
        sa.Column('product_size_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('product_size.id'), nullable=True),
        sa.Column('deducted_cm', sa.Numeric(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('source', sa.String(length=30), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_material_usage_log_material_id', 'material_usage_log', ['material_id'])


def downgrade() -> None:
    op.drop_index('ix_material_usage_log_material_id', table_name='material_usage_log')
    op.drop_table('material_usage_log')
