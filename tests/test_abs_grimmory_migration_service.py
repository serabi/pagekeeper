import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.services.abs_grimmory_migration_service import (
    AbsGrimmoryMigrationService,
    _ms_to_date,
    _ms_to_iso,
)


@pytest.fixture(autouse=True)
def _no_throttle():
    os.environ["ABS_GRIMMORY_MIGRATION_THROTTLE_MS"] = "0"
    yield
    os.environ.pop("ABS_GRIMMORY_MIGRATION_THROTTLE_MS", None)


def _service(*, finished=None, grimmory_match=None, existing_migration=None):
    db = MagicMock()
    db.get_abs_grimmory_migration.return_value = existing_migration
    db.get_book_by_abs_id.return_value = None

    abs_client = MagicMock()
    abs_client.is_configured.return_value = True
    abs_client.get_finished_books.return_value = finished or []
    abs_client.get_listening_sessions.return_value = []
    abs_client.get_bookmarks.return_value = {}

    grimmory = MagicMock()
    grimmory.is_configured.return_value = True
    # match returns (book, matched_by); default no match
    grimmory.match_book_by_identifiers.return_value = grimmory_match or (None, None)
    grimmory.update_read_status_by_id.return_value = True
    grimmory.set_finished_date.return_value = True
    grimmory.add_reading_session.return_value = True
    grimmory.add_bookmark.return_value = True

    svc = AbsGrimmoryMigrationService(db, abs_client, grimmory)
    return svc, db, abs_client, grimmory


def test_ms_conversions():
    assert _ms_to_date(1700000000000) == "2023-11-14"
    assert _ms_to_iso(1700000000000) == "2023-11-14T22:13:20Z"
    assert _ms_to_date(None) is None
    assert _ms_to_iso(0) is None


def test_preview_unmatched_bucket():
    svc, *_ = _service(
        finished=[{"id": "a", "title": "X", "author": "Y", "isbn": None, "asin": None}],
        grimmory_match=(None, None),
    )
    result = svc.preview()
    assert result["configured"] is True
    assert result["counts"]["unmatched"] == 1
    assert result["books"][0]["bucket"] == "unmatched"


def test_preview_will_migrate_bucket():
    svc, *_ = _service(
        finished=[{"id": "a", "title": "X", "isbn": "111", "finished_at_ms": 1700000000000}],
        grimmory_match=({"id": 5, "title": "X", "bookType": "EPUB"}, "isbn"),
    )
    result = svc.preview()
    assert result["counts"]["will_migrate"] == 1
    book = result["books"][0]
    assert book["bucket"] == "will_migrate"
    assert book["grimmory_book_id"] == "5"
    assert book["matched_by"] == "isbn"
    assert book["finished_at"] == "2023-11-14"
    assert "grimmory_book" not in book  # non-serializable object stripped


def test_idempotency_already_migrated():
    existing = MagicMock()
    existing.outcome = "migrated"
    svc, *_ = _service(
        finished=[{"id": "a", "title": "X", "isbn": "111"}],
        grimmory_match=({"id": 5, "title": "X"}, "isbn"),
        existing_migration=existing,
    )
    result = svc.preview()
    assert result["counts"]["already_migrated"] == 1


def test_idempotency_partial_treated_as_already_migrated():
    existing = MagicMock()
    existing.outcome = "migrated_partial"
    svc, *_ = _service(
        finished=[{"id": "a", "title": "X", "isbn": "111"}],
        grimmory_match=({"id": 5, "title": "X"}, "isbn"),
        existing_migration=existing,
    )
    result = svc.preview()
    assert result["counts"]["already_migrated"] == 1
    assert result["books"][0]["bucket"] == "already_migrated"


def test_dry_run_performs_no_writes():
    svc, db, abs_client, grimmory = _service(
        finished=[{"id": "a", "title": "X", "isbn": "111", "finished_at_ms": 1700000000000}],
        grimmory_match=({"id": 5, "title": "X", "bookType": "EPUB"}, "isbn"),
    )
    result = svc.migrate(dry_run=True)
    grimmory.update_read_status_by_id.assert_not_called()
    grimmory.set_finished_date.assert_not_called()
    db.save_abs_grimmory_migration.assert_not_called()
    assert result["results"][0]["outcome"] == "would_migrate"


def test_migrate_marks_read_and_sets_date():
    svc, db, abs_client, grimmory = _service(
        finished=[{"id": "a", "title": "X", "isbn": "111", "finished_at_ms": 1700000000000}],
        grimmory_match=({"id": 5, "title": "X", "bookType": "EPUB"}, "isbn"),
    )
    result = svc.migrate()
    grimmory.update_read_status_by_id.assert_called_once_with("5", "READ", instance_id="default")
    grimmory.set_finished_date.assert_called_once()
    assert result["results"][0]["outcome"] == "migrated"
    db.save_abs_grimmory_migration.assert_called_once()


def test_read_status_failure_marks_failed():
    svc, db, abs_client, grimmory = _service(
        finished=[{"id": "a", "title": "X", "isbn": "111"}],
        grimmory_match=({"id": 5, "title": "X"}, "isbn"),
    )
    grimmory.update_read_status_by_id.return_value = False
    result = svc.migrate()
    assert result["results"][0]["outcome"] == "failed"
    # finish date never attempted after READ failed
    grimmory.set_finished_date.assert_not_called()


def test_bookmark_failure_does_not_block_read_status():
    svc, db, abs_client, grimmory = _service(
        finished=[{"id": "a", "title": "X", "isbn": "111", "finished_at_ms": 1700000000000}],
        grimmory_match=({"id": 5, "title": "X", "bookType": "AUDIOBOOK"}, "isbn"),
    )
    abs_client.get_bookmarks.return_value = {"a": [{"time": 12.5, "title": "Ch1"}]}
    grimmory.add_bookmark.return_value = False
    result = svc.migrate()
    # READ status still succeeded; outcome is partial, not failed
    grimmory.update_read_status_by_id.assert_called_once()
    assert result["results"][0]["outcome"] == "migrated_partial"


def test_bookmark_fetch_failure_records_partial():
    svc, db, abs_client, grimmory = _service(
        finished=[{"id": "a", "title": "X", "isbn": "111", "finished_at_ms": 1700000000000}],
        grimmory_match=({"id": 5, "title": "X", "bookType": "AUDIOBOOK"}, "isbn"),
    )
    abs_client.get_bookmarks.return_value = None
    result = svc.migrate()
    # READ status still succeeded; the failed bookmark fetch surfaces as partial
    grimmory.update_read_status_by_id.assert_called_once()
    grimmory.add_bookmark.assert_not_called()
    outcome = result["results"][0]
    assert outcome["outcome"] == "migrated_partial"
    assert "bookmarks" in (outcome["error"] or "")


def test_session_replay_one_call_per_session():
    svc, db, abs_client, grimmory = _service(
        finished=[{"id": "a", "title": "X", "isbn": "111", "finished_at_ms": 1700000000000}],
        grimmory_match=({"id": 5, "title": "X", "bookType": "AUDIOBOOK"}, "isbn"),
    )
    abs_client.get_listening_sessions.return_value = [
        {"libraryItemId": "a", "timeListening": 1800, "startedAt": 1699000000000},
        {"libraryItemId": "a", "timeListening": 600, "startedAt": 1699100000000},
        {"libraryItemId": "a", "timeListening": 0, "startedAt": 1699200000000},  # skipped (no time)
    ]
    result = svc.migrate()
    assert grimmory.add_reading_session.call_count == 2
    assert result["results"][0]["sessions_written"] == 2


def test_bookmark_time_to_position_ms():
    svc, db, abs_client, grimmory = _service(
        finished=[{"id": "a", "title": "X", "isbn": "111"}],
        grimmory_match=({"id": 5, "title": "X", "bookType": "AUDIOBOOK"}, "isbn"),
    )
    abs_client.get_bookmarks.return_value = {"a": [{"time": 12.5, "title": "Ch1"}]}
    svc.migrate()
    _, kwargs = grimmory.add_bookmark.call_args
    assert kwargs["position_ms"] == 12500
    assert kwargs["title"] == "Ch1"


def test_non_audiobook_primary_skips_session_and_bookmark_replay():
    # ABS audiobook matched an ebook (no per-book audiobook exists in Grimmory).
    # Sessions/bookmarks are audiobook-only, so they must not replay onto it; the
    # book still marks READ + finish date and migrates cleanly.
    svc, db, abs_client, grimmory = _service(
        finished=[{"id": "a", "title": "X", "isbn": "111", "finished_at_ms": 1700000000000}],
        grimmory_match=({"id": 5, "title": "X", "bookType": "EPUB"}, "title"),
    )
    abs_client.get_listening_sessions.return_value = [
        {"libraryItemId": "a", "timeListening": 1800, "startedAt": 1699000000000},
    ]
    abs_client.get_bookmarks.return_value = {"a": [{"time": 12.5, "title": "Ch1"}]}

    result = svc.migrate()

    grimmory.add_reading_session.assert_not_called()
    grimmory.add_bookmark.assert_not_called()
    outcome = result["results"][0]
    assert outcome["outcome"] == "migrated"
    assert outcome["sessions_written"] == 0
    assert outcome["bookmarks_written"] == 0
    assert outcome["replay_note"] == "sessions/bookmarks skipped (matched record is not an audiobook)"
    grimmory.update_read_status_by_id.assert_called_once_with("5", "READ", instance_id="default")
    grimmory.set_finished_date.assert_called_once()


def test_preview_flags_non_audiobook_primary_replay_note():
    svc, db, abs_client, grimmory = _service(
        finished=[{"id": "a", "title": "X", "isbn": "111", "finished_at_ms": 1700000000000}],
        grimmory_match=({"id": 5, "title": "X", "bookType": "EPUB"}, "title"),
    )

    preview = svc.preview()

    book = preview["books"][0]
    assert book["grimmory_book_type"] == "EPUB"
    assert book["replay_note"] == "sessions/bookmarks skipped (matched record is not an audiobook)"


def test_audiobook_primary_has_no_replay_note():
    svc, db, abs_client, grimmory = _service(
        finished=[{"id": "a", "title": "X", "isbn": "111", "finished_at_ms": 1700000000000}],
        grimmory_match=({"id": 5, "title": "X", "bookType": "AUDIOBOOK"}, "title"),
    )

    preview = svc.preview()

    book = preview["books"][0]
    assert book["grimmory_book_type"] == "AUDIOBOOK"
    assert "replay_note" not in book


def test_local_book_updated_via_status_machine():
    svc, db, abs_client, grimmory = _service(
        finished=[{"id": "a", "title": "X", "isbn": "111", "finished_at_ms": 1700000000000}],
        grimmory_match=({"id": 5, "title": "X", "bookType": "EPUB"}, "isbn"),
    )
    local_book = MagicMock()
    db.get_book_by_abs_id.return_value = local_book
    sm = MagicMock()
    svc.status_machine = sm

    svc.migrate()
    sm.transition.assert_called_once()
    args, kwargs = sm.transition.call_args
    assert args[1] == "completed"
    assert kwargs["dates"] == {"finished_at": "2023-11-14"}


def test_already_completed_book_writes_finish_date_directly():
    svc, db, abs_client, grimmory = _service(
        finished=[{"id": "a", "title": "X", "isbn": "111", "finished_at_ms": 1700000000000}],
        grimmory_match=({"id": 5, "title": "X", "bookType": "EPUB"}, "isbn"),
    )
    local_book = MagicMock()
    local_book.status = "completed"
    db.get_book_by_abs_id.return_value = local_book
    sm = MagicMock()
    svc.status_machine = sm

    svc.migrate()

    # status machine would short-circuit and drop the date, so bypass it
    sm.transition.assert_not_called()
    db.update_book_reading_fields.assert_called_once_with(local_book.id, finished_at="2023-11-14")


def test_already_read_in_grimmory_skips_migration():
    svc, db, abs_client, grimmory = _service(
        finished=[{"id": "a", "title": "X", "isbn": "111", "finished_at_ms": 1700000000000}],
        grimmory_match=({"id": 5, "title": "X", "bookType": "EPUB", "readStatus": "READ"}, "isbn"),
    )
    result = svc.migrate()

    assert result["counts"]["already_read"] == 1
    assert result["counts"]["will_migrate"] == 0
    assert result["results"][0]["outcome"] == "already_read"
    grimmory.update_read_status_by_id.assert_not_called()
    grimmory.add_reading_session.assert_not_called()
    grimmory.add_bookmark.assert_not_called()


def test_unread_grimmory_status_still_migrates():
    svc, db, abs_client, grimmory = _service(
        finished=[{"id": "a", "title": "X", "isbn": "111", "finished_at_ms": 1700000000000}],
        grimmory_match=({"id": 5, "title": "X", "bookType": "EPUB", "readStatus": "UNREAD"}, "isbn"),
    )
    result = svc.migrate()

    assert result["counts"]["will_migrate"] == 1
    grimmory.update_read_status_by_id.assert_called_once()


def test_audit_persist_failure_reports_audit_failed_outcome():
    svc, db, abs_client, grimmory = _service(
        finished=[{"id": "a", "title": "X", "isbn": "111", "finished_at_ms": 1700000000000}],
        grimmory_match=({"id": 5, "title": "X", "bookType": "EPUB"}, "isbn"),
    )
    db.save_abs_grimmory_migration.side_effect = RuntimeError("db down")

    result = svc.migrate()

    outcome = result["results"][0]
    assert outcome["outcome"] == "audit_failed"
    assert "audit row not persisted" in (outcome["error"] or "")


def test_multi_instance_dispatch():
    svc, db, abs_client, grimmory = _service(
        finished=[{"id": "a", "title": "X", "isbn": "111", "finished_at_ms": 1700000000000}],
        grimmory_match=({"id": 5, "title": "X", "bookType": "EPUB", "_instance_id": "GRIMMORY_2"}, "isbn"),
    )
    svc.migrate()
    grimmory.update_read_status_by_id.assert_called_once_with("5", "READ", instance_id="GRIMMORY_2")


def test_not_configured_returns_empty():
    svc, db, abs_client, grimmory = _service()
    grimmory.is_configured.return_value = False
    assert svc.preview()["configured"] is False
    assert svc.migrate()["configured"] is False


def test_selected_abs_ids_skips_deselected():
    finished = [
        {"id": "abs-1", "title": "Keep", "author": "A", "finished_at_ms": 1700000000000},
        {"id": "abs-2", "title": "Drop", "author": "B", "finished_at_ms": 1700000000000},
    ]

    def match(isbn=None, asin=None, title=None, author=None, prefer_book_type=None, exclude_book_id=None):
        if title == "Keep":
            return ({"id": 1, "title": "Keep", "bookType": "EPUB"}, "title")
        return ({"id": 2, "title": "Drop", "bookType": "EPUB"}, "title")

    svc, db, _abs, grimmory = _service(finished=finished)
    grimmory.match_book_by_identifiers.side_effect = match

    result = svc.migrate(selected_abs_ids=["abs-1"])

    outcomes = {r["abs_id"]: r["outcome"] for r in result["results"]}
    assert outcomes["abs-2"] == "skipped_deselected"
    assert outcomes["abs-1"] in ("migrated", "already_read")
    # the deselected book is never written to Grimmory
    written_ids = [c.args[0] for c in grimmory.update_read_status_by_id.call_args_list]
    assert 2 not in written_ids


def test_selected_abs_ids_none_migrates_all():
    finished = [{"id": "abs-1", "title": "K", "author": "A", "finished_at_ms": 1700000000000}]
    svc, db, _abs, grimmory = _service(
        finished=finished, grimmory_match=({"id": 1, "title": "K", "bookType": "EPUB"}, "title")
    )

    result = svc.migrate(selected_abs_ids=None)

    outcomes = [r["outcome"] for r in result["results"]]
    assert "skipped_deselected" not in outcomes


def test_already_migrated_carries_audit_history():
    from datetime import UTC, datetime

    existing = MagicMock()
    existing.outcome = "migrated"
    existing.created_at = datetime(2026, 6, 20, 14, 30, 0, tzinfo=UTC)
    existing.matched_by = "isbn"
    existing.sessions_written = 3
    existing.bookmarks_written = 1

    finished = [{"id": "abs-1", "title": "Done", "author": "A", "finished_at_ms": 1700000000000}]
    svc, db, _abs, grimmory = _service(
        finished=finished,
        grimmory_match=({"id": 1, "title": "Done", "bookType": "EPUB"}, "isbn"),
        existing_migration=existing,
    )

    data = svc.preview()

    book = next(b for b in data["books"] if b["abs_id"] == "abs-1")
    assert book["bucket"] == "already_migrated"
    assert book["migrated_outcome"] == "migrated"
    assert book["migrated_at"].startswith("2026-06-20")
    assert book["migrated_sessions"] == 3
    assert book["migrated_bookmarks"] == 1


def test_service_requests_audiobook_preference():
    svc, db, abs_client, grimmory = _service(
        finished=[{"id": "a", "title": "X", "isbn": "111", "finished_at_ms": 1700000000000}],
        grimmory_match=({"id": 5, "title": "X", "bookType": "AUDIOBOOK"}, "isbn"),
    )

    svc.preview()

    _, kwargs = grimmory.match_book_by_identifiers.call_args
    assert kwargs["prefer_book_type"] == "AUDIOBOOK"


def _wire_counterpart(grimmory, audiobook, all_books):
    """Set up a realistic two-step lookup on the mocked Grimmory client.

    ``match_book_by_identifiers`` returns the audiobook match, while
    ``find_format_counterpart`` delegates to the *real* GrimmoryClient resolver
    over ``all_books`` (a ``get_all_books``-shaped list). This exercises the
    actual candidate-collapse / book-type-exclusion behavior instead of fully
    mocking the matcher, so a real-data regression is caught.
    """
    from src.api.grimmory_client import GrimmoryClient

    grimmory.match_book_by_identifiers.return_value = audiobook

    real = GrimmoryClient.__new__(GrimmoryClient)
    real.get_all_books = MagicMock(return_value=all_books)

    def counterpart(*, matched_book, isbn=None, asin=None, title=None, author=None, exclude_book_type="AUDIOBOOK"):
        book, matched_by = real.find_format_counterpart(
            matched_book=matched_book,
            isbn=isbn,
            asin=asin,
            title=title,
            author=author,
            exclude_book_type=exclude_book_type,
        )
        if book is None:
            return None, None
        return {**book, "_instance_id": book.get("_instance_id", "default")}, matched_by

    grimmory.find_format_counterpart.side_effect = counterpart


def test_mark_ebook_on_with_counterpart_marks_read_and_date():
    svc, db, abs_client, grimmory = _service(
        finished=[{"id": "a", "title": "X", "isbn": "111", "finished_at_ms": 1700000000000}],
    )
    # Audiobook title "01 X" differs from the ebook "X", and a collapsed
    # duplicate filename leaves a second ebook row sharing the title -- the real
    # cascade that previously broke. Author is null, as in live ABS data.
    _wire_counterpart(
        grimmory,
        ({"id": 5, "title": "01 X", "bookType": "AUDIOBOOK"}, "title"),
        [
            {"id": 5, "title": "01 X", "authors": "", "bookType": "AUDIOBOOK"},
            {"id": 9, "title": "X", "authors": "", "bookType": "EPUB"},
        ],
    )

    result = svc.migrate(options={"mark_ebook_as_read": True})

    read_ids = [c.args[0] for c in grimmory.update_read_status_by_id.call_args_list]
    assert "5" in read_ids and "9" in read_ids
    date_ids = [c.args[0] for c in grimmory.set_finished_date.call_args_list]
    assert "9" in date_ids
    # No sessions or bookmarks target the ebook id.
    assert all(c.args[0] != "9" for c in grimmory.add_reading_session.call_args_list)
    assert all(c.args[0] != "9" for c in grimmory.add_bookmark.call_args_list)
    assert result["results"][0]["outcome"] == "migrated"


def test_mark_ebook_on_no_counterpart_notes_and_succeeds():
    svc, db, abs_client, grimmory = _service(
        finished=[{"id": "a", "title": "X", "isbn": "111", "finished_at_ms": 1700000000000}],
    )
    # Only the audiobook record exists; no non-audiobook counterpart.
    _wire_counterpart(
        grimmory,
        ({"id": 5, "title": "X", "bookType": "AUDIOBOOK"}, "title"),
        [{"id": 5, "title": "X", "authors": "", "bookType": "AUDIOBOOK"}],
    )

    result = svc.migrate(options={"mark_ebook_as_read": True})

    outcome = result["results"][0]
    assert outcome["outcome"] == "migrated"
    assert outcome["ebook_note"] == "no ebook record found"
    # Only the audiobook id was marked read.
    read_ids = [c.args[0] for c in grimmory.update_read_status_by_id.call_args_list]
    assert read_ids == ["5"]


def test_mark_ebook_off_does_no_ebook_lookup():
    svc, db, abs_client, grimmory = _service(
        finished=[{"id": "a", "title": "X", "isbn": "111", "finished_at_ms": 1700000000000}],
        grimmory_match=({"id": 5, "title": "X", "bookType": "AUDIOBOOK"}, "isbn"),
    )

    svc.migrate()

    # Without the toggle, the matcher is called once per book (audiobook only),
    # never with exclude_book_id.
    assert all(
        c.kwargs.get("exclude_book_id") is None
        for c in grimmory.match_book_by_identifiers.call_args_list
    )
    read_ids = [c.args[0] for c in grimmory.update_read_status_by_id.call_args_list]
    assert read_ids == ["5"]


def test_mark_ebook_on_mark_failure_is_partial():
    svc, db, abs_client, grimmory = _service(
        finished=[{"id": "a", "title": "X", "isbn": "111", "finished_at_ms": 1700000000000}],
    )
    _wire_counterpart(
        grimmory,
        ({"id": 5, "title": "X", "bookType": "AUDIOBOOK"}, "title"),
        [
            {"id": 5, "title": "X", "authors": "", "bookType": "AUDIOBOOK"},
            {"id": 9, "title": "X", "authors": "", "bookType": "EPUB"},
        ],
    )

    def read_status(book_id, status, instance_id="default"):
        return book_id != "9"

    grimmory.update_read_status_by_id.side_effect = read_status

    result = svc.migrate(options={"mark_ebook_as_read": True})

    outcome = result["results"][0]
    assert outcome["outcome"] == "migrated_partial"
    assert "ebook read" in (outcome["error"] or "")
