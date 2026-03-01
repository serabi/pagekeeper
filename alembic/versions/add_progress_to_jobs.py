"""add progress to jobs

Revision ID: e7b8c9d0a1b2
Revises: 4dced57540b5
Create Date: 2026-01-17 16:45:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e7b8c9d0a1b2'
down_revision: str | Sequence[str] | None = '4dced57540b5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('jobs', sa.Column('progress', sa.Float(), nullable=True, server_default='0.0'))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('jobs', 'progress')
