#!/usr/bin/env python3
"""
Unit tests for HardcoverSyncClient to verify auto-matching and progress sync functionality.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.database_service import DatabaseService
from src.db.models import Book, HardcoverDetails
from src.sync_clients.hardcover_sync_client import (
    HC_CURRENTLY_READING,
    HC_READ,
    HC_WANT_TO_READ,
    HardcoverSyncClient,
)
from src.sync_clients.sync_client_interface import LocatorResult, UpdateProgressRequest


class TestHardcoverSyncClient(unittest.TestCase):
    """Test suite for HardcoverSyncClient auto-matching and progress sync."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.test_db_path = str(Path(self.temp_dir) / 'test_hardcover.db')

        self.database_service = DatabaseService(self.test_db_path)

        self.mock_hardcover_client = Mock()
        self.mock_abs_client = Mock()
        self.mock_ebook_parser = Mock()

        self.mock_hardcover_client.is_configured.return_value = True

        self.hardcover_sync_client = HardcoverSyncClient(
            hardcover_client=self.mock_hardcover_client,
            ebook_parser=self.mock_ebook_parser,
            abs_client=self.mock_abs_client,
            database_service=self.database_service
        )

        self.test_book = Book(
            abs_id='test-hardcover-book',
            abs_title='Test Hardcover Book',
            ebook_filename='test-hardcover.epub',
            status='active',
            duration=7200.0
        )
        self.database_service.save_book(self.test_book)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_basic_interface_compliance(self):
        self.assertTrue(self.hardcover_sync_client.is_configured())
        self.assertFalse(self.hardcover_sync_client.can_be_leader())
        # get_service_state returns None when no hardcover_details exist
        self.assertIsNone(self.hardcover_sync_client.get_service_state(self.test_book, None))

    @patch('src.sync_clients.hardcover_sync_client.record_write')
    def test_automatch_successful_isbn_search(self, mock_record_write):
        mock_abs_item = {
            'media': {
                'metadata': {
                    'title': 'Test ISBN Book',
                    'authorName': 'Test Author',
                    'isbn': '9781234567890'
                }
            }
        }
        self.mock_abs_client.get_item_details.return_value = mock_abs_item

        self.mock_hardcover_client.search_by_isbn.return_value = {
            'book_id': 12345,
            'edition_id': 67890,
            'pages': 300,
            'title': 'Test ISBN Book'
        }
        # No existing user_book on Hardcover — will create one
        self.mock_hardcover_client.get_user_book.return_value = None
        # update_status returns user_book result for caching
        self.mock_hardcover_client.update_status.return_value = {'id': 999, 'status_id': HC_CURRENTLY_READING}

        update_request = UpdateProgressRequest(
            locator_result=LocatorResult(percentage=0.5)
        )

        result = self.hardcover_sync_client.update_progress(self.test_book, update_request)

        self.mock_abs_client.get_item_details.assert_called_once_with('test-hardcover-book')
        self.mock_hardcover_client.search_by_isbn.assert_called_once_with('9781234567890')

        # Book has status='active' → should map to HC_CURRENTLY_READING (2)
        self.mock_hardcover_client.update_status.assert_any_call(12345, HC_CURRENTLY_READING, 67890)

        saved_details = self.database_service.get_hardcover_details('test-hardcover-book')
        self.assertIsNotNone(saved_details)
        self.assertEqual(saved_details.hardcover_book_id, '12345')
        self.assertEqual(saved_details.isbn, '9781234567890')
        self.assertEqual(saved_details.matched_by, 'isbn')

        self.assertTrue(result.success)

    @patch('src.sync_clients.hardcover_sync_client.record_write')
    def test_update_progress_calls_hardcover_api(self, mock_record_write):
        hardcover_details = HardcoverDetails(
            abs_id='test-hardcover-book',
            hardcover_book_id='123',
            hardcover_edition_id='456',
            hardcover_pages=200,
            matched_by='pre-existing',
            hardcover_user_book_id=789,
            hardcover_status_id=1,
        )
        self.database_service.save_hardcover_details(hardcover_details)

        update_request = UpdateProgressRequest(
            locator_result=LocatorResult(percentage=0.25)
        )

        result = self.hardcover_sync_client.update_progress(self.test_book, update_request)

        # Status should be promoted: Want to Read (1) → Currently Reading (2)
        self.mock_hardcover_client.update_status.assert_called_with(123, 2, 456)

        expected_page = int(200 * 0.25)
        self.mock_hardcover_client.update_progress.assert_called_with(
            789,
            expected_page,
            edition_id='456',
            is_finished=False,
            current_percentage=0.25,
            started_at=None,
            finished_at=None,
        )

        self.assertTrue(result.success)

    @patch('src.sync_clients.hardcover_sync_client.record_write')
    def test_finished_book_status_promotion(self, mock_record_write):
        hardcover_details = HardcoverDetails(
            abs_id='test-hardcover-book',
            hardcover_book_id='123',
            hardcover_edition_id='456',
            hardcover_pages=100,
            matched_by='test',
            hardcover_user_book_id=789,
            hardcover_status_id=2,
        )
        self.database_service.save_hardcover_details(hardcover_details)

        update_request = UpdateProgressRequest(
            locator_result=LocatorResult(percentage=0.995)
        )

        self.hardcover_sync_client.update_progress(self.test_book, update_request)

        # Status should be promoted to Read (3)
        self.mock_hardcover_client.update_status.assert_called_with(123, 3, 456)

        # When is_finished=True, page_num is clamped to total_pages to prevent
        # feedback loops from mismatched cached state
        self.mock_hardcover_client.update_progress.assert_called_with(
            789,
            100,
            edition_id='456',
            is_finished=True,
            current_percentage=0.995,
            started_at=None,
            finished_at=None,
        )

    @patch('src.sync_clients.hardcover_sync_client.record_write')
    def test_automatch_skip_when_already_matched(self, mock_record_write):
        existing_details = HardcoverDetails(
            abs_id='test-hardcover-book',
            hardcover_book_id='100',
            hardcover_edition_id='200',
            hardcover_pages=200,
            matched_by='manual',
            hardcover_user_book_id=789,
            hardcover_status_id=1,
        )
        self.database_service.save_hardcover_details(existing_details)

        update_request = UpdateProgressRequest(
            locator_result=LocatorResult(percentage=0.4)
        )

        self.hardcover_sync_client.update_progress(self.test_book, update_request)

        # ABS should NOT be called since book is already matched
        self.mock_abs_client.get_item_details.assert_not_called()
        self.mock_hardcover_client.search_by_isbn.assert_not_called()

    @patch('src.sync_clients.hardcover_sync_client.record_write')
    def test_zero_pages_edge_case(self, mock_record_write):
        hardcover_details = HardcoverDetails(
            abs_id='test-hardcover-book',
            hardcover_book_id='300',
            hardcover_edition_id='400',
            hardcover_pages=0,
            matched_by='test',
            hardcover_user_book_id=789,
            hardcover_status_id=1,
        )
        self.database_service.save_hardcover_details(hardcover_details)

        update_request = UpdateProgressRequest(
            locator_result=LocatorResult(percentage=0.5)
        )

        # Mock get_all_editions to return empty (refresh fails)
        self.mock_hardcover_client.get_all_editions.return_value = {}

        result = self.hardcover_sync_client.update_progress(self.test_book, update_request)

        self.assertFalse(result.success)
        self.assertIsNone(result.location)
        self.mock_hardcover_client.update_progress.assert_not_called()

    def test_no_configuration_returns_failure(self):
        self.mock_hardcover_client.is_configured.return_value = False

        update_request = UpdateProgressRequest(
            locator_result=LocatorResult(percentage=0.5)
        )

        result = self.hardcover_sync_client.update_progress(self.test_book, update_request)
        self.assertFalse(result.success)

    @patch('src.sync_clients.hardcover_sync_client.record_write')
    def test_api_error_handling(self, mock_record_write):
        hardcover_details = HardcoverDetails(
            abs_id='test-hardcover-book',
            hardcover_book_id='error-123',
            hardcover_edition_id='error-456',
            hardcover_pages=150,
            matched_by='test',
            hardcover_user_book_id=789,
            hardcover_status_id=2,
        )
        self.database_service.save_hardcover_details(hardcover_details)

        self.mock_hardcover_client.update_progress.side_effect = Exception("Hardcover API Error")

        update_request = UpdateProgressRequest(
            locator_result=LocatorResult(percentage=0.6)
        )

        result = self.hardcover_sync_client.update_progress(self.test_book, update_request)
        self.assertFalse(result.success)

    def test_get_text_from_current_state_returns_none(self):
        text = self.hardcover_sync_client.get_text_from_current_state(self.test_book, None)
        self.assertIsNone(text)

    @patch('src.sync_clients.hardcover_sync_client.record_write')
    def test_push_local_status(self, mock_record_write):
        """Test push_local_status pushes to Hardcover and caches status."""
        hardcover_details = HardcoverDetails(
            abs_id='test-hardcover-book',
            hardcover_book_id='123',
            hardcover_edition_id='456',
            hardcover_pages=200,
            matched_by='test',
        )
        self.database_service.save_hardcover_details(hardcover_details)

        self.hardcover_sync_client.push_local_status(self.test_book, 'paused')

        self.mock_hardcover_client.update_status.assert_called_once_with(123, 4, 456)
        mock_record_write.assert_called()

        # Verify cached status was updated
        updated_details = self.database_service.get_hardcover_details('test-hardcover-book')
        self.assertEqual(updated_details.hardcover_status_id, 4)

    @patch('src.sync_clients.hardcover_sync_client.record_write')
    def test_dnf_status_not_auto_resumed(self, mock_record_write):
        """Test that DNF books are not auto-resumed by progress updates."""
        hardcover_details = HardcoverDetails(
            abs_id='test-hardcover-book',
            hardcover_book_id='123',
            hardcover_edition_id='456',
            hardcover_pages=200,
            matched_by='test',
            hardcover_user_book_id=789,
            hardcover_status_id=5,  # DNF
        )
        self.database_service.save_hardcover_details(hardcover_details)

        update_request = UpdateProgressRequest(
            locator_result=LocatorResult(percentage=0.5)
        )

        self.hardcover_sync_client.update_progress(self.test_book, update_request)

        # Status should NOT change from DNF — update_status should not be called
        self.mock_hardcover_client.update_status.assert_not_called()

    @patch('src.sync_clients.hardcover_sync_client.is_own_write', return_value=False)
    @patch('src.sync_clients.hardcover_sync_client.record_write')
    def test_get_service_state_with_bulk_context(self, mock_record_write, mock_is_own):
        """Test that get_service_state uses bulk context for efficient lookups."""
        hardcover_details = HardcoverDetails(
            abs_id='test-hardcover-book',
            hardcover_book_id='123',
            hardcover_edition_id='456',
            hardcover_pages=200,
            matched_by='test',
        )
        self.database_service.save_hardcover_details(hardcover_details)

        bulk_context = {
            123: {
                'id': 789,
                'status_id': 2,
                'book_id': 123,
                'edition_id': 456,
                'user_book_reads': [{
                    'id': 101,
                    'started_at': '2026-01-15',
                    'finished_at': None,
                    'progress_pages': 100,
                    'progress_seconds': None,
                }],
            }
        }

        state = self.hardcover_sync_client.get_service_state(
            self.test_book, None, bulk_context=bulk_context
        )

        self.assertIsNotNone(state)
        self.assertAlmostEqual(state.current['pct'], 0.5)  # 100/200 pages

        # Verify IDs were cached
        updated = self.database_service.get_hardcover_details('test-hardcover-book')
        self.assertEqual(updated.hardcover_user_book_id, 789)
        self.assertEqual(updated.hardcover_user_book_read_id, 101)

    def test_fetch_bulk_state(self):
        """Test that fetch_bulk_state calls get_currently_reading."""
        self.mock_hardcover_client.get_currently_reading.return_value = {123: {'id': 789}}

        result = self.hardcover_sync_client.fetch_bulk_state()

        self.assertEqual(result, {123: {'id': 789}})
        self.mock_hardcover_client.get_currently_reading.assert_called_once()

    @patch('src.sync_clients.hardcover_sync_client.record_write')
    def test_push_local_rating_with_cached_user_book(self, mock_record_write):
        hardcover_details = HardcoverDetails(
            abs_id='test-hardcover-book',
            hardcover_book_id='123',
            hardcover_edition_id='456',
            matched_by='test',
            hardcover_user_book_id=789,
            hardcover_status_id=2,
        )
        self.database_service.save_hardcover_details(hardcover_details)
        self.mock_hardcover_client.update_user_book.return_value = {'id': 789, 'rating': 4.5}

        result = self.hardcover_sync_client.push_local_rating(self.test_book, 4.5)

        self.assertTrue(result['hardcover_synced'])
        self.mock_hardcover_client.update_user_book.assert_called_once_with(789, {'rating': 4.5})
        mock_record_write.assert_called()

    @patch('src.sync_clients.hardcover_sync_client.record_write')
    def test_push_local_rating_creates_user_book_when_missing(self, mock_record_write):
        hardcover_details = HardcoverDetails(
            abs_id='test-hardcover-book',
            hardcover_book_id='123',
            hardcover_edition_id='456',
            matched_by='test',
        )
        self.database_service.save_hardcover_details(hardcover_details)
        self.mock_hardcover_client.get_user_book.return_value = None
        self.mock_hardcover_client.update_status.return_value = {'id': 999, 'status_id': 2}
        self.mock_hardcover_client.update_user_book.return_value = {'id': 999, 'rating': 3.5}

        result = self.hardcover_sync_client.push_local_rating(self.test_book, 3.5)

        self.assertTrue(result['hardcover_synced'])
        self.mock_hardcover_client.update_status.assert_called_once_with(123, 2, 456)
        self.mock_hardcover_client.update_user_book.assert_called_once_with(999, {'rating': 3.5})
        saved = self.database_service.get_hardcover_details('test-hardcover-book')
        self.assertEqual(saved.hardcover_user_book_id, 999)
        mock_record_write.assert_called()

    @patch('src.sync_clients.hardcover_sync_client.record_write')
    def test_completed_book_forwards_historical_dates(self, mock_record_write):
        """Test that a completed book's started_at/finished_at are forwarded to the API."""
        hardcover_details = HardcoverDetails(
            abs_id='test-hardcover-book',
            hardcover_book_id='123',
            hardcover_edition_id='456',
            hardcover_pages=200,
            matched_by='test',
            hardcover_user_book_id=789,
            hardcover_status_id=3,
        )
        self.database_service.save_hardcover_details(hardcover_details)

        self.test_book.started_at = '2024-06-01'
        self.test_book.finished_at = '2024-07-15'

        update_request = UpdateProgressRequest(
            locator_result=LocatorResult(percentage=1.0)
        )

        self.hardcover_sync_client.update_progress(self.test_book, update_request)

        self.mock_hardcover_client.update_progress.assert_called_with(
            789,
            200,
            edition_id='456',
            is_finished=True,
            current_percentage=1.0,
            started_at='2024-06-01',
            finished_at='2024-07-15',
        )

    @patch('src.sync_clients.hardcover_sync_client.record_write')
    def test_automatch_preserves_existing_hardcover_status(self, mock_record_write):
        """If a user_book already exists on Hardcover, adopt it without overwriting status."""
        self.mock_abs_client.get_item_details.return_value = {
            'media': {'metadata': {'title': 'Already Read', 'authorName': 'Author', 'isbn': '111'}}
        }
        self.mock_hardcover_client.search_by_isbn.return_value = {
            'book_id': 42, 'edition_id': 100, 'pages': 250, 'title': 'Already Read',
        }
        # Existing user_book on Hardcover with status = Read (3)
        self.mock_hardcover_client.get_user_book.return_value = {'id': 555, 'status_id': HC_READ}

        self.hardcover_sync_client.automatch_hardcover(self.test_book)

        # update_status should NOT be called — we preserve the existing status
        self.mock_hardcover_client.update_status.assert_not_called()
        mock_record_write.assert_not_called()

        saved = self.database_service.get_hardcover_details('test-hardcover-book')
        self.assertEqual(saved.hardcover_user_book_id, 555)
        self.assertEqual(saved.hardcover_status_id, HC_READ)

    @patch('src.sync_clients.hardcover_sync_client.record_write')
    def test_automatch_uses_local_status_for_new_user_book(self, mock_record_write):
        """When no user_book exists on Hardcover, map local book.status to HC status."""
        self.test_book.status = 'completed'
        self.database_service.save_book(self.test_book)

        self.mock_abs_client.get_item_details.return_value = {
            'media': {'metadata': {'title': 'Done Book', 'authorName': 'A', 'isbn': '222'}}
        }
        self.mock_hardcover_client.search_by_isbn.return_value = {
            'book_id': 50, 'edition_id': 200, 'pages': 300, 'title': 'Done Book',
        }
        self.mock_hardcover_client.get_user_book.return_value = None
        self.mock_hardcover_client.update_status.return_value = {'id': 600, 'status_id': HC_READ}

        self.hardcover_sync_client.automatch_hardcover(self.test_book)

        # 'completed' maps to HC_READ (3)
        self.mock_hardcover_client.update_status.assert_called_once_with(50, HC_READ, 200)
        mock_record_write.assert_called_once_with('Hardcover', 'test-hardcover-book', {'status': HC_READ})

    @patch('src.sync_clients.hardcover_sync_client.record_write')
    def test_manual_match_preserves_existing_hardcover_status(self, mock_record_write):
        """Manual match should adopt existing Hardcover status without overwriting."""
        self.mock_hardcover_client.resolve_book_from_input.return_value = {
            'book_id': 77, 'edition_id': 88, 'pages': 400, 'title': 'Manual Book',
        }
        self.mock_abs_client.get_item_details.return_value = {
            'media': {'metadata': {'isbn': '333', 'asin': None}}
        }
        # Existing user_book with status = Read (3)
        self.mock_hardcover_client.get_user_book.return_value = {'id': 700, 'status_id': HC_READ}

        result = self.hardcover_sync_client.set_manual_match('test-hardcover-book', 'some-slug')

        self.assertTrue(result)
        self.mock_hardcover_client.update_status.assert_not_called()
        mock_record_write.assert_not_called()

        saved = self.database_service.get_hardcover_details('test-hardcover-book')
        self.assertEqual(saved.hardcover_user_book_id, 700)
        self.assertEqual(saved.hardcover_status_id, HC_READ)

    def test_push_local_rating_returns_local_only_for_unlinked_book(self):
        result = self.hardcover_sync_client.push_local_rating(self.test_book, 4.0)
        self.assertFalse(result['hardcover_synced'])
        self.assertIn('not linked', result['hardcover_error'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
