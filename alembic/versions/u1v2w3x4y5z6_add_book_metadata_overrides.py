"""add book metadata override columns

Revision ID: u1v2w3x4y5z6
Revises: t9u0v1w2x3y4
Create Date: 2026-05-04
"""

from typing import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "u1v2w3x4y5z6"
down_revision: str | Sequence[str] | None = "t9u0v1w2x3y4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    book_cols = {col["name"] for col in inspector.get_columns("books")}

    if "title_override" not in book_cols:
        op.add_column("books", sa.Column("title_override", sa.String(500), nullable=True))
    if "author_override" not in book_cols:
        op.add_column("books", sa.Column("author_override", sa.String(500), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    book_cols = {col["name"] for col in inspector.get_columns("books")}

    if "author_override" in book_cols:
        op.drop_column("books", "author_override")
    if "title_override" in book_cols:
        op.drop_column("books", "title_override")
