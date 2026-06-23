"""Add detected_books table.

Revision ID: s1t2u3v4w5x6
Revises: r5s6t7u8v9w0
Create Date: 2026-04-05
"""

from typing import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "s1t2u3v4w5x6"
down_revision: str = "r5s6t7u8v9w0"
branch_labels: Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "detected_books",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False, server_default="abs"),
        sa.Column("source_id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("author", sa.String(length=500), nullable=True),
        sa.Column("cover_url", sa.String(length=500), nullable=True),
        sa.Column("progress_percentage", sa.Float(), nullable=False, server_default="0"),
        sa.Column("first_detected_at", sa.DateTime(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True, server_default="detected"),
        sa.Column("matches_json", sa.Text(), nullable=True),
        sa.Column("device", sa.String(length=128), nullable=True),
        sa.Column("ebook_filename", sa.String(length=500), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "source", name="uq_detected_books_source_id_source"),
    )
    op.create_index("ix_detected_books_source", "detected_books", ["source"], unique=False)
    op.create_index("ix_detected_books_status", "detected_books", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_detected_books_status", table_name="detected_books")
    op.drop_index("ix_detected_books_source", table_name="detected_books")
    op.drop_table("detected_books")
