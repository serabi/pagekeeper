"""Integration tests for TBR repository — real SQLite in /tmp."""

import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ['DATA_DIR'] = 'test_data'
os.environ['BOOKS_DIR'] = 'test_data'


class TestTbrRepository(unittest.TestCase):
    """Tests TbrRepository via DatabaseService against a real temp SQLite DB."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.test_db_path = str(Path(self.temp_dir) / 'test_tbr.db')
        from src.db.database_service import DatabaseService
        self.db = DatabaseService(self.test_db_path)

    def tearDown(self):
        if hasattr(self, 'db') and hasattr(self.db, 'db_manager'):
            self.db.db_manager.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -- CRUD basics --

    def test_add_and_get_item(self):
        """Add a TBR item and retrieve it by ID."""
        item, created = self.db.add_tbr_item('Dune', author='Frank Herbert')
        self.assertTrue(created)
        self.assertIsNotNone(item.id)
        self.assertEqual(item.title, 'Dune')
        self.assertEqual(item.author, 'Frank Herbert')

        retrieved = self.db.get_tbr_item(item.id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.title, 'Dune')

    def test_get_all_items(self):
        """get_tbr_items returns all items."""
        self.db.add_tbr_item('Book A')
        self.db.add_tbr_item('Book B')
        self.db.add_tbr_item('Book C')

        items = self.db.get_tbr_items()
        self.assertEqual(len(items), 3)

    def test_delete_item(self):
        """delete_tbr_item removes the item and returns True."""
        item, _ = self.db.add_tbr_item('Temp Book')
        self.assertTrue(self.db.delete_tbr_item(item.id))
        self.assertIsNone(self.db.get_tbr_item(item.id))

    def test_delete_nonexistent(self):
        """delete_tbr_item returns False for missing ID."""
        self.assertFalse(self.db.delete_tbr_item(9999))

    def test_count(self):
        """get_tbr_count returns the total number of items."""
        self.assertEqual(self.db.get_tbr_count(), 0)
        self.db.add_tbr_item('Book A')
        self.db.add_tbr_item('Book B')
        self.assertEqual(self.db.get_tbr_count(), 2)

    def test_get_item_nonexistent(self):
        """get_tbr_item returns None for missing ID."""
        self.assertIsNone(self.db.get_tbr_item(9999))

    # -- Atomic dedup --

    def test_dedup_by_hardcover_book_id(self):
        """Same hardcover_book_id returns existing item with created=False."""
        item1, created1 = self.db.add_tbr_item('Dune', hardcover_book_id=42)
        item2, created2 = self.db.add_tbr_item('Dune (dup)', hardcover_book_id=42)

        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(item1.id, item2.id)
        self.assertEqual(item2.title, 'Dune')  # original title preserved

    def test_dedup_by_ol_work_key(self):
        """Same ol_work_key returns existing item with created=False."""
        item1, created1 = self.db.add_tbr_item('Neuromancer', ol_work_key='/works/OL123')
        item2, created2 = self.db.add_tbr_item('Neuromancer dup', ol_work_key='/works/OL123')

        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(item1.id, item2.id)

    def test_different_keys_both_create(self):
        """Different dedup keys create separate items."""
        item1, created1 = self.db.add_tbr_item('Book A', hardcover_book_id=1)
        item2, created2 = self.db.add_tbr_item('Book B', hardcover_book_id=2)

        self.assertTrue(created1)
        self.assertTrue(created2)
        self.assertNotEqual(item1.id, item2.id)

    def test_no_dedup_without_keys(self):
        """Manual items without dedup keys always create new entries."""
        item1, created1 = self.db.add_tbr_item('My Book')
        item2, created2 = self.db.add_tbr_item('My Book')

        self.assertTrue(created1)
        self.assertTrue(created2)
        self.assertNotEqual(item1.id, item2.id)

    # -- Linking --

    def test_link_tbr_to_book(self):
        """link_tbr_to_book sets book_abs_id on the item."""
        from src.db.models import Book
        self.db.save_book(Book(abs_id='abs-123', abs_title='Owned Copy', status='active'))

        item, _ = self.db.add_tbr_item('Dune')
        self.assertIsNone(item.book_abs_id)

        linked = self.db.link_tbr_to_book(item.id, 'abs-123')
        self.assertIsNotNone(linked)
        self.assertEqual(linked.book_abs_id, 'abs-123')

        # Verify persisted
        refreshed = self.db.get_tbr_item(item.id)
        self.assertEqual(refreshed.book_abs_id, 'abs-123')

    def test_link_tbr_not_found(self):
        """link_tbr_to_book returns None for missing item ID."""
        result = self.db.link_tbr_to_book(9999, 'abs-123')
        self.assertIsNone(result)

    # -- Lookup --

    def test_find_tbr_by_hardcover_id(self):
        """find_tbr_by_hardcover_id returns the matching item."""
        self.db.add_tbr_item('Dune', hardcover_book_id=42)
        found = self.db.find_tbr_by_hardcover_id(42)
        self.assertIsNotNone(found)
        self.assertEqual(found.title, 'Dune')

    def test_find_tbr_by_hardcover_id_not_found(self):
        """find_tbr_by_hardcover_id returns None for unknown ID."""
        result = self.db.find_tbr_by_hardcover_id(9999)
        self.assertIsNone(result)

    # -- Ordering --

    def test_items_ordered_newest_first(self):
        """get_tbr_items returns items newest-first by added_at."""
        import time
        self.db.add_tbr_item('Oldest')
        time.sleep(0.05)
        self.db.add_tbr_item('Middle')
        time.sleep(0.05)
        self.db.add_tbr_item('Newest')

        items = self.db.get_tbr_items()
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0].title, 'Newest')
        self.assertEqual(items[1].title, 'Middle')
        self.assertEqual(items[2].title, 'Oldest')

    # -- Auto-link via save_hardcover_details --

    def test_save_hardcover_details_auto_links_tbr(self):
        """save_hardcover_details auto-links a TBR item with matching hardcover_book_id."""
        from src.db.models import Book, HardcoverDetails

        # Create a book in the library
        self.db.save_book(Book(abs_id='abs-1', abs_title='Dune', status='active'))

        # Create a TBR item with hardcover_book_id=42 (not yet linked)
        item, created = self.db.add_tbr_item('Dune', hardcover_book_id=42)
        self.assertTrue(created)
        self.assertIsNone(item.book_abs_id)

        # Save HardcoverDetails linking abs-1 to hardcover_book_id=42
        hc = HardcoverDetails(abs_id='abs-1', hardcover_book_id='42')
        self.db.save_hardcover_details(hc)

        # Verify TBR item is now linked
        refreshed = self.db.get_tbr_item(item.id)
        self.assertEqual(refreshed.book_abs_id, 'abs-1')

    def test_save_hardcover_details_no_tbr_match(self):
        """save_hardcover_details with no matching TBR item is a no-op."""
        from src.db.models import Book, HardcoverDetails

        self.db.save_book(Book(abs_id='abs-1', abs_title='Dune', status='active'))
        hc = HardcoverDetails(abs_id='abs-1', hardcover_book_id='99')
        # Should not raise — no TBR item with hardcover_book_id=99
        self.db.save_hardcover_details(hc)

    def test_save_hardcover_details_skips_already_linked(self):
        """save_hardcover_details does not overwrite an already-linked TBR item."""
        from src.db.models import Book, HardcoverDetails

        self.db.save_book(Book(abs_id='abs-1', abs_title='Dune', status='active'))
        self.db.save_book(Book(abs_id='abs-2', abs_title='Dune Messiah', status='active'))

        item, _ = self.db.add_tbr_item('Dune', hardcover_book_id=42)
        self.db.link_tbr_to_book(item.id, 'abs-2')  # pre-linked to abs-2

        hc = HardcoverDetails(abs_id='abs-1', hardcover_book_id='42')
        self.db.save_hardcover_details(hc)

        # Should still be linked to abs-2, not overwritten to abs-1
        refreshed = self.db.get_tbr_item(item.id)
        self.assertEqual(refreshed.book_abs_id, 'abs-2')

    # -- Source filtering --

    def test_filter_by_source(self):
        """get_tbr_items with source filter returns only matching items."""
        self.db.add_tbr_item('Manual Book', source='manual')
        self.db.add_tbr_item('HC Book', source='hardcover_wtr')
        self.db.add_tbr_item('OL Book', source='open_library')

        manual_items = self.db.get_tbr_items(source='manual')
        self.assertEqual(len(manual_items), 1)
        self.assertEqual(manual_items[0].title, 'Manual Book')

        hc_items = self.db.get_tbr_items(source='hardcover_wtr')
        self.assertEqual(len(hc_items), 1)


if __name__ == '__main__':
    unittest.main()
