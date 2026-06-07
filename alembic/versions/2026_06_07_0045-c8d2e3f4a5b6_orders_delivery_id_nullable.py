"""order-first: make orders.delivery_id nullable

In the order-first model an order exists on its own (status PENDING, no run) and
is later assigned to a delivery. This relaxes orders.delivery_id to NULL-able.

Revision ID: c8d2e3f4a5b6
Revises: b7c1d2e3f4a5
Create Date: 2026-06-07 00:45:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8d2e3f4a5b6'
down_revision: Union[str, None] = 'b7c1d2e3f4a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.alter_column('delivery_id', existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    # Un-assigned orders cannot exist under the old NOT NULL constraint; drop them
    # so the column can be made NOT NULL again.
    op.execute("DELETE FROM orders WHERE delivery_id IS NULL")
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.alter_column('delivery_id', existing_type=sa.Integer(), nullable=False)
