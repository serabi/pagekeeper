"""One-off backfill for ABS->Grimmory finish dates that failed to carry over.

Books migrated before the set_finished_date instant-format fix came out as
``migrated_partial`` with a "finish date" error: their read status was set in
Grimmory (which auto-dates the finish to "today"), but the explicit ABS finish
date was rejected by Grimmory's java.time.Instant binding. A normal migration
re-run skips these rows (they are already READ / already audited), so this script
re-applies only the finish date using the now-fixed set_finished_date.

When the matched record is an audiobook, the migration also marks the ebook
counterpart (a separate Grimmory record, e.g. the EPUB edition) READ + finish
date. That ebook write hit the same bug, recorded on the audit row as an
"ebook read" error. The ebook id is not stored on the audit row, so this script
re-resolves the counterpart through the migration service and re-applies the date
to it as well.

Run inside the dev container so it uses the same DB and configured clients as the
app:

    python -m scripts.backfill_grimmory_finish_dates --dry-run
    python -m scripts.backfill_grimmory_finish_dates

It is idempotent: rows already ``migrated`` are skipped, and only
``migrated_partial`` rows whose error mentions the finish date are touched.
"""

import argparse
import logging
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.db.models import AbsGrimmoryMigration

logger = logging.getLogger("backfill_grimmory_finish_dates")


def _needs_backfill(row):
    """A row is a finish-date backfill candidate when it partially migrated with
    a finish-date failure and still carries the original ABS finish date."""
    if row.outcome != "migrated_partial":
        return False
    if not row.finished_at:
        return False
    return bool(row.error_message) and "finish date" in row.error_message


def _had_ebook_failure(row):
    """The migration recorded an ebook counterpart write failure. Older rows use
    "ebook read" for both status and date failures; newer rows distinguish them
    with "ebook finish date". Either warrants re-resolving the counterpart and
    re-applying its finish date."""
    if not row.error_message:
        return False
    return "ebook read" in row.error_message or "ebook finish date" in row.error_message


def backfill_finish_dates(database_service, grimmory, dry_run=False, resolve_ebook=None):
    """Re-apply the ABS finish date to Grimmory for finish-date failures.

    For each candidate row the audiobook (the audited record) gets its finish date
    re-applied. When the row also recorded an ebook failure and ``resolve_ebook``
    is supplied, the ebook counterpart is resolved and its finish date re-applied
    too; the row is only flipped to ``migrated`` when both writes succeed.

    ``resolve_ebook`` is a callable ``row -> (ebook_id, ebook_instance_id)`` (or
    None when no separate ebook record exists). It is injected so the selection /
    update logic stays testable without ABS or Grimmory matching internals.

    Returns a summary dict with backfilled / failed / skipped counts. On success
    the finish-date errors are removed from the audit row; the row only flips to
    ``migrated`` when no unrelated errors remain. On failure the row is left
    untouched.
    """
    rows = database_service.get_all_abs_grimmory_migrations()
    summary = {"backfilled": 0, "failed": 0, "skipped": 0}

    for row in rows:
        if not _needs_backfill(row):
            summary["skipped"] += 1
            continue

        needs_ebook = _had_ebook_failure(row)
        label = f"book {row.grimmory_book_id} ({row.book_title}) -> {row.finished_at}"

        if dry_run:
            extra = " (+ ebook counterpart)" if needs_ebook else ""
            logger.info(f"[dry-run] would backfill finish date for {label}{extra}")
            summary["backfilled"] += 1
            continue

        # book_type is not stored on the audit row and is unused by
        # set_finished_date's payload (bookId + dateFinished only).
        ok = grimmory.set_finished_date(
            row.grimmory_book_id,
            "",
            row.finished_at,
            instance_id=row.grimmory_instance_id,
        )
        if not ok:
            logger.warning(f"Backfill failed for {label}; leaving row as-is")
            summary["failed"] += 1
            continue

        if needs_ebook:
            if not _backfill_ebook(row, grimmory, resolve_ebook, label):
                summary["failed"] += 1
                continue

        residual_error = _residual_error_message(row)
        updated = AbsGrimmoryMigration(
            abs_id=row.abs_id,
            book_title=row.book_title,
            grimmory_book_id=row.grimmory_book_id,
            grimmory_instance_id=row.grimmory_instance_id,
            matched_by=row.matched_by,
            finished_at=row.finished_at,
            sessions_written=row.sessions_written,
            bookmarks_written=row.bookmarks_written,
            outcome="migrated_partial" if residual_error else "migrated",
            error_message=residual_error,
        )
        database_service.save_abs_grimmory_migration(updated)
        logger.info(f"Backfilled finish date for {label}")
        summary["backfilled"] += 1

    return summary


def _residual_error_message(row):
    """Return non-finish-date errors that should survive a successful backfill."""
    cleared = {"finish date", "ebook finish date", "ebook read"}
    parts = [p.strip() for p in (row.error_message or "").split(";") if p.strip()]
    residual = [p for p in parts if p not in cleared]
    return "; ".join(residual) if residual else None


def _backfill_ebook(row, grimmory, resolve_ebook, label):
    """Re-apply the finish date to the ebook counterpart. Returns True when the
    ebook is handled (written, or legitimately absent) so the row can be flipped
    to migrated; False when the write fails and the row should be left as-is."""
    if resolve_ebook is None:
        logger.warning(f"Cannot resolve ebook counterpart for {label}; leaving row as-is")
        return False

    resolved = resolve_ebook(row)
    if not resolved:
        # No separate ebook record exists; the audiobook write is the whole fix.
        logger.info(f"No ebook counterpart for {label}; audiobook finish date is sufficient")
        return True

    ebook_id, ebook_instance_id = resolved
    if str(ebook_id) == str(row.grimmory_book_id) and ebook_instance_id == row.grimmory_instance_id:
        # The ebook target is the already-handled audiobook record.
        return True

    ok = grimmory.set_finished_date(ebook_id, "", row.finished_at, instance_id=ebook_instance_id)
    if not ok:
        logger.warning(f"Ebook backfill failed for {label} (ebook {ebook_id}); leaving row as-is")
        return False
    logger.info(f"Backfilled ebook finish date for {label} (ebook {ebook_id})")
    return True


def _build_ebook_resolver(container):
    """Build a resolver that re-derives the ebook counterpart for an audit row via
    the migration service's own resolution logic (ABS book + format counterpart)."""
    abs_client = container.abs_client()
    grimmory = container.grimmory_client_group()
    database_service = container.database_service()

    from src.services.abs_grimmory_migration_service import AbsGrimmoryMigrationService

    service = AbsGrimmoryMigrationService(database_service, abs_client, grimmory)
    finished_by_id = {str(b["id"]): b for b in abs_client.get_finished_books()}
    books_by_key = {}
    for b in grimmory.get_all_books():
        key = (str(b.get("id")), str(b.get("_instance_id", "default")))
        books_by_key[key] = b

    def resolve(row):
        abs_book = finished_by_id.get(str(row.abs_id))
        matched_book = books_by_key.get((str(row.grimmory_book_id), str(row.grimmory_instance_id)))
        if not abs_book or not matched_book:
            logger.warning(
                f"Could not re-resolve ebook for abs {row.abs_id} / grimmory {row.grimmory_book_id}"
            )
            return None
        entry = {
            "grimmory_book": matched_book,
            "grimmory_book_id": str(row.grimmory_book_id),
            "grimmory_instance_id": row.grimmory_instance_id,
            "grimmory_book_type": (matched_book.get("bookType") or "").upper(),
            "grimmory_title": matched_book.get("title"),
        }
        service._resolve_ebook_counterpart(entry, abs_book)
        ebook_id = entry.get("grimmory_ebook_id")
        if not ebook_id:
            return None
        return ebook_id, entry.get("grimmory_ebook_instance_id", "default")

    return resolve


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the rows that would be backfilled (and target dates) without writing.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    from src.utils.config_loader import ConfigLoader
    from src.utils.di_container import create_container

    container = create_container()
    database_service = container.database_service()
    # Credentials are persisted in the DB and loaded into the environment at app
    # startup; replicate that so the clients are actually configured.
    ConfigLoader.load_settings(database_service)
    grimmory = container.grimmory_client_group()

    if not grimmory.is_configured():
        logger.error("Grimmory is not configured; aborting backfill")
        sys.exit(1)

    resolve_ebook = None if args.dry_run else _build_ebook_resolver(container)

    summary = backfill_finish_dates(
        database_service, grimmory, dry_run=args.dry_run, resolve_ebook=resolve_ebook
    )

    mode = "[dry-run] " if args.dry_run else ""
    logger.info(
        f"{mode}Backfill complete: "
        f"{summary['backfilled']} backfilled, "
        f"{summary['failed']} still failing, "
        f"{summary['skipped']} skipped"
    )


if __name__ == "__main__":
    main()
