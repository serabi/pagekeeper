"""add tbr_items table

Revision ID: f7a8b9c0d1e2
Revises: add_storyteller_submission_table
Create Date: 2026-03-10
"""

from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f7a8b9c0d1e2'
down_revision: str | Sequence[str] | None = 'add_storyteller_submission_table'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if 'tbr_items' not in inspector.get_table_names():
        op.create_table(
            'tbr_items',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('title', sa.String(500), nullable=False),
            sa.Column('author', sa.String(500), nullable=True),
            sa.Column('cover_url', sa.String(500), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('priority', sa.Integer(), server_default='0'),
            sa.Column('added_at', sa.DateTime(), nullable=True),
            sa.Column('hardcover_book_id', sa.Integer(), nullable=True),
            sa.Column('hardcover_slug', sa.String(255), nullable=True),
            sa.Column('source', sa.String(50), server_default='manual'),
            sa.Column('ol_work_key', sa.String(255), nullable=True),
            sa.Column('isbn', sa.String(20), nullable=True),
            sa.Column('hardcover_list_id', sa.Integer(), nullable=True),
            sa.Column('hardcover_list_name', sa.String(500), nullable=True),
            sa.Column('book_abs_id', sa.String(255), nullable=True),
            sa.ForeignKeyConstraint(['book_abs_id'], ['books.abs_id'], ondelete='SET NULL'),
        )
        op.create_index('ix_tbr_items_hardcover_book_id', 'tbr_items', ['hardcover_book_id'])
        op.create_index('ix_tbr_items_book_abs_id', 'tbr_items', ['book_abs_id'])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if 'tbr_items' in inspector.get_table_names():
        op.drop_index('ix_tbr_items_book_abs_id', table_name='tbr_items')
        op.drop_index('ix_tbr_items_hardcover_book_id', table_name='tbr_items')
        op.drop_table('tbr_items')
