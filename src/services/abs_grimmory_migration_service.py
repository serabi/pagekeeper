"""One-time ABS -> Grimmory reading-history migration.

Reads the books a user has FINISHED in Audiobookshelf and replays them into
Grimmory: mark READ, carry the finish date, replay listening-session history, and
copy bookmarks. Mirrors the service/sync-client split used by the Hardcover
integration -- this module owns matching + write orchestration and is invoked only
by blueprint routes (never the periodic sync loop).
"""

import logging
import os
import time
from datetime import UTC, datetime

from src.db.models import AbsGrimmoryMigration

logger = logging.getLogger(__name__)

# isFinished is authoritative; books at/above this progress without the flag are
# surfaced as "not_finished" and only migrated when the user opts in.
NEAR_COMPLETE_THRESHOLD = 0.99


def _ms_to_date(ms):
    """Convert an ABS millisecond epoch to a YYYY-MM-DD (UTC) string."""
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000.0, UTC).strftime("%Y-%m-%d")
    except (ValueError, OSError, OverflowError):
        return None


def _ms_to_iso(ms):
    """Convert an ABS millisecond epoch to an ISO-8601 UTC instant string."""
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000.0, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OSError, OverflowError):
        return None


class AbsGrimmoryMigrationService:
    def __init__(self, database_service, abs_client, grimmory_client_group, status_machine=None, container=None):
        self.database_service = database_service
        self.abs_client = abs_client
        self.grimmory = grimmory_client_group
        self.status_machine = status_machine
        self.container = container

    # ── Configuration ──

    def is_configured(self):
        return bool(self.abs_client.is_configured() and self.grimmory.is_configured())

    def _throttle(self):
        ms = float(os.environ.get("ABS_GRIMMORY_MIGRATION_THROTTLE_MS", 100))
        if ms > 0:
            time.sleep(ms / 1000.0)

    # ── Enumeration + classification ──

    def _classify(self, options):
        """Return (entries, summary_counts). Each entry is a per-book dict with a
        'bucket' and (when matched) the resolved Grimmory book + matched_by."""
        include_near_complete = bool(options.get("include_near_complete"))
        finished = self.abs_client.get_finished_books()

        entries = []
        counts = {
            "will_migrate": 0,
            "already_migrated": 0,
            "already_read": 0,
            "unmatched": 0,
            "not_finished": 0,
        }

        for book in finished:
            entry = {
                "abs_id": book["id"],
                "title": book.get("title"),
                "author": book.get("author"),
                "finished_at": _ms_to_date(book.get("finished_at_ms")),
                "started_at_ms": book.get("started_at_ms"),
                "isbn": book.get("isbn"),
                "asin": book.get("asin"),
            }

            gr_book, matched_by = self.grimmory.match_book_by_identifiers(
                isbn=book.get("isbn"), asin=book.get("asin"), title=book.get("title"), author=book.get("author")
            )

            if not gr_book:
                entry["bucket"] = "unmatched"
                counts["unmatched"] += 1
                entries.append(entry)
                continue

            instance_id = gr_book.get("_instance_id", "default")
            gr_book_id = str(gr_book.get("id"))
            entry.update(
                {
                    "matched_by": matched_by,
                    "grimmory_book_id": gr_book_id,
                    "grimmory_instance_id": instance_id,
                    "grimmory_title": gr_book.get("title"),
                    "grimmory_book": gr_book,
                }
            )

            existing = self.database_service.get_abs_grimmory_migration(
                book["id"], gr_book_id, instance_id
            )
            if existing and existing.outcome in ("migrated", "already_read", "migrated_partial"):
                entry["bucket"] = "already_migrated"
                entry["migrated_outcome"] = existing.outcome
                created = getattr(existing, "created_at", None)
                entry["migrated_at"] = created.strftime("%Y-%m-%dT%H:%M:%SZ") if created else None
                entry["migrated_sessions"] = getattr(existing, "sessions_written", 0) or 0
                entry["migrated_bookmarks"] = getattr(existing, "bookmarks_written", 0) or 0
                counts["already_migrated"] += 1
            elif _grimmory_already_read(gr_book):
                # Already READ in Grimmory (e.g. marked manually) with no audit
                # row. Replaying sessions/bookmarks would duplicate history, so
                # surface it as already_read rather than will_migrate.
                entry["bucket"] = "already_read"
                counts["already_read"] += 1
            else:
                entry["bucket"] = "will_migrate"
                counts["will_migrate"] += 1
            entries.append(entry)

        # near-complete-but-not-finished is informational only (ABS only returns
        # isFinished items via get_finished_books, so this stays empty unless the
        # client is extended; the bucket is reserved for that opt-in path).
        _ = include_near_complete
        return entries, counts

    def preview(self, options=None):
        options = options or {}
        if not self.is_configured():
            return {"configured": False, "counts": {}, "books": []}

        entries, counts = self._classify(options)
        books = [
            {k: v for k, v in e.items() if k != "grimmory_book"}
            for e in entries
        ]
        return {"configured": True, "counts": counts, "books": books}

    # ── Migration ──

    def migrate(self, options=None, dry_run=False, selected_abs_ids=None):
        options = options or {}
        if not self.is_configured():
            return {"configured": False, "results": [], "counts": {}}

        carry_sessions = options.get("carry_listening_sessions", True)
        carry_bookmarks = options.get("carry_bookmarks", True)
        selected = set(selected_abs_ids) if selected_abs_ids is not None else None

        entries, counts = self._classify(options)
        results = []

        for entry in entries:
            if entry["bucket"] != "will_migrate":
                results.append({**_public(entry), "outcome": entry["bucket"]})
                continue

            if selected is not None and entry["abs_id"] not in selected:
                results.append({**_public(entry), "outcome": "skipped_deselected"})
                continue

            if dry_run:
                results.append({**_public(entry), "outcome": "would_migrate"})
                continue

            result = self._migrate_one(entry, carry_sessions, carry_bookmarks)
            results.append(result)
            self._throttle()

        outcome_counts = {}
        for r in results:
            outcome_counts[r["outcome"]] = outcome_counts.get(r["outcome"], 0) + 1

        return {"configured": True, "dry_run": dry_run, "counts": counts, "outcome_counts": outcome_counts, "results": results}

    def _migrate_one(self, entry, carry_sessions, carry_bookmarks):
        abs_id = entry["abs_id"]
        gr_book = entry["grimmory_book"]
        gr_book_id = entry["grimmory_book_id"]
        instance_id = entry["grimmory_instance_id"]
        book_type = (gr_book.get("bookType") or "").upper()
        errors = []
        sessions_written = 0
        bookmarks_written = 0

        # 1. Mark READ (the essential step; its failure fails the book)
        if not self.grimmory.update_read_status_by_id(gr_book_id, "READ", instance_id=instance_id):
            return self._record(entry, "failed", error="Failed to set READ status")

        # 2. Finish date (carry ABS date)
        if entry.get("finished_at"):
            if not self.grimmory.set_finished_date(gr_book_id, book_type, entry["finished_at"], instance_id=instance_id):
                errors.append("finish date")

        # 3. Listening-session history replay
        if carry_sessions:
            try:
                sessions_written, sessions_failed = self._replay_sessions(abs_id, gr_book_id, book_type, instance_id)
                if sessions_failed:
                    errors.append("sessions")
            except Exception as e:
                logger.warning(f"ABS->Grimmory: session replay failed for {abs_id}: {e}")
                errors.append("sessions")

        # 4. Bookmarks
        if carry_bookmarks:
            try:
                bookmarks_written, bookmarks_failed = self._copy_bookmarks(abs_id, gr_book_id, instance_id)
                if bookmarks_failed:
                    errors.append("bookmarks")
            except Exception as e:
                logger.warning(f"ABS->Grimmory: bookmark copy failed for {abs_id}: {e}")
                errors.append("bookmarks")

        # 5. Update local pagekeeper Book (status + finish date + journal)
        try:
            self._update_local_book(abs_id, entry.get("finished_at"))
        except Exception as e:
            logger.warning(f"ABS->Grimmory: local book update failed for {abs_id}: {e}")
            errors.append("local")

        outcome = "migrated" if not errors else "migrated_partial"
        return self._record(
            entry,
            outcome,
            sessions_written=sessions_written,
            bookmarks_written=bookmarks_written,
            error="; ".join(errors) if errors else None,
        )

    def _replay_sessions(self, abs_id, gr_book_id, book_type, instance_id):
        sessions = self.abs_client.get_listening_sessions(item_id=abs_id)
        written = 0
        failed = 0
        for s in sessions:
            time_listening = s.get("timeListening") or 0
            if time_listening <= 0:
                continue
            start_ms = s.get("startedAt")
            start_iso = _ms_to_iso(start_ms)
            if not start_iso:
                continue
            end_iso = _ms_to_iso(start_ms + int(time_listening * 1000)) if start_ms else None
            if not end_iso:
                continue
            ok = self.grimmory.add_reading_session(
                gr_book_id,
                book_type,
                instance_id=instance_id,
                start_time=start_iso,
                end_time=end_iso,
                duration_seconds=int(time_listening),
            )
            if ok:
                written += 1
            else:
                failed += 1
            self._throttle()
        return written, failed

    def _copy_bookmarks(self, abs_id, gr_book_id, instance_id):
        grouped = self.abs_client.get_bookmarks()
        if grouped is None:
            logger.warning(f"ABS->Grimmory: bookmark fetch failed for {abs_id}; treating as failure")
            return 0, 1
        bookmarks = grouped.get(abs_id, [])
        written = 0
        failed = 0
        for bm in bookmarks:
            time_sec = bm.get("time")
            if time_sec is None:
                continue
            ok = self.grimmory.add_bookmark(
                gr_book_id,
                instance_id=instance_id,
                position_ms=int(time_sec * 1000),
                track_index=0,
                title=bm.get("title"),
            )
            if ok:
                written += 1
            else:
                failed += 1
            self._throttle()
        return written, failed

    def _update_local_book(self, abs_id, finished_at):
        book = self.database_service.get_book_by_abs_id(abs_id)
        if not book:
            return
        # The status machine short-circuits when old_status == new_status and
        # never applies `dates`, so for an already-completed book we must write
        # the finish date directly or the ABS date is silently dropped.
        already_completed = book.status == "completed"
        if self.status_machine and not already_completed:
            self.status_machine.transition(
                book,
                "completed",
                source="auto_complete",
                container=self.container,
                dates={"finished_at": finished_at} if finished_at else None,
            )
        elif finished_at:
            self.database_service.update_book_reading_fields(book.id, finished_at=finished_at)

    def _record(self, entry, outcome, *, sessions_written=0, bookmarks_written=0, error=None):
        row = AbsGrimmoryMigration(
            abs_id=entry["abs_id"],
            book_title=entry.get("title"),
            grimmory_book_id=entry.get("grimmory_book_id"),
            grimmory_instance_id=entry.get("grimmory_instance_id", "default"),
            matched_by=entry.get("matched_by"),
            finished_at=entry.get("finished_at"),
            sessions_written=sessions_written,
            bookmarks_written=bookmarks_written,
            outcome=outcome,
            error_message=error,
        )
        persisted = True
        try:
            self.database_service.save_abs_grimmory_migration(row)
        except Exception as e:
            persisted = False
            logger.error(f"ABS->Grimmory: failed to persist audit row for {entry['abs_id']}: {e}")

        # The audit row is the only idempotency guard _classify reads. If it did
        # not persist, a re-run reclassifies this book as will_migrate and
        # replays sessions/bookmarks (POST endpoints that create new rows),
        # duplicating history. Report the unpersisted state instead of a clean
        # success so callers do not treat it as fully migrated.
        if not persisted:
            audit_error = "audit row not persisted"
            error = f"{error}; {audit_error}" if error else audit_error
            outcome = "audit_failed"

        return {
            **_public(entry),
            "outcome": outcome,
            "sessions_written": sessions_written,
            "bookmarks_written": bookmarks_written,
            "error": error,
        }


def _grimmory_already_read(gr_book):
    """True when a matched Grimmory book is already marked READ.

    Grimmory book listings expose the read status under one of a few keys
    depending on version. Only an explicit READ counts; an unknown/absent field
    falls through so a not-read book is never misclassified as already read.
    """
    if not isinstance(gr_book, dict):
        return False
    for key in ("readStatus", "read_status", "status"):
        value = gr_book.get(key)
        if isinstance(value, str) and value.strip().upper() == "READ":
            return True
    return False


def _public(entry):
    """Strip the non-serializable grimmory_book object from an entry."""
    return {k: v for k, v in entry.items() if k != "grimmory_book"}
