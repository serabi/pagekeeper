"""add unique index on (source_id, source) to pending_suggestions

Ensures that the same source_id can exist for different source types
without collision.

Revision ID: p6q7r8s9t0u1
Revises: o7p8q9r0s1t2
Create Date: 2026-03-19
"""

from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'p6q7r8s9t0u1'
down_revision: str = 'o7p8q9r0s1t2'
branch_labels: Sequence[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    try:
        op.create_index(
            'ix_pending_suggestions_source_id_source',
            'pending_suggestions',
            ['source_id', 'source'],
            unique=True,
        )
    except sa.exc.OperationalError:
        pass  # Index already exists (idempotent)


def downgrade() -> None:
    try:
        op.drop_index('ix_pending_suggestions_source_id_source', table_name='pending_suggestions')
    except sa.exc.OperationalError:
        pass
