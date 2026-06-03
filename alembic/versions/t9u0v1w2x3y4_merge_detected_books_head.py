"""merge grimmory rename and bookfusion cleanup heads

Revision ID: t9u0v1w2x3y4
Revises: 5308a8e2c930, r5s6t7u8v9w0
Create Date: 2026-04-25
"""

from collections.abc import Sequence

revision: str = "t9u0v1w2x3y4"
down_revision: str | Sequence[str] | None = ("5308a8e2c930", "r5s6t7u8v9w0")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Merge parallel heads into a single linear history."""


def downgrade() -> None:
    """Unmerge the parallel heads."""
