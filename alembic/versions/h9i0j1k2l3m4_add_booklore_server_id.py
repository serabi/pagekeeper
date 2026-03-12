"""add booklore server_id and qualify booklore_id

Revision ID: h9i0j1k2l3m4
Revises: g8h9i0j1k2l3
Create Date: 2026-03-11
"""

from typing import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'h9i0j1k2l3m4'
down_revision: str | Sequence[str] | None = 'g8h9i0j1k2l3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if 'booklore_books' not in inspector.get_table_names():
        return

    existing = {col['name'] for col in inspector.get_columns('booklore_books')}

    # 1. Add server_id column
    if 'server_id' not in existing:
        op.add_column('booklore_books', sa.Column('server_id', sa.String(50), nullable=False, server_default='default'))

    # 2. Drop old unique constraint on filename and add composite
    # SQLite doesn't support DROP CONSTRAINT, so we use batch mode
    with op.batch_alter_table('booklore_books') as batch_op:
        # Try to drop the old unique index on filename
        try:
            batch_op.drop_constraint('uq_booklore_books_filename', type_='unique')
        except Exception:
            pass
        # Create composite unique constraint
        batch_op.create_unique_constraint('uq_booklore_server_filename', ['server_id', 'filename'])

    # 3. Qualify existing booklore_id values in kosync_documents
    if 'kosync_documents' in inspector.get_table_names():
        kosync_cols = {col['name'] for col in inspector.get_columns('kosync_documents')}
        if 'booklore_id' in kosync_cols:
            op.execute(
                "UPDATE kosync_documents SET booklore_id = 'default:' || booklore_id "
                "WHERE booklore_id IS NOT NULL AND booklore_id NOT LIKE '%:%'"
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if 'booklore_books' not in inspector.get_table_names():
        return

    # Reverse booklore_id qualification
    if 'kosync_documents' in inspector.get_table_names():
        kosync_cols = {col['name'] for col in inspector.get_columns('kosync_documents')}
        if 'booklore_id' in kosync_cols:
            op.execute(
                "UPDATE kosync_documents SET booklore_id = SUBSTR(booklore_id, INSTR(booklore_id, ':') + 1) "
                "WHERE booklore_id IS NOT NULL AND booklore_id LIKE '%:%'"
            )

    with op.batch_alter_table('booklore_books') as batch_op:
        batch_op.drop_constraint('uq_booklore_server_filename', type_='unique')
        batch_op.create_unique_constraint('uq_booklore_books_filename', ['filename'])

    existing = {col['name'] for col in inspector.get_columns('booklore_books')}
    if 'server_id' in existing:
        op.drop_column('booklore_books', 'server_id')
