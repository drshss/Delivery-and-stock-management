"""add orders.invoice_number (optional billing reference)

Optional invoice/billing number an admin or stock manager attaches to an order,
either at creation time or later once the invoice is issued. Indexed for lookup;
not unique (allows corrections / re-issues).

Revision ID: e0f4a5b6c7d8
Revises: d9e3f4a5b6c7
Create Date: 2026-06-10 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e0f4a5b6c7d8'
down_revision: Union[str, None] = 'd9e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('orders', sa.Column('invoice_number', sa.String(length=100), nullable=True))
    op.create_index(op.f('ix_orders_invoice_number'), 'orders', ['invoice_number'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_orders_invoice_number'), table_name='orders')
    op.drop_column('orders', 'invoice_number')
