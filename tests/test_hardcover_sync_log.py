#!/usr/bin/env python3
"""Tests for Hardcover sync log feature: model, repository, API, instrumentation."""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.database_service import DatabaseService
from src.db.models import Book, HardcoverDetails, HardcoverSyncLog
from src.services.hardcover_log_service import log_hardcover_action
from src.sync_clients.hardcover_sync_client import HardcoverSyncClient


class TestHardcoverSyncLogModel(unittest.TestCase):
    """Test HardcoverSyncLog model persistence."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db = DatabaseService(str(Path(self.temp_dir) / 'test.db'))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_create_log_entry(self):
        entry = HardcoverSyncLog(
            abs_id=None, book_title='Test Book',
            direction='push', action='status_update',
            detail='{"status": 2}', success=True,
        )
        saved = self.db.add_hardcover_sync_log(entry)
        self.assertIsNotNone(saved.id)
        self.assertEqual(saved.direction, 'push')
        self.assertEqual(saved.action, 'status_update')
        self.assertTrue(saved.success)

    def test_create_log_with_book_fk(self):
        book = Book(abs_id='hc-log-test', abs_title='FK Book', status='active')
        self.db.save_book(book)
        entry = HardcoverSyncLog(
            abs_id='hc-log-test', book_title='FK Book',
            direction='pull', action='status_pull',
        )
        saved = self.db.add_hardcover_sync_log(entry)
        self.assertEqual(saved.abs_id, 'hc-log-test')

    def test_create_failed_entry(self):
        entry = HardcoverSyncLog(
            direction='push', action='rating',
            success=False, error_message='API timeout',
        )
        saved = self.db.add_hardcover_sync_log(entry)
        self.assertFalse(saved.success)
        self.assertEqual(saved.error_message, 'API timeout')


class TestHardcoverSyncLogRepository(unittest.TestCase):
    """Test repository query, filter, and prune methods."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db = DatabaseService(str(Path(self.temp_dir) / 'test.db'))
        for i in range(5):
            self.db.add_hardcover_sync_log(HardcoverSyncLog(
                book_title=f'Book {i}', direction='push', action='automatch',
            ))
        for i in range(3):
            self.db.add_hardcover_sync_log(HardcoverSyncLog(
                book_title=f'Pull Book {i}', direction='pull', action='status_pull',
            ))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_get_all(self):
        items, total = self.db.get_hardcover_sync_logs()
        self.assertEqual(total, 8)
        self.assertEqual(len(items), 8)

    def test_filter_by_direction(self):
        items, total = self.db.get_hardcover_sync_logs(direction='pull')
        self.assertEqual(total, 3)
        for item in items:
            self.assertEqual(item.direction, 'pull')

    def test_filter_by_action(self):
        items, total = self.db.get_hardcover_sync_logs(action='automatch')
        self.assertEqual(total, 5)

    def test_filter_by_search(self):
        items, total = self.db.get_hardcover_sync_logs(search='Pull Book')
        self.assertEqual(total, 3)

    def test_pagination(self):
        items, total = self.db.get_hardcover_sync_logs(page=1, per_page=3)
        self.assertEqual(len(items), 3)
        self.assertEqual(total, 8)

        items2, _ = self.db.get_hardcover_sync_logs(page=2, per_page=3)
        self.assertEqual(len(items2), 3)

    def test_prune_all(self):
        cutoff = datetime.utcnow() + timedelta(hours=1)
        deleted = self.db.prune_hardcover_sync_logs(cutoff)
        self.assertEqual(deleted, 8)

        items, total = self.db.get_hardcover_sync_logs()
        self.assertEqual(total, 0)

    def test_prune_leaves_recent(self):
        cutoff = datetime.utcnow() - timedelta(hours=1)
        deleted = self.db.prune_hardcover_sync_logs(cutoff)
        self.assertEqual(deleted, 0)

        items, total = self.db.get_hardcover_sync_logs()
        self.assertEqual(total, 8)


class TestHardcoverLogService(unittest.TestCase):
    """Test the log_hardcover_action helper."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db = DatabaseService(str(Path(self.temp_dir) / 'test.db'))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_log_action_writes_entry(self):
        log_hardcover_action(
            self.db, abs_id=None, book_title='Test',
            direction='push', action='rating',
            detail={'rating': 4.5},
        )
        items, total = self.db.get_hardcover_sync_logs()
        self.assertEqual(total, 1)
        self.assertEqual(items[0].action, 'rating')
        parsed = json.loads(items[0].detail)
        self.assertEqual(parsed['rating'], 4.5)

    def test_log_action_never_raises(self):
        """Logging errors should be swallowed, not propagated."""
        mock_db = Mock()
        mock_db.add_hardcover_sync_log.side_effect = Exception("DB error")
        # Should not raise
        log_hardcover_action(
            mock_db, direction='push', action='test',
        )


class _MockContainer:
    """Minimal mock container that provides a real DatabaseService."""

    def __init__(self, db):
        self._db = db

    def database_service(self):
        return self._db

    def abs_client(self):
        return Mock()

    def hardcover_client(self):
        return Mock()

    def sync_manager(self):
        return Mock()

    def booklore_client(self):
        return Mock()

    def storyteller_client(self):
        return Mock()

    def ebook_parser(self):
        return Mock()

    def sync_clients(self):
        mock = Mock()
        mock.items.return_value = {}
        return mock

    def data_dir(self):
        return Path(tempfile.gettempdir()) / 'test_data'

    def books_dir(self):
        return Path(tempfile.gettempdir()) / 'test_books'

    def epub_cache_dir(self):
        return Path(tempfile.gettempdir()) / 'test_epub_cache'


class TestHardcoverSyncLogAPI(unittest.TestCase):
    """Test the /api/logs/hardcover endpoint."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        os.environ['DATA_DIR'] = self.temp_dir
        os.environ['BOOKS_DIR'] = self.temp_dir

        self.db = DatabaseService(str(Path(self.temp_dir) / 'test.db'))
        self.mock_container = _MockContainer(self.db)

        import src.db.migration_utils
        self.original_init_db = src.db.migration_utils.initialize_database
        src.db.migration_utils.initialize_database = lambda x: self.db

        from src.web_server import create_app
        self.app, _ = create_app(test_container=self.mock_container)
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

        # Seed data
        self.db.add_hardcover_sync_log(HardcoverSyncLog(
            book_title='API Test Book', direction='push', action='automatch',
            detail='{"matched_by": "isbn"}',
        ))
        self.db.add_hardcover_sync_log(HardcoverSyncLog(
            book_title='Pull Test', direction='pull', action='status_pull',
            success=False, error_message='Timeout',
        ))

    def tearDown(self):
        import shutil
        import src.db.migration_utils
        src.db.migration_utils.initialize_database = self.original_init_db
        os.environ.pop('DATA_DIR', None)
        os.environ.pop('BOOKS_DIR', None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_get_all_logs(self):
        resp = self.client.get('/api/logs/hardcover')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['total'], 2)
        self.assertEqual(len(data['logs']), 2)
        self.assertIn('total_pages', data)

    def test_filter_direction(self):
        resp = self.client.get('/api/logs/hardcover?direction=pull')
        data = resp.get_json()
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['logs'][0]['direction'], 'pull')

    def test_filter_action(self):
        resp = self.client.get('/api/logs/hardcover?action=automatch')
        data = resp.get_json()
        self.assertEqual(data['total'], 1)

    def test_filter_search(self):
        resp = self.client.get('/api/logs/hardcover?search=Pull')
        data = resp.get_json()
        self.assertEqual(data['total'], 1)

    def test_detail_parsed_as_json(self):
        resp = self.client.get('/api/logs/hardcover?action=automatch')
        data = resp.get_json()
        detail = data['logs'][0]['detail']
        self.assertIsInstance(detail, dict)
        self.assertEqual(detail['matched_by'], 'isbn')

    def test_per_page_clamped(self):
        resp = self.client.get('/api/logs/hardcover?per_page=9999')
        self.assertEqual(resp.status_code, 200)


class TestHardcoverSyncLogInstrumentation(unittest.TestCase):
    """Verify that sync client methods actually write log entries."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db = DatabaseService(str(Path(self.temp_dir) / 'test.db'))

        self.mock_hc = Mock()
        self.mock_abs = Mock()
        self.mock_parser = Mock()
        self.mock_hc.is_configured.return_value = True

        self.sync_client = HardcoverSyncClient(
            hardcover_client=self.mock_hc,
            ebook_parser=self.mock_parser,
            abs_client=self.mock_abs,
            database_service=self.db,
        )

        self.book = Book(abs_id='instr-test', abs_title='Instrumentation Book', status='active')
        self.db.save_book(self.book)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch('src.sync_clients.hardcover_sync_client.record_write')
    def test_push_local_status_logs_success(self, mock_rw):
        details = HardcoverDetails(
            abs_id='instr-test', hardcover_book_id='999',
            hardcover_edition_id='111', hardcover_pages=300,
        )
        self.db.save_hardcover_details(details)

        self.mock_hc.update_status.return_value = {'id': 1, 'status_id': 2}
        self.sync_client.push_local_status(self.book, 'active')

        items, total = self.db.get_hardcover_sync_logs(action='status_update')
        self.assertGreaterEqual(total, 1)
        self.assertEqual(items[0].direction, 'push')
        self.assertTrue(items[0].success)

    @patch('src.sync_clients.hardcover_sync_client.record_write')
    def test_push_local_status_logs_error(self, mock_rw):
        details = HardcoverDetails(
            abs_id='instr-test', hardcover_book_id='999',
            hardcover_edition_id='111',
        )
        self.db.save_hardcover_details(details)

        self.mock_hc.update_status.side_effect = Exception("API down")
        self.sync_client.push_local_status(self.book, 'active')

        items, total = self.db.get_hardcover_sync_logs(action='status_update')
        self.assertGreaterEqual(total, 1)
        self.assertFalse(items[0].success)
        self.assertIn("API down", items[0].error_message)

    @patch('src.sync_clients.hardcover_sync_client.record_write')
    def test_push_rating_logs(self, mock_rw):
        details = HardcoverDetails(
            abs_id='instr-test', hardcover_book_id='999',
            hardcover_edition_id='111',
            hardcover_user_book_id=42, hardcover_status_id=2,
        )
        self.db.save_hardcover_details(details)

        self.mock_hc.update_user_book.return_value = True
        result = self.sync_client.push_local_rating(self.book, 4.0)

        self.assertTrue(result['hardcover_synced'])
        items, total = self.db.get_hardcover_sync_logs(action='rating')
        self.assertGreaterEqual(total, 1)


if __name__ == '__main__':
    unittest.main()
