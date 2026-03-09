"""add hardcover_sync_logs table

Revision ID: add_hardcover_sync_log_table
Revises: add_hardcover_bidirectional_columns
Create Date: 2026-03-08 12:00:00.000000

"""
from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'add_hardcover_sync_log_table'
down_revision: str | Sequence[str] | None = 'add_hardcover_bidirectional_columns'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if 'hardcover_sync_logs' in inspector.get_table_names():
        return

    op.create_table(
        'hardcover_sync_logs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('abs_id', sa.String(255), sa.ForeignKey('books.abs_id', ondelete='SET NULL'), nullable=True),
        sa.Column('book_title', sa.String(500), nullable=True),
        sa.Column('direction', sa.String(4), nullable=False),
        sa.Column('action', sa.String(30), nullable=False),
        sa.Column('detail', sa.Text(), nullable=True),
        sa.Column('success', sa.Boolean(), server_default=sa.true()),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('ix_hardcover_sync_logs_abs_id', 'hardcover_sync_logs', ['abs_id'])
    op.create_index('ix_hardcover_sync_logs_action', 'hardcover_sync_logs', ['action'])
    op.create_index('ix_hardcover_sync_logs_created_at', 'hardcover_sync_logs', ['created_at'])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if 'hardcover_sync_logs' in inspector.get_table_names():
        op.drop_table('hardcover_sync_logs')
