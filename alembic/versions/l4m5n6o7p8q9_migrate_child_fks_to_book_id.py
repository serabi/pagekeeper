"""migrate child table FKs from abs_id to book_id

Phase 2 of abs_id decoupling: adds book_id integer FK to all 11 child
tables, populates from books.id via abs_id lookup.  Also adds id PK to
hardcover_details and book_alignments (which used abs_id as PK+FK).

Safe for production: handles re-runs (column-exists checks), cleans up
temp tables from interrupted attempts, and gracefully skips orphaned
child rows whose abs_id has no matching book.

Revision ID: l4m5n6o7p8q9
Revises: k3l4m5n6o7p8
Create Date: 2026-03-17
"""

import logging
from typing import Sequence

import sqlalchemy as sa
from sqlalchemy.exc import OperationalError

from alembic import op

logger = logging.getLogger(__name__)

revision: str = 'l4m5n6o7p8q9'
down_revision: str | Sequence[str] | None = 'k3l4m5n6o7p8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_exists(conn, table, column):
    """Check if a column already exists in a table (safe for re-runs)."""
    cols = {c['name'] for c in sa.inspect(conn).get_columns(table)}
    return column in cols


def _column_fully_migrated(conn, table, column):
    """Check if a book_id column exists AND is fully populated (not just added by _ensure_model_columns)."""
    if not _column_exists(conn, table, column):
        return False
    # If any non-NULL values exist, the migration has run
    result = conn.execute(sa.text(
        f"SELECT COUNT(*) FROM [{table}] WHERE [{column}] IS NOT NULL"
    )).scalar()
    return result > 0 or conn.execute(sa.text(f"SELECT COUNT(*) FROM [{table}]")).scalar() == 0


def _log_orphan_count(conn, table: str, fk_col: str = "abs_id") -> None:
    """Log a warning if any child rows will be dropped due to missing parent book."""
    count = conn.execute(sa.text(
        f"SELECT COUNT(*) FROM [{table}] c "
        f"LEFT JOIN books b ON b.abs_id = c.[{fk_col}] "
        f"WHERE b.id IS NULL"
    )).scalar()
    if count:
        logger.error(
            "Dropping %d orphaned row(s) from '%s' with no matching book",
            count,
            table,
        )


def upgrade() -> None:
    conn = op.get_bind()

    # ── Group 1: NOT NULL FK tables (CASCADE) ──
    # Uses explicit SQL recreation (not batch_alter_table) to avoid
    # FK constraint naming issues with SQLite reflection.
    _upgrade_states(conn)
    _upgrade_jobs(conn)
    _upgrade_reading_journals(conn)
    _upgrade_storyteller_submissions(conn)

    # ── Group 2: PK+FK tables (abs_id was PK — recreate with id PK) ──
    _upgrade_hardcover_details(conn)
    _upgrade_book_alignments(conn)

    # ── Group 3: Nullable FK tables ──
    _upgrade_nullable_tables(conn)


def _upgrade_states(conn) -> None:
    if _column_fully_migrated(conn, 'states', 'book_id'):
        return
    _log_orphan_count(conn, "states")
    conn.execute(sa.text("DROP TABLE IF EXISTS _states_new"))
    conn.execute(sa.text("""
        CREATE TABLE _states_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            abs_id VARCHAR(255) NOT NULL,
            book_id INTEGER NOT NULL,
            client_name VARCHAR(50) NOT NULL,
            last_updated FLOAT,
            percentage FLOAT,
            timestamp FLOAT,
            xpath TEXT,
            cfi TEXT,
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
        )
    """))
    conn.execute(sa.text("""
        INSERT INTO _states_new (id, abs_id, book_id, client_name, last_updated, percentage, timestamp, xpath, cfi)
        SELECT s.id, s.abs_id, b.id, s.client_name, s.last_updated, s.percentage, s.timestamp, s.xpath, s.cfi
        FROM states s JOIN books b ON b.abs_id = s.abs_id
    """))
    conn.execute(sa.text("DROP TABLE states"))
    conn.execute(sa.text("ALTER TABLE _states_new RENAME TO states"))
    conn.execute(sa.text("CREATE INDEX ix_states_book_id ON states (book_id)"))


def _upgrade_jobs(conn) -> None:
    if _column_fully_migrated(conn, "jobs", "book_id"):
        return
    _log_orphan_count(conn, "jobs")
    conn.execute(sa.text("DROP TABLE IF EXISTS _jobs_new"))
    conn.execute(sa.text("""
        CREATE TABLE _jobs_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            abs_id VARCHAR(255) NOT NULL,
            book_id INTEGER NOT NULL,
            last_attempt FLOAT,
            retry_count INTEGER DEFAULT 0,
            last_error TEXT,
            progress FLOAT DEFAULT 0.0,
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
        )
    """))
    conn.execute(sa.text("""
        INSERT INTO _jobs_new (id, abs_id, book_id, last_attempt, retry_count, last_error, progress)
        SELECT j.id, j.abs_id, b.id, j.last_attempt, j.retry_count, j.last_error, j.progress
        FROM jobs j JOIN books b ON b.abs_id = j.abs_id
    """))
    conn.execute(sa.text("DROP TABLE jobs"))
    conn.execute(sa.text("ALTER TABLE _jobs_new RENAME TO jobs"))
    conn.execute(sa.text("CREATE INDEX ix_jobs_book_id ON jobs (book_id)"))


def _upgrade_reading_journals(conn) -> None:
    if _column_fully_migrated(conn, "reading_journals", "book_id"):
        return
    _log_orphan_count(conn, "reading_journals")
    conn.execute(sa.text("DROP TABLE IF EXISTS _reading_journals_new"))
    conn.execute(sa.text("""
        CREATE TABLE _reading_journals_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            abs_id VARCHAR(255) NOT NULL,
            book_id INTEGER NOT NULL,
            event VARCHAR(20) NOT NULL,
            entry TEXT,
            percentage FLOAT,
            created_at DATETIME,
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
        )
    """))
    conn.execute(sa.text("""
        INSERT INTO _reading_journals_new (id, abs_id, book_id, event, entry, percentage, created_at)
        SELECT rj.id, rj.abs_id, b.id, rj.event, rj.entry, rj.percentage, rj.created_at
        FROM reading_journals rj JOIN books b ON b.abs_id = rj.abs_id
    """))
    conn.execute(sa.text("DROP TABLE reading_journals"))
    conn.execute(sa.text("ALTER TABLE _reading_journals_new RENAME TO reading_journals"))
    conn.execute(sa.text("CREATE INDEX ix_reading_journals_book_id ON reading_journals (book_id)"))


def _upgrade_storyteller_submissions(conn) -> None:
    if _column_fully_migrated(conn, "storyteller_submissions", "book_id"):
        return
    _log_orphan_count(conn, "storyteller_submissions")
    conn.execute(sa.text("DROP TABLE IF EXISTS _storyteller_submissions_new"))
    conn.execute(sa.text("""
        CREATE TABLE _storyteller_submissions_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            abs_id VARCHAR(255) NOT NULL,
            book_id INTEGER NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'queued',
            submission_dir VARCHAR(500),
            storyteller_uuid VARCHAR(36),
            error TEXT,
            submitted_at DATETIME,
            last_checked_at DATETIME,
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
        )
    """))
    conn.execute(sa.text("""
        INSERT INTO _storyteller_submissions_new (id, abs_id, book_id, status, submission_dir, storyteller_uuid, error, submitted_at, last_checked_at)
        SELECT ss.id, ss.abs_id, b.id, ss.status, ss.submission_dir, ss.storyteller_uuid, ss.error, ss.submitted_at, ss.last_checked_at
        FROM storyteller_submissions ss JOIN books b ON b.abs_id = ss.abs_id
    """))
    conn.execute(sa.text("DROP TABLE storyteller_submissions"))
    conn.execute(sa.text("ALTER TABLE _storyteller_submissions_new RENAME TO storyteller_submissions"))
    conn.execute(sa.text("CREATE INDEX ix_storyteller_submissions_book_id ON storyteller_submissions (book_id)"))


def _upgrade_hardcover_details(conn) -> None:
    # Clean up from interrupted previous attempt
    conn.execute(sa.text("DROP TABLE IF EXISTS _hardcover_details_new"))

    # Check if already migrated (has id column = new schema)
    if _column_fully_migrated(conn, 'hardcover_details', 'book_id'):
        return
    _log_orphan_count(conn, "hardcover_details")

    conn.execute(sa.text("""
        CREATE TABLE _hardcover_details_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            abs_id VARCHAR(255),
            hardcover_book_id VARCHAR(255),
            hardcover_slug VARCHAR(255),
            hardcover_edition_id VARCHAR(255),
            hardcover_pages INTEGER,
            hardcover_audio_seconds INTEGER,
            isbn VARCHAR(255),
            asin VARCHAR(255),
            matched_by VARCHAR(50),
            hardcover_cover_url VARCHAR(500),
            hardcover_user_book_id INTEGER,
            hardcover_user_book_read_id INTEGER,
            hardcover_status_id INTEGER,
            hardcover_audio_edition_id VARCHAR(255),
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
            UNIQUE(book_id)
        )
    """))
    conn.execute(sa.text("""
        INSERT INTO _hardcover_details_new (
            book_id, abs_id, hardcover_book_id, hardcover_slug,
            hardcover_edition_id, hardcover_pages, hardcover_audio_seconds,
            isbn, asin, matched_by, hardcover_cover_url,
            hardcover_user_book_id, hardcover_user_book_read_id,
            hardcover_status_id, hardcover_audio_edition_id
        )
        SELECT b.id, hd.abs_id, hd.hardcover_book_id, hd.hardcover_slug,
            hd.hardcover_edition_id, hd.hardcover_pages, hd.hardcover_audio_seconds,
            hd.isbn, hd.asin, hd.matched_by, hd.hardcover_cover_url,
            hd.hardcover_user_book_id, hd.hardcover_user_book_read_id,
            hd.hardcover_status_id, hd.hardcover_audio_edition_id
        FROM hardcover_details hd
        JOIN books b ON b.abs_id = hd.abs_id
    """))
    conn.execute(sa.text("DROP TABLE hardcover_details"))
    conn.execute(sa.text("ALTER TABLE _hardcover_details_new RENAME TO hardcover_details"))
    conn.execute(sa.text(
        "CREATE UNIQUE INDEX ix_hardcover_details_book_id ON hardcover_details (book_id)"))


def _upgrade_book_alignments(conn) -> None:
    conn.execute(sa.text("DROP TABLE IF EXISTS _book_alignments_new"))

    if _column_fully_migrated(conn, 'book_alignments', 'book_id'):
        return
    _log_orphan_count(conn, "book_alignments")

    conn.execute(sa.text("""
        CREATE TABLE _book_alignments_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            abs_id VARCHAR(255),
            alignment_map_json TEXT NOT NULL,
            source VARCHAR(20),
            last_updated DATETIME,
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
            UNIQUE(book_id)
        )
    """))
    conn.execute(sa.text("""
        INSERT INTO _book_alignments_new (
            book_id, abs_id, alignment_map_json, source, last_updated
        )
        SELECT b.id, ba.abs_id, ba.alignment_map_json, ba.source, ba.last_updated
        FROM book_alignments ba
        JOIN books b ON b.abs_id = ba.abs_id
    """))
    conn.execute(sa.text("DROP TABLE book_alignments"))
    conn.execute(sa.text("ALTER TABLE _book_alignments_new RENAME TO book_alignments"))
    conn.execute(sa.text(
        "CREATE UNIQUE INDEX ix_book_alignments_book_id ON book_alignments (book_id)"))


def _upgrade_nullable_tables(conn) -> None:
    nullable_tables = [
        ('hardcover_sync_logs', 'abs_id', 'book_id'),
        ('tbr_items', 'book_abs_id', 'book_id'),
        ('kosync_documents', 'linked_abs_id', 'linked_book_id'),
        ('bookfusion_highlights', 'matched_abs_id', 'matched_book_id'),
        ('bookfusion_books', 'matched_abs_id', 'matched_book_id'),
    ]
    for table, old_col, new_col in nullable_tables:
        if not _column_exists(conn, table, new_col):
            op.add_column(table, sa.Column(new_col, sa.Integer(), nullable=True))
        op.execute(sa.text(
            f"UPDATE [{table}] SET [{new_col}] = "
            f"(SELECT id FROM books WHERE books.abs_id = [{table}].[{old_col}]) "
            f"WHERE [{old_col}] IS NOT NULL AND [{new_col}] IS NULL"
        ))
        orphan_count = conn.execute(sa.text(
            f"SELECT COUNT(*) FROM [{table}] WHERE [{old_col}] IS NOT NULL AND [{new_col}] IS NULL"
        )).scalar()
        if orphan_count:
            logger.warning("%d row(s) in '%s' could not be linked to a book", orphan_count, table)
        # Create index if it doesn't exist (safe for re-runs)
        try:
            op.create_index(f'ix_{table}_{new_col}', table, [new_col])
        except OperationalError:
            pass  # Index already exists from a previous partial run


def downgrade() -> None:
    conn = op.get_bind()

    # Drop new columns from nullable tables
    for table, new_col in [
        ('bookfusion_books', 'matched_book_id'),
        ('bookfusion_highlights', 'matched_book_id'),
        ('kosync_documents', 'linked_book_id'),
        ('tbr_items', 'book_id'),
        ('hardcover_sync_logs', 'book_id'),
    ]:
        if _column_exists(conn, table, new_col):
            try:
                op.drop_index(f'ix_{table}_{new_col}', table_name=table)
            except OperationalError:
                pass
            with op.batch_alter_table(table, recreate='always') as batch_op:
                batch_op.drop_column(new_col)

    # Restore hardcover_details with abs_id PK
    conn.execute(sa.text("DROP TABLE IF EXISTS _hardcover_details_old"))
    conn.execute(sa.text("""
        CREATE TABLE _hardcover_details_old (
            abs_id VARCHAR(255) PRIMARY KEY,
            hardcover_book_id VARCHAR(255),
            hardcover_slug VARCHAR(255),
            hardcover_edition_id VARCHAR(255),
            hardcover_pages INTEGER,
            hardcover_audio_seconds INTEGER,
            isbn VARCHAR(255),
            asin VARCHAR(255),
            matched_by VARCHAR(50),
            hardcover_cover_url VARCHAR(500),
            hardcover_user_book_id INTEGER,
            hardcover_user_book_read_id INTEGER,
            hardcover_status_id INTEGER,
            hardcover_audio_edition_id VARCHAR(255),
            FOREIGN KEY(abs_id) REFERENCES books(abs_id) ON DELETE CASCADE
        )
    """))
    conn.execute(sa.text("""
        INSERT INTO _hardcover_details_old
        SELECT abs_id, hardcover_book_id, hardcover_slug,
            hardcover_edition_id, hardcover_pages, hardcover_audio_seconds,
            isbn, asin, matched_by, hardcover_cover_url,
            hardcover_user_book_id, hardcover_user_book_read_id,
            hardcover_status_id, hardcover_audio_edition_id
        FROM hardcover_details WHERE abs_id IS NOT NULL
    """))
    conn.execute(sa.text("DROP TABLE hardcover_details"))
    conn.execute(sa.text("ALTER TABLE _hardcover_details_old RENAME TO hardcover_details"))

    # Restore book_alignments with abs_id PK
    conn.execute(sa.text("DROP TABLE IF EXISTS _book_alignments_old"))
    conn.execute(sa.text("""
        CREATE TABLE _book_alignments_old (
            abs_id VARCHAR(255) PRIMARY KEY,
            alignment_map_json TEXT NOT NULL,
            source VARCHAR(20),
            last_updated DATETIME,
            FOREIGN KEY(abs_id) REFERENCES books(abs_id) ON DELETE CASCADE
        )
    """))
    conn.execute(sa.text("""
        INSERT INTO _book_alignments_old
        SELECT abs_id, alignment_map_json, source, last_updated
        FROM book_alignments WHERE abs_id IS NOT NULL
    """))
    conn.execute(sa.text("DROP TABLE book_alignments"))
    conn.execute(sa.text("ALTER TABLE _book_alignments_old RENAME TO book_alignments"))

    # Drop book_id from NOT NULL tables
    for table in ('storyteller_submissions', 'reading_journals', 'jobs', 'states'):
        if _column_exists(conn, table, 'book_id'):
            with op.batch_alter_table(table, recreate='always') as batch_op:
                batch_op.drop_column('book_id')
