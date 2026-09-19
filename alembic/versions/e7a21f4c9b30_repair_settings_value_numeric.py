"""repair settings.value back to numeric

Revision ID: e7a21f4c9b30
Revises: b5d06862784d
Create Date: 2026-09-18

Live Supabase had settings.value as TEXT (out-of-band change, no migration
in repo history), while the model, schemas, and initial migration all specify
NUMERIC. SQLAlchemy's Numeric result processor received PG type OID 25 (TEXT)
and raised `InvalidRequestError: Unknown PG numeric type: 25`, making
GET /settings (and any _get_setting call in sales/production) return 500.

This is a no-op on databases already on NUMERIC.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e7a21f4c9b30'
down_revision: Union[str, None] = 'b5d06862784d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.settings "
        "ALTER COLUMN value TYPE numeric USING value::numeric"
    )


def downgrade() -> None:
    # TEXT was the broken state; downgrade kept only for symmetry.
    op.execute(
        "ALTER TABLE public.settings "
        "ALTER COLUMN value TYPE text USING value::text"
    )
