"""add storyteller_submissions table

Revision ID: add_storyteller_submission_table
Revises: j1s2y3n4c5o6
Create Date: 2026-03-09
"""
from typing import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'add_storyteller_submission_table'
down_revision: str | Sequence[str] | None = 'j1s2y3n4c5o6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if 'storyteller_submissions' in inspector.get_table_names():
        return

    op.create_table(
        'storyteller_submissions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('abs_id', sa.String(255), sa.ForeignKey('books.abs_id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='queued'),
        sa.Column('submission_dir', sa.String(500), nullable=True),
        sa.Column('storyteller_uuid', sa.String(36), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('submitted_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('last_checked_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_storyteller_submissions_abs_id', 'storyteller_submissions', ['abs_id'])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if 'storyteller_submissions' in inspector.get_table_names():
        op.drop_table('storyteller_submissions')
