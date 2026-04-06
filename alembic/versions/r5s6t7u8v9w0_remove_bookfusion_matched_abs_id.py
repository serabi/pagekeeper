"""Remove matched_abs_id columns from BookFusion tables.

Revision ID: r5s6t7u8v9w0
Revises: p6q7r8s9t0u1
Create Date: 2026-04-05
"""

import sqlalchemy as sa

from alembic import op

revision = "r5s6t7u8v9w0"
down_revision = "p6q7r8s9t0u1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("bookfusion_books") as batch_op:
        batch_op.drop_column("matched_abs_id")

    with op.batch_alter_table("bookfusion_highlights") as batch_op:
        batch_op.drop_column("matched_abs_id")


def downgrade():
    op.add_column("bookfusion_books", sa.Column("matched_abs_id", sa.String(255), nullable=True))
    op.add_column("bookfusion_highlights", sa.Column("matched_abs_id", sa.String(255), nullable=True))
