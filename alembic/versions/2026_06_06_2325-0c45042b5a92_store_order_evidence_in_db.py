"""store order evidence in db

Revision ID: 0c45042b5a92
Revises: 94e331d57c4e
Create Date: 2026-06-06 23:25:18.623330
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0c45042b5a92'
down_revision: Union[str, None] = '94e331d57c4e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Old evidence referenced files on (ephemeral) local disk via `file_path`;
    # those bytes cannot be recovered into the new DB-backed columns, so purge
    # any existing rows before adding the NOT NULL columns. (No production
    # evidence exists yet; agents simply re-upload if needed.)
    op.execute("DELETE FROM order_evidences")
    with op.batch_alter_table('order_evidences', schema=None) as batch_op:
        batch_op.add_column(sa.Column('filename', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('content_type', sa.String(length=100), nullable=False))
        batch_op.add_column(sa.Column('size_bytes', sa.Integer(), nullable=False))
        batch_op.add_column(sa.Column('data', sa.LargeBinary(), nullable=False))
        batch_op.drop_column('file_path')


def downgrade() -> None:
    op.execute("DELETE FROM order_evidences")
    with op.batch_alter_table('order_evidences', schema=None) as batch_op:
        batch_op.add_column(sa.Column('file_path', sa.VARCHAR(length=500), nullable=False))
        batch_op.drop_column('data')
        batch_op.drop_column('size_bytes')
        batch_op.drop_column('content_type')
        batch_op.drop_column('filename')
