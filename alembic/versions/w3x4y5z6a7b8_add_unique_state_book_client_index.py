"""add unique index for state book/client pairs

Revision ID: w3x4y5z6a7b8
Revises: v2w3x4y5z6a7
Create Date: 2026-06-23
"""

from typing import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "w3x4y5z6a7b8"
down_revision: str | Sequence[str] | None = "v2w3x4y5z6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UNIQUE_INDEX = "uq_states_book_id_client_name"


def _table_exists(inspector: sa.Inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _index_exists(inspector: sa.Inspector, table_name: str, index_name: str) -> bool:
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def _has_columns(inspector: sa.Inspector, table_name: str, *column_names: str) -> bool:
    existing = {column["name"] for column in inspector.get_columns(table_name)}
    return set(column_names).issubset(existing)


def _relax_states_book_id_nullability(bind) -> None:
    bind.execute(sa.text("DROP TABLE IF EXISTS _states_nullable_new"))
    bind.execute(sa.text("""
        CREATE TABLE _states_nullable_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            abs_id VARCHAR(255) NOT NULL,
            book_id INTEGER,
            client_name VARCHAR(50) NOT NULL,
            last_updated FLOAT,
            percentage FLOAT,
            timestamp FLOAT,
            xpath TEXT,
            cfi TEXT,
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
        )
    """))
    bind.execute(sa.text("""
        INSERT INTO _states_nullable_new (id, abs_id, book_id, client_name, last_updated, percentage, timestamp, xpath, cfi)
        SELECT id, abs_id, book_id, client_name, last_updated, percentage, timestamp, xpath, cfi
        FROM states
    """))
    bind.execute(sa.text("DROP TABLE states"))
    bind.execute(sa.text("ALTER TABLE _states_nullable_new RENAME TO states"))
    bind.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_states_book_id ON states (book_id)"))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _table_exists(inspector, "states"):
        return

    if _table_exists(inspector, "books") and _has_columns(
        inspector,
        "states",
        "id",
        "abs_id",
        "book_id",
        "client_name",
        "last_updated",
        "percentage",
        "timestamp",
        "xpath",
        "cfi",
    ):
        _relax_states_book_id_nullability(bind)
        inspector = sa.inspect(bind)

    if _table_exists(inspector, "books") and _has_columns(inspector, "states", "book_id", "abs_id"):
        bind.execute(sa.text("""
            UPDATE states
            SET book_id = (
                SELECT books.id
                FROM books
                WHERE books.abs_id = states.abs_id
            )
            WHERE book_id IS NULL
              AND abs_id IS NOT NULL
              AND EXISTS (
                  SELECT 1
                  FROM books
                  WHERE books.abs_id = states.abs_id
              )
        """))

    if _has_columns(inspector, "states", "book_id", "client_name", "last_updated", "id"):
        bind.execute(sa.text("""
            DELETE FROM states
            WHERE book_id IS NOT NULL
              AND EXISTS (
                  SELECT 1
                  FROM states AS keep
                  WHERE keep.book_id = states.book_id
                    AND keep.client_name = states.client_name
                    AND (
                        COALESCE(keep.last_updated, -1) > COALESCE(states.last_updated, -1)
                        OR (
                            COALESCE(keep.last_updated, -1) = COALESCE(states.last_updated, -1)
                            AND keep.id > states.id
                        )
                    )
              )
        """))

    inspector = sa.inspect(bind)
    if _has_columns(inspector, "states", "book_id", "client_name") and not _index_exists(inspector, "states", UNIQUE_INDEX):
        op.create_index(
            UNIQUE_INDEX,
            "states",
            ["book_id", "client_name"],
            unique=True,
            sqlite_where=sa.text("book_id IS NOT NULL"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _table_exists(inspector, "states") and _index_exists(inspector, "states", UNIQUE_INDEX):
        op.drop_index(UNIQUE_INDEX, table_name="states")
