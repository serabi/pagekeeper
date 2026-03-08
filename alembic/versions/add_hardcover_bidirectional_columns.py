"""add hardcover bidirectional sync columns

Revision ID: add_hardcover_bidirectional_columns
Revises: d6e7f8a9b0c1
Create Date: 2026-03-07 12:00:00.000000

"""
from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'add_hardcover_bidirectional_columns'
down_revision: str | Sequence[str] | None = 'd6e7f8a9b0c1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add cached columns for Hardcover bidirectional sync."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c['name'] for c in inspector.get_columns('hardcover_details')]

    new_columns = {
        'hardcover_user_book_id': sa.Column('hardcover_user_book_id', sa.Integer(), nullable=True),
        'hardcover_user_book_read_id': sa.Column('hardcover_user_book_read_id', sa.Integer(), nullable=True),
        'hardcover_status_id': sa.Column('hardcover_status_id', sa.Integer(), nullable=True),
        'hardcover_audio_edition_id': sa.Column('hardcover_audio_edition_id', sa.String(255), nullable=True),
    }

    for col_name, col_def in new_columns.items():
        if col_name not in columns:
            op.add_column('hardcover_details', col_def)


def downgrade() -> None:
    """Remove bidirectional sync columns."""
    for col_name in ('hardcover_user_book_id', 'hardcover_user_book_read_id',
                     'hardcover_status_id', 'hardcover_audio_edition_id'):
        op.drop_column('hardcover_details', col_name)
