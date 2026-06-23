"""merge grimmory rename, bookfusion cleanup, and detected books heads

Revision ID: t9u0v1w2x3y4
Revises: 5308a8e2c930, s1t2u3v4w5x6
Create Date: 2026-04-25
"""

from collections.abc import Sequence

revision: str = "t9u0v1w2x3y4"
down_revision: str | Sequence[str] | None = ("5308a8e2c930", "s1t2u3v4w5x6")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Merge parallel heads into a single linear history."""


def downgrade() -> None:
    """Unmerge the parallel heads."""
