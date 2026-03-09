"""add journal_sync column to hardcover_details

Revision ID: j1s2y3n4c5o6
Revises:
Create Date: 2026-03-08
"""
import sqlalchemy as sa
from alembic import op

revision = 'j1s2y3n4c5o6'
down_revision = 'add_hardcover_sync_log_table'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('hardcover_details') as batch_op:
        batch_op.add_column(sa.Column('journal_sync', sa.String(10), nullable=True))


def downgrade():
    with op.batch_alter_table('hardcover_details') as batch_op:
        batch_op.drop_column('journal_sync')
