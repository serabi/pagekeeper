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
        mark_ebook_as_read = bool(options.get("mark_ebook_as_read"))
        manual_matches = _normalized_manual_matches(options.get("manual_matches"))
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

            manual_match = manual_matches.get(str(book["id"]))
            if manual_match:
                gr_book = self._resolve_manual_match(manual_match)
                matched_by = "manual"
                if not gr_book:
                    entry["bucket"] = "unmatched"
                    entry["manual_match_error"] = "Selected Grimmory match is no longer available"
                    counts["unmatched"] += 1
                    entries.append(entry)
                    continue
            else:
                gr_book, matched_by = self.grimmory.match_book_by_identifiers(
                    isbn=book.get("isbn"),
                    asin=book.get("asin"),
                    title=book.get("title"),
                    author=book.get("author"),
                    prefer_book_type="AUDIOBOOK",
                )

            if not gr_book:
                entry["bucket"] = "unmatched"
                counts["unmatched"] += 1
                entries.append(entry)
                continue

            instance_id = gr_book.get("_instance_id", "default")
            gr_book_id = str(gr_book.get("id"))
            gr_book_type = (gr_book.get("bookType") or "").upper()
            entry.update(
                {
                    "matched_by": matched_by,
                    "manual_match": bool(manual_match),
                    "grimmory_book_id": gr_book_id,
                    "grimmory_instance_id": instance_id,
                    "grimmory_title": gr_book.get("title"),
                    "grimmory_authors": gr_book.get("authors"),
                    "grimmory_file_name": gr_book.get("fileName"),
                    "grimmory_book_type": gr_book_type,
                    "grimmory_book": gr_book,
                }
            )

            # When no per-book audiobook exists in Grimmory the match resolves to
            # the ebook; listening sessions / audiobook bookmarks would then land
            # on an ebook. Flag it in the preview so the skip is visible up front.
            if gr_book_type != "AUDIOBOOK":
                matched_type = gr_book_type or "non-audiobook"
                entry["replay_note"] = (
                    f"sessions/bookmarks skipped because this Grimmory match is {matched_type}, not an audiobook"
                )

            if mark_ebook_as_read:
                self._resolve_ebook_counterpart(entry, book)

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

    def _resolve_manual_match(self, manual_match):
        """Resolve a user-selected Grimmory match by id + instance."""
        book_id = manual_match.get("grimmory_book_id")
        instance_id = manual_match.get("grimmory_instance_id", "default")
        if not book_id:
            return None
        try:
            books = self.grimmory.get_all_books()
        except Exception as e:
            logger.warning(f"ABS->Grimmory: manual match lookup failed for {book_id}: {e}")
            return None
        for book in books or []:
            same_book = str(book.get("id")) == str(book_id)
            same_instance = str(book.get("_instance_id", "default")) == str(instance_id)
            if same_book and same_instance:
                return book
        return None

    def _resolve_ebook_counterpart(self, entry, abs_book):
        """Find the non-audiobook Grimmory record for the matched audiobook.

        Passes the already-matched audiobook record into the dedicated counterpart
        resolver, which excludes audiobook-type records and ranks the rest, so a
        slightly differing ebook title (or a collapsed duplicate filename) still
        surfaces. Stashes the result on the entry for the preview and _migrate_one;
        records None when no counterpart exists so the result can note it without
        failing the book.
        """
        if entry.get("grimmory_book_type") != "AUDIOBOOK":
            entry["grimmory_ebook"] = entry["grimmory_book"]
            entry["grimmory_ebook_id"] = entry["grimmory_book_id"]
            entry["grimmory_ebook_instance_id"] = entry["grimmory_instance_id"]
            entry["grimmory_ebook_title"] = entry.get("grimmory_title")
            entry["grimmory_ebook_source"] = "matched_record"
            entry["ebook_note"] = "ebook target is the matched Grimmory record"
            return

        ebook, _ebook_matched_by = self.grimmory.find_format_counterpart(
            matched_book=entry["grimmory_book"],
            isbn=abs_book.get("isbn"),
            asin=abs_book.get("asin"),
            title=abs_book.get("title"),
            author=abs_book.get("author"),
        )
        if not ebook:
            entry["grimmory_ebook_id"] = None
            entry["ebook_note"] = "no separate ebook record found"
            return
        entry["grimmory_ebook"] = ebook
        entry["grimmory_ebook_id"] = str(ebook.get("id"))
        entry["grimmory_ebook_instance_id"] = ebook.get("_instance_id", "default")
        entry["grimmory_ebook_title"] = ebook.get("title")
        entry["grimmory_ebook_source"] = "counterpart"

    def preview(self, options=None):
        options = options or {}
        if not self.is_configured():
            return {"configured": False, "counts": {}, "books": []}

        entries, counts = self._classify(options)
        books = [_public(e) for e in entries]
        return {"configured": True, "counts": counts, "books": books}

    # ── Migration ──

    def migrate(self, options=None, dry_run=False, selected_abs_ids=None):
        options = options or {}
        if not self.is_configured():
            return {"configured": False, "results": [], "counts": {}}

        carry_sessions = options.get("carry_listening_sessions", True)
        carry_bookmarks = options.get("carry_bookmarks", True)
        mark_ebook_as_read = options.get("mark_ebook_as_read", False)
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

            result = self._migrate_one(entry, carry_sessions, carry_bookmarks, mark_ebook_as_read)
            results.append(result)
            self._throttle()

        outcome_counts = {}
        for r in results:
            outcome_counts[r["outcome"]] = outcome_counts.get(r["outcome"], 0) + 1

        return {"configured": True, "dry_run": dry_run, "counts": counts, "outcome_counts": outcome_counts, "results": results}

    def _migrate_one(self, entry, carry_sessions, carry_bookmarks, mark_ebook_as_read=False):
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

        # Listening sessions and audiobook bookmarks are audiobook-only
        # constructs. When the matched record is not an audiobook (the ABS
        # audiobook has no per-book audiobook in Grimmory, so the match resolved
        # to the ebook), replaying them onto the ebook is meaningless -- skip both
        # rather than recording it as a failure. _classify already set the
        # informational replay_note on the entry for the preview.
        primary_is_audiobook = book_type == "AUDIOBOOK"

        # 3. Listening-session history replay
        if carry_sessions and primary_is_audiobook:
            try:
                sessions_written, sessions_failed = self._replay_sessions(abs_id, gr_book_id, book_type, instance_id)
                if sessions_failed:
                    errors.append("sessions")
            except Exception as e:
                logger.warning(f"ABS->Grimmory: session replay failed for {abs_id}: {e}")
                errors.append("sessions")

        # 4. Bookmarks
        if carry_bookmarks and primary_is_audiobook:
            try:
                bookmarks_written, bookmarks_failed = self._copy_bookmarks(abs_id, gr_book_id, instance_id)
                if bookmarks_failed:
                    errors.append("bookmarks")
            except Exception as e:
                logger.warning(f"ABS->Grimmory: bookmark copy failed for {abs_id}: {e}")
                errors.append("bookmarks")

        # 5. Optionally mark the matching ebook record READ (status + finish date
        #    only -- sessions/bookmarks are audiobook-only constructs).
        if mark_ebook_as_read:
            self._mark_ebook(entry, errors)

        # 6. Update local pagekeeper Book (status + finish date + journal)
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

    def _mark_ebook(self, entry, errors):
        """Mark the matched ebook counterpart READ + finish date.

        No sessions or bookmarks (audiobook-only). On a missing counterpart, note
        it informationally without failing the book. On a write failure append a
        distinct label so the outcome becomes migrated_partial and the recovery
        path can tell which write to retry: "ebook read" when the status write
        fails, "ebook finish date" when the date write fails after the status
        write succeeded.
        """
        ebook_id = entry.get("grimmory_ebook_id")
        if not ebook_id:
            entry["ebook_note"] = "no separate ebook record found"
            return

        ebook = entry.get("grimmory_ebook") or {}
        ebook_instance = entry.get("grimmory_ebook_instance_id", "default")
        ebook_type = (ebook.get("bookType") or "").upper()

        if str(ebook_id) == str(entry.get("grimmory_book_id")) and ebook_instance == entry.get("grimmory_instance_id"):
            entry["ebook_note"] = "ebook target was already marked through the matched record"
            return

        if not self.grimmory.update_read_status_by_id(ebook_id, "READ", instance_id=ebook_instance):
            errors.append("ebook read")
            return

        finished_at = entry.get("finished_at")
        if finished_at and not self.grimmory.set_finished_date(
            ebook_id, ebook_type, finished_at, instance_id=ebook_instance
        ):
            errors.append("ebook finish date")
            return

        entry["ebook_note"] = "ebook marked read"

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


def _normalized_manual_matches(raw_matches):
    """Return sanitized manual ABS -> Grimmory match overrides."""
    if not isinstance(raw_matches, dict):
        return {}
    matches = {}
    for abs_id, match in raw_matches.items():
        if not isinstance(match, dict):
            continue
        book_id = match.get("grimmory_book_id")
        if book_id is None:
            continue
        instance_id = match.get("grimmory_instance_id") or "default"
        matches[str(abs_id)] = {
            "grimmory_book_id": str(book_id),
            "grimmory_instance_id": str(instance_id),
        }
    return matches


def _public(entry):
    """Strip the non-serializable grimmory book objects from an entry."""
    return {k: v for k, v in entry.items() if k not in ("grimmory_book", "grimmory_ebook")}
