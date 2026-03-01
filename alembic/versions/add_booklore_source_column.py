"""add source column to booklore_books

Revision ID: a7b8c9d0e1f2
Revises: e4a1c2d9f7b3
Create Date: 2026-03-01
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7b8c9d0e1f2'
down_revision: str | Sequence[str] | None = 'e4a1c2d9f7b3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if 'booklore_books' not in inspector.get_table_names():
        return

    columns = {c['name'] for c in inspector.get_columns('booklore_books')}

    if 'source' not in columns:
        op.add_column('booklore_books', sa.Column('source', sa.String(50), server_default='booklore'))

    # Rebuild the table with the new composite unique constraint via batch mode.
    # SQLite requires table recreation to change constraints. We pass the full
    # desired schema so batch mode can recreate the table correctly, regardless
    # of what constraint names existed before.
    with op.batch_alter_table(
        'booklore_books',
        recreate='always',
        table_args=[
            sa.UniqueConstraint('filename', 'source', name='uq_booklore_books_filename_source'),
        ],
    ) as _batch_op:
        pass  # Table is recreated with the new constraint


def downgrade() -> None:
    # Intentionally non-reversible: the composite unique constraint
    # (filename, source) cannot be safely reverted without data loss.
    pass
