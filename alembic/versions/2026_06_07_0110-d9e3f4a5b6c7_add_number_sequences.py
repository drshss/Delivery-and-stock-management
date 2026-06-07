"""add number_sequences (daily-reset order/delivery numbering)

Backs the gapless, race-safe human-readable numbers ORD-YYYYMMDD-0001 /
DLV-YYYYMMDD-0001. One row per (scope, period), bumped via an atomic upsert.

Revision ID: d9e3f4a5b6c7
Revises: c8d2e3f4a5b6
Create Date: 2026-06-07 01:10:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd9e3f4a5b6c7'
down_revision: Union[str, None] = 'c8d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'number_sequences',
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('last_value', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('key'),
    )


def downgrade() -> None:
    op.drop_table('number_sequences')
