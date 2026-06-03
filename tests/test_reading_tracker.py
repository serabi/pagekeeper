"""Tests for Phase 1 reading tracker: models, CRUD, auto-journal on status transitions."""

import logging
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ["DATA_DIR"] = "test_data"
os.environ["BOOKS_DIR"] = "test_data"

logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")

from src.db.models import State
from src.services.reading_stats_service import ReadingStatsService


class TestReadingTrackerModels(unittest.TestCase):
    """Test ReadingJournal, ReadingGoal models and Book reading fields."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.test_db_path = str(Path(self.temp_dir) / "test_reading.db")
        from src.db.database_service import DatabaseService

        self.db = DatabaseService(self.test_db_path)

    def tearDown(self):
        if hasattr(self, "db") and hasattr(self.db, "db_manager"):
            self.db.db_manager.close()
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_book(self, abs_id="test-book-1", title="Test Book", status="active", author=None):
        from src.db.models import Book

        book = Book(abs_id=abs_id, title=title, status=status, author=author)
        return self.db.save_book(book)

    # -- Book reading fields --

    def test_book_reading_fields_default(self):
        """New books have null reading fields and read_count=1."""
        book = self._create_book()
        self.assertIsNone(book.started_at)
        self.assertIsNone(book.finished_at)
        self.assertIsNone(book.rating)
        self.assertEqual(book.read_count, 1)

    def test_book_metadata_override_defaults(self):
        """New books fall back to imported title/author when no overrides exist."""
        book = self._create_book(author="Source Author")
        self.assertIsNone(book.title_override)
        self.assertIsNone(book.author_override)
        self.assertEqual(book.display_title, "Test Book")
        self.assertEqual(book.display_author, "Source Author")

    def test_update_book_metadata_overrides(self):
        """Metadata overrides persist separately from imported metadata."""
        book = self._create_book(author="Source Author")
        updated = self.db.update_book_metadata_overrides(
            book.id,
            title_override="Clean Title",
            author_override="Clean Author",
        )
        self.assertEqual(updated.title_override, "Clean Title")
        self.assertEqual(updated.author_override, "Clean Author")
        self.assertEqual(updated.display_title, "Clean Title")
        self.assertEqual(updated.display_author, "Clean Author")

    def test_clearing_book_metadata_override_falls_back_to_source(self):
        """Clearing one override restores source metadata for that field."""
        book = self._create_book(author="Source Author")
        self.db.update_book_metadata_overrides(
            book.id,
            title_override="Clean Title",
            author_override="Clean Author",
        )

        updated = self.db.update_book_metadata_overrides(book.id, title_override=None)

        self.assertIsNone(updated.title_override)
        self.assertEqual(updated.author_override, "Clean Author")
        self.assertEqual(updated.display_title, "Test Book")
        self.assertEqual(updated.display_author, "Clean Author")

    def test_update_book_metadata_overrides_nonexistent(self):
        """Returns None for a missing book."""
        result = self.db.update_book_metadata_overrides(99999, title_override="Missing")
        self.assertIsNone(result)

    def test_save_book_source_refresh_preserves_metadata_overrides(self):
        """Source metadata refreshes should not clear PageKeeper overrides."""
        from src.db.models import Book

        book = self._create_book(title="Imported Title", author="Imported Author")
        self.db.update_book_metadata_overrides(
            book.id,
            title_override="Override Title",
            author_override="Override Author",
        )

        self.db.save_book(Book(abs_id=book.abs_id, title="Refreshed Title", author="Refreshed Author"))
        refreshed = self.db.get_book_by_abs_id(book.abs_id)

        self.assertEqual(refreshed.title, "Refreshed Title")
        self.assertEqual(refreshed.author, "Refreshed Author")
        self.assertEqual(refreshed.title_override, "Override Title")
        self.assertEqual(refreshed.author_override, "Override Author")
        self.assertEqual(refreshed.display_title, "Override Title")
        self.assertEqual(refreshed.display_author, "Override Author")

    def test_update_book_reading_fields(self):
        """update_book_reading_fields sets only reading fields."""
        book = self._create_book()
        updated = self.db.update_book_reading_fields(book.id, started_at="2026-03-01", rating=4.5)
        self.assertEqual(updated.started_at, "2026-03-01")
        self.assertEqual(updated.rating, 4.5)
        self.assertIsNone(updated.finished_at)

    def test_update_book_reading_fields_rejects_non_reading(self):
        """update_book_reading_fields ignores non-reading kwargs."""
        book = self._create_book()
        updated = self.db.update_book_reading_fields(book.id, started_at="2026-03-01", title="HACKED")
        self.assertEqual(updated.started_at, "2026-03-01")
        # title should not have changed
        refreshed = self.db.get_book_by_abs_id("test-book-1")
        self.assertEqual(refreshed.title, "Test Book")

    def test_update_book_reading_fields_nonexistent(self):
        """Returns None for a missing book."""
        result = self.db.update_book_reading_fields(99999, rating=3.0)
        self.assertIsNone(result)

    def test_save_book_does_not_overwrite_reading_fields(self):
        """save_book should not null out reading fields set separately."""
        book = self._create_book()
        self.db.update_book_reading_fields(book.id, rating=4.0, started_at="2026-01-15")

        # Re-save via save_book (simulating sync path)
        book = self.db.get_book_by_abs_id("test-book-1")
        book.status = "active"
        self.db.save_book(book)

        refreshed = self.db.get_book_by_abs_id("test-book-1")
        self.assertEqual(refreshed.rating, 4.0)
        self.assertEqual(refreshed.started_at, "2026-01-15")

    # -- ReadingJournal CRUD --

    def test_add_and_get_journals(self):
        """Create journal entries and retrieve them newest-first."""
        book = self._create_book()
        self.db.add_reading_journal(book.id, event="started")
        self.db.add_reading_journal(book.id, event="progress", percentage=0.5)
        self.db.add_reading_journal(book.id, event="note", entry="Great chapter!")

        journals = self.db.get_reading_journals(book.id)
        self.assertEqual(len(journals), 3)
        # Newest first
        self.assertEqual(journals[0].event, "note")
        self.assertEqual(journals[0].entry, "Great chapter!")
        self.assertEqual(journals[1].event, "progress")
        self.assertAlmostEqual(journals[1].percentage, 0.5)
        self.assertEqual(journals[2].event, "started")

    def test_delete_journal(self):
        """Delete a specific journal entry by ID."""
        book = self._create_book()
        j = self.db.add_reading_journal(book.id, event="note", entry="delete me")
        self.assertTrue(self.db.delete_reading_journal(j.id))
        self.assertEqual(len(self.db.get_reading_journals(book.id)), 0)

    def test_delete_journal_nonexistent(self):
        """Deleting a missing journal returns False."""
        self.assertFalse(self.db.delete_reading_journal(9999))

    def test_journals_cascade_on_book_delete(self):
        """Deleting a book cascades to its journal entries."""
        book = self._create_book()
        self.db.add_reading_journal(book.id, event="started")
        self.db.add_reading_journal(book.id, event="note", entry="cascade test")

        self.db.delete_book(book.id)
        journals = self.db.get_reading_journals(book.id)
        self.assertEqual(len(journals), 0)

    def test_journals_empty_for_unknown_book(self):
        """get_reading_journals returns empty list for unknown book_id."""
        self.assertEqual(self.db.get_reading_journals(99999), [])

    # -- ReadingGoal CRUD --

    def test_save_and_get_goal(self):
        """Create and retrieve a reading goal."""
        goal = self.db.save_reading_goal(2026, 24)
        self.assertEqual(goal.year, 2026)
        self.assertEqual(goal.target_books, 24)

        retrieved = self.db.get_reading_goal(2026)
        self.assertEqual(retrieved.target_books, 24)

    def test_update_goal(self):
        """Updating an existing goal overwrites target_books."""
        self.db.save_reading_goal(2026, 24)
        updated = self.db.save_reading_goal(2026, 50)
        self.assertEqual(updated.target_books, 50)

        retrieved = self.db.get_reading_goal(2026)
        self.assertEqual(retrieved.target_books, 50)

    def test_get_goal_nonexistent(self):
        """Returns None for a year with no goal."""
        self.assertIsNone(self.db.get_reading_goal(1999))

    # -- Reading stats --

    def test_reading_stats(self):
        """get_reading_stats counts finished books and active books."""
        from src.db.models import Book

        # Create books with various statuses
        b1 = self.db.save_book(Book(abs_id="b1", title="Finished 1", status="completed"))
        self.db.update_book_reading_fields(b1.id, finished_at="2026-06-01", rating=4.5)

        b2 = self.db.save_book(Book(abs_id="b2", title="Finished 2", status="completed"))
        self.db.update_book_reading_fields(b2.id, finished_at="2026-11-15", rating=3.5)

        b3 = self.db.save_book(Book(abs_id="b3", title="Still Reading", status="active"))
        self.db.save_state(State(abs_id="b3", book_id=b3.id, client_name="manual", percentage=0.45))
        self.db.save_book(Book(abs_id="b4", title="Paused", status="paused"))
        b5 = self.db.save_book(Book(abs_id="b5", title="Last Year", status="completed"))
        self.db.update_book_reading_fields(b5.id, finished_at="2025-12-31")
        b6 = self.db.save_book(Book(abs_id="b6", title="DNF but dated", status="dnf"))
        self.db.update_book_reading_fields(b6.id, finished_at="2026-04-21", rating=1.5)

        self.db.save_reading_goal(2026, 12)

        stats = ReadingStatsService(self.db).get_year_stats(2026)
        self.assertEqual(stats["books_finished"], 2)  # b1, b2 (finished in 2026)
        self.assertEqual(stats["currently_reading"], 1)  # b3
        self.assertEqual(stats["total_tracked"], 6)  # b1-b6 (all have reading statuses)
        self.assertEqual(stats["goal_target"], 12)
        self.assertEqual(stats["goal_completed"], 2)
        self.assertEqual(stats["monthly_finished"][5], 1)  # June
        self.assertEqual(stats["monthly_finished"][10], 1)  # November
        self.assertAlmostEqual(stats["average_rating"], 4.0)
        self.assertAlmostEqual(stats["goal_percent"], 16.7)

    def test_reading_stats_no_goal(self):
        """Stats work fine without a goal set."""
        stats = ReadingStatsService(self.db).get_year_stats(2026)
        self.assertEqual(stats["books_finished"], 0)
        self.assertIsNone(stats["goal_target"])
        self.assertEqual(stats["monthly_finished"], [0] * 12)
        self.assertIsNone(stats["average_rating"])

    # -- Journal migration --

    def test_migrate_book_data_includes_journals(self):
        """migrate_book_data moves journal entries to the new book ID."""
        from src.db.models import Book

        old_book = self.db.save_book(Book(abs_id="old-id", title="Old Book", status="active"))
        self.db.add_reading_journal(old_book.id, event="started")
        self.db.add_reading_journal(old_book.id, event="note", entry="migrate me")

        new_book = self.db.save_book(Book(abs_id="new-id", title="New Book", status="active"))
        self.db.migrate_book_data("old-id", "new-id")

        # After migration, old book's journals move to the merged book (now with abs_id='new-id')
        # The old book_id still has the journals since migrate_book_data updates abs_id but keeps book_id
        migrated_book = self.db.get_book_by_abs_id("new-id")
        old_journals = self.db.get_reading_journals(new_book.id)
        new_journals = self.db.get_reading_journals(migrated_book.id)
        self.assertEqual(len(old_journals), 0)
        self.assertEqual(len(new_journals), 2)


if __name__ == "__main__":
    unittest.main()
