"""add missing indexes

Revision ID: v2w3x4y5z6a7
Revises: u1v2w3x4y5z6
Create Date: 2026-06-03 07:03:24

"""

from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "v2w3x4y5z6a7"
down_revision: str | Sequence[str] | None = "u1v2w3x4y5z6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


INDEXES = (
    ("books", "ix_books_ebook_filename", ["ebook_filename"]),
    ("books", "ix_books_status", ["status"]),
    ("bookfusion_highlights", "ix_bookfusion_highlights_bookfusion_book_id", ["bookfusion_book_id"]),
    ("bookfusion_highlights", "ix_bookfusion_highlights_highlighted_at", ["highlighted_at"]),
    ("pending_suggestions", "ix_pending_suggestions_status", ["status"]),
)


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _index_exists(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table_name, index_name, columns in INDEXES:
        if _table_exists(inspector, table_name) and not _index_exists(inspector, table_name, index_name):
            op.create_index(index_name, table_name, columns, unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table_name, index_name, _columns in reversed(INDEXES):
        if _table_exists(inspector, table_name) and _index_exists(inspector, table_name, index_name):
            op.drop_index(index_name, table_name=table_name)
