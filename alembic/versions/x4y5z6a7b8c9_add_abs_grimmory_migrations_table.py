"""Add abs_grimmory_migrations table.

Revision ID: x4y5z6a7b8c9
Revises: w3x4y5z6a7b8
Create Date: 2026-06-24
"""

from typing import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "x4y5z6a7b8c9"
down_revision: str = "w3x4y5z6a7b8"
branch_labels: Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "abs_grimmory_migrations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("abs_id", sa.String(length=255), nullable=False),
        sa.Column("book_title", sa.String(length=500), nullable=True),
        sa.Column("grimmory_book_id", sa.String(length=64), nullable=True),
        sa.Column("grimmory_instance_id", sa.String(length=50), nullable=True, server_default="default"),
        sa.Column("matched_by", sa.String(length=50), nullable=True),
        sa.Column("finished_at", sa.String(length=10), nullable=True),
        sa.Column("sessions_written", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("bookmarks_written", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("outcome", sa.String(length=20), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "abs_id", "grimmory_book_id", "grimmory_instance_id", name="uq_abs_grimmory_migration"
        ),
    )
    op.create_index("ix_abs_grimmory_migrations_abs_id", "abs_grimmory_migrations", ["abs_id"], unique=False)
    op.create_index("ix_abs_grimmory_migrations_outcome", "abs_grimmory_migrations", ["outcome"], unique=False)
    op.create_index(
        "ix_abs_grimmory_migrations_created_at", "abs_grimmory_migrations", ["created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_abs_grimmory_migrations_created_at", table_name="abs_grimmory_migrations")
    op.drop_index("ix_abs_grimmory_migrations_outcome", table_name="abs_grimmory_migrations")
    op.drop_index("ix_abs_grimmory_migrations_abs_id", table_name="abs_grimmory_migrations")
    op.drop_table("abs_grimmory_migrations")
