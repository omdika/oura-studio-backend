"""owner_account google sso

Revision ID: d7f5015901ba
Revises: 900130a6de91
Create Date: 2026-08-05 21:50:28.287361

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7f5015901ba'
down_revision: Union[str, None] = '900130a6de91'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # handoff Section 3 MIGRATION NOTE (v1.0 -> v1.1): drop password_hash, add google_sub.
    # owner_account is confirmed empty pre-migration, so this is a direct alter, no backfill needed.
    op.drop_column('owner_account', 'password_hash')
    op.add_column('owner_account', sa.Column('google_sub', sa.String(), nullable=False))
    op.create_unique_constraint('owner_account_google_sub_key', 'owner_account', ['google_sub'])


def downgrade() -> None:
    op.drop_constraint('owner_account_google_sub_key', 'owner_account', type_='unique')
    op.drop_column('owner_account', 'google_sub')
    op.add_column('owner_account', sa.Column('password_hash', sa.String(), nullable=False))
