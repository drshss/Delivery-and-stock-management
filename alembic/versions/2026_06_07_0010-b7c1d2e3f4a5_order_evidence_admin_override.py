"""order evidence admin override

Adds an audit trail for an admin completing an order without the mandatory
proof-of-delivery photo: the justification comment, who overrode it, and when.

Revision ID: b7c1d2e3f4a5
Revises: 0c45042b5a92
Create Date: 2026-06-07 00:10:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c1d2e3f4a5'
down_revision: Union[str, None] = '0c45042b5a92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('evidence_override_reason', sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column(
                'evidence_overridden_by',
                sa.Integer(),
                sa.ForeignKey('users.id', name='fk_orders_evidence_overridden_by_users'),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column('evidence_overridden_at', sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_column('evidence_overridden_at')
        batch_op.drop_column('evidence_overridden_by')
        batch_op.drop_column('evidence_override_reason')
