"""add_role_and_invitation

Revision ID: 1bbae0f66ab4
Revises: f7bc124c44a8
Create Date: 2026-09-04 09:18:49.070686

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1bbae0f66ab4'
down_revision: Union[str, None] = 'f7bc124c44a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('invitation',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('email', sa.String(), nullable=False),
        sa.Column('code', sa.String(), nullable=False),
        sa.Column('role', sa.String(), server_default='member', nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('is_used', sa.Boolean(), server_default='false', nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['owner_account.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
        sa.UniqueConstraint('email')
    )
    op.add_column('owner_account', sa.Column('role', sa.String(), server_default='member', nullable=False))


def downgrade() -> None:
    op.drop_column('owner_account', 'role')
    op.drop_table('invitation')
