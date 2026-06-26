import os
import sys
from unittest.mock import MagicMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.backfill_grimmory_finish_dates import backfill_finish_dates


def _row(**kwargs):
    row = MagicMock()
    row.abs_id = kwargs.get("abs_id", "abs-1")
    row.book_title = kwargs.get("book_title", "Some Book")
    row.grimmory_book_id = kwargs.get("grimmory_book_id", "5345")
    row.grimmory_instance_id = kwargs.get("grimmory_instance_id", "default")
    row.matched_by = kwargs.get("matched_by", "asin")
    row.finished_at = kwargs.get("finished_at", "2023-11-14")
    row.sessions_written = kwargs.get("sessions_written", 3)
    row.bookmarks_written = kwargs.get("bookmarks_written", 1)
    row.outcome = kwargs.get("outcome", "migrated_partial")
    row.error_message = kwargs.get("error_message", "finish date")
    return row


def test_backfills_partial_finish_date_row():
    db = MagicMock()
    db.get_all_abs_grimmory_migrations.return_value = [_row()]
    grimmory = MagicMock()
    grimmory.set_finished_date.return_value = True

    summary = backfill_finish_dates(db, grimmory, dry_run=False)

    grimmory.set_finished_date.assert_called_once()
    args, kwargs = grimmory.set_finished_date.call_args
    assert args[0] == "5345"
    assert "2023-11-14" in args
    assert kwargs.get("instance_id") == "default"

    db.save_abs_grimmory_migration.assert_called_once()
    saved = db.save_abs_grimmory_migration.call_args[0][0]
    assert saved.outcome == "migrated"
    assert saved.error_message is None
    assert saved.finished_at == "2023-11-14"
    # Existing counts are preserved through the upsert
    assert saved.sessions_written == 3
    assert saved.bookmarks_written == 1

    assert summary["backfilled"] == 1
    assert summary["failed"] == 0
    assert summary["skipped"] == 0


def test_dry_run_does_not_write():
    db = MagicMock()
    db.get_all_abs_grimmory_migrations.return_value = [_row()]
    grimmory = MagicMock()

    summary = backfill_finish_dates(db, grimmory, dry_run=True)

    grimmory.set_finished_date.assert_not_called()
    db.save_abs_grimmory_migration.assert_not_called()
    assert summary["backfilled"] == 1


def test_skips_already_migrated_row():
    db = MagicMock()
    db.get_all_abs_grimmory_migrations.return_value = [_row(outcome="migrated", error_message=None)]
    grimmory = MagicMock()

    summary = backfill_finish_dates(db, grimmory, dry_run=False)

    grimmory.set_finished_date.assert_not_called()
    db.save_abs_grimmory_migration.assert_not_called()
    assert summary["skipped"] == 1
    assert summary["backfilled"] == 0


def test_skips_partial_row_without_finish_date_error():
    db = MagicMock()
    db.get_all_abs_grimmory_migrations.return_value = [
        _row(error_message="sessions; bookmarks")
    ]
    grimmory = MagicMock()

    summary = backfill_finish_dates(db, grimmory, dry_run=False)

    grimmory.set_finished_date.assert_not_called()
    assert summary["skipped"] == 1
    assert summary["backfilled"] == 0


def test_skips_row_without_finished_at():
    db = MagicMock()
    db.get_all_abs_grimmory_migrations.return_value = [_row(finished_at=None)]
    grimmory = MagicMock()

    summary = backfill_finish_dates(db, grimmory, dry_run=False)

    grimmory.set_finished_date.assert_not_called()
    assert summary["skipped"] == 1


def test_failed_write_leaves_row_untouched():
    db = MagicMock()
    db.get_all_abs_grimmory_migrations.return_value = [_row()]
    grimmory = MagicMock()
    grimmory.set_finished_date.return_value = False

    summary = backfill_finish_dates(db, grimmory, dry_run=False)

    db.save_abs_grimmory_migration.assert_not_called()
    assert summary["failed"] == 1
    assert summary["backfilled"] == 0


def test_backfill_preserves_unrelated_residual_errors():
    db = MagicMock()
    db.get_all_abs_grimmory_migrations.return_value = [_row(error_message="finish date; local")]
    grimmory = MagicMock()
    grimmory.set_finished_date.return_value = True

    summary = backfill_finish_dates(db, grimmory, dry_run=False)

    saved = db.save_abs_grimmory_migration.call_args[0][0]
    assert saved.outcome == "migrated_partial"
    assert saved.error_message == "local"
    assert summary["backfilled"] == 1
    assert summary["failed"] == 0


def test_backfills_ebook_counterpart_when_recorded():
    db = MagicMock()
    db.get_all_abs_grimmory_migrations.return_value = [_row(error_message="finish date; ebook read")]
    grimmory = MagicMock()
    grimmory.set_finished_date.return_value = True
    resolve_ebook = MagicMock(return_value=("3989", "default"))

    summary = backfill_finish_dates(db, grimmory, dry_run=False, resolve_ebook=resolve_ebook)

    resolve_ebook.assert_called_once()
    # Both the audiobook (5345) and the ebook counterpart (3989) get the date.
    targets = {call.args[0] for call in grimmory.set_finished_date.call_args_list}
    assert targets == {"5345", "3989"}
    saved = db.save_abs_grimmory_migration.call_args[0][0]
    assert saved.outcome == "migrated"
    assert summary["backfilled"] == 1
    assert summary["failed"] == 0


def test_backfills_ebook_counterpart_for_finish_date_label():
    # Newer rows distinguish an ebook date failure as "ebook finish date"; it must
    # still trigger counterpart resolution and a re-applied ebook date.
    db = MagicMock()
    db.get_all_abs_grimmory_migrations.return_value = [_row(error_message="ebook finish date")]
    grimmory = MagicMock()
    grimmory.set_finished_date.return_value = True
    resolve_ebook = MagicMock(return_value=("3989", "default"))

    summary = backfill_finish_dates(db, grimmory, dry_run=False, resolve_ebook=resolve_ebook)

    resolve_ebook.assert_called_once()
    targets = {call.args[0] for call in grimmory.set_finished_date.call_args_list}
    assert targets == {"5345", "3989"}
    assert summary["backfilled"] == 1
    assert summary["failed"] == 0


def test_ebook_write_failure_leaves_row_untouched():
    db = MagicMock()
    db.get_all_abs_grimmory_migrations.return_value = [_row(error_message="finish date; ebook read")]
    grimmory = MagicMock()
    # Audiobook write succeeds, ebook write fails.
    grimmory.set_finished_date.side_effect = lambda book_id, *a, **k: book_id == "5345"
    resolve_ebook = MagicMock(return_value=("3989", "default"))

    summary = backfill_finish_dates(db, grimmory, dry_run=False, resolve_ebook=resolve_ebook)

    db.save_abs_grimmory_migration.assert_not_called()
    assert summary["failed"] == 1
    assert summary["backfilled"] == 0


def test_ebook_failure_with_no_counterpart_still_migrates():
    db = MagicMock()
    db.get_all_abs_grimmory_migrations.return_value = [_row(error_message="finish date; ebook read")]
    grimmory = MagicMock()
    grimmory.set_finished_date.return_value = True
    resolve_ebook = MagicMock(return_value=None)

    summary = backfill_finish_dates(db, grimmory, dry_run=False, resolve_ebook=resolve_ebook)

    grimmory.set_finished_date.assert_called_once()  # audiobook only
    saved = db.save_abs_grimmory_migration.call_args[0][0]
    assert saved.outcome == "migrated"
    assert summary["backfilled"] == 1


def test_ebook_failure_without_resolver_leaves_row_untouched():
    db = MagicMock()
    db.get_all_abs_grimmory_migrations.return_value = [_row(error_message="finish date; ebook read")]
    grimmory = MagicMock()
    grimmory.set_finished_date.return_value = True

    summary = backfill_finish_dates(db, grimmory, dry_run=False, resolve_ebook=None)

    db.save_abs_grimmory_migration.assert_not_called()
    assert summary["failed"] == 1


def test_ebook_target_same_as_audiobook_is_not_rewritten():
    db = MagicMock()
    db.get_all_abs_grimmory_migrations.return_value = [_row(error_message="finish date; ebook read")]
    grimmory = MagicMock()
    grimmory.set_finished_date.return_value = True
    resolve_ebook = MagicMock(return_value=("5345", "default"))

    summary = backfill_finish_dates(db, grimmory, dry_run=False, resolve_ebook=resolve_ebook)

    grimmory.set_finished_date.assert_called_once()  # audiobook only; ebook is same record
    saved = db.save_abs_grimmory_migration.call_args[0][0]
    assert saved.outcome == "migrated"
    assert summary["backfilled"] == 1
