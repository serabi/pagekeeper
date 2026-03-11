"""add tbr enrichment columns

Revision ID: g8h9i0j1k2l3
Revises: f7a8b9c0d1e2
Create Date: 2026-03-10
"""

from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'g8h9i0j1k2l3'
down_revision: str | Sequence[str] | None = 'f7a8b9c0d1e2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENRICHMENT_COLUMNS = [
    ('description', sa.Text()),
    ('page_count', sa.Integer()),
    ('rating', sa.Float()),
    ('ratings_count', sa.Integer()),
    ('release_year', sa.Integer()),
    ('genres', sa.Text()),
    ('subtitle', sa.String(500)),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if 'tbr_items' not in inspector.get_table_names():
        return

    existing = {col['name'] for col in inspector.get_columns('tbr_items')}
    for name, col_type in ENRICHMENT_COLUMNS:
        if name not in existing:
            op.add_column('tbr_items', sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if 'tbr_items' not in inspector.get_table_names():
        return

    existing = {col['name'] for col in inspector.get_columns('tbr_items')}
    for name, _ in reversed(ENRICHMENT_COLUMNS):
        if name in existing:
            op.drop_column('tbr_items', name)
