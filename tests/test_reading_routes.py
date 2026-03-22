"""Route tests for reading stats and rating sync behavior."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.models import Book, State  # noqa: E402


class MockABSService:
    def is_available(self):
        return True

    def get_audiobooks(self):
        return []

    def get_cover_proxy_url(self, abs_id):
        return f'/covers/{abs_id}.jpg'


class MockContainer:
    def __init__(self):
        self.mock_database_service = Mock()
        self.mock_database_service.get_all_settings.return_value = {}
        self.mock_database_service.get_book_by_ref.return_value = None
        self.mock_hardcover_sync_client = Mock()
        self.mock_hardcover_sync_client.is_configured.return_value = False
        self.mock_hardcover_client = Mock()
        self.mock_hardcover_client.is_configured.return_value = False
        self.mock_abs_client = Mock()
        self.mock_abs_service = MockABSService()
        self.mock_booklore_client = Mock()
        self.mock_booklore_client.is_configured.return_value = False
        self.mock_storyteller_client = Mock()
        self.mock_storyteller_client.is_configured.return_value = False
        self.mock_bookfusion_client = Mock()
        self.mock_bookfusion_client.is_configured.return_value = False
        self.mock_hardcover_service = Mock()
        self.mock_hardcover_service.is_configured.return_value = False
        self.mock_reading_date_service = Mock()
        self.mock_reading_date_service.pull_reading_dates.return_value = {}
        self.mock_reading_date_service.push_dates_to_hardcover.return_value = (True, "Dates synced")

    def database_service(self):
        return self.mock_database_service

    def abs_client(self):
        return self.mock_abs_client

    def abs_service(self):
        return self.mock_abs_service

    def hardcover_client(self):
        return self.mock_hardcover_client

    def hardcover_sync_client(self):
        return self.mock_hardcover_sync_client

    def hardcover_service(self):
        return self.mock_hardcover_service

    def reading_date_service(self):
        return self.mock_reading_date_service

    def booklore_client(self):
        return self.mock_booklore_client

    def booklore_client_group(self):
        return self.mock_booklore_client

    def storyteller_client(self):
        return self.mock_storyteller_client

    def bookfusion_client(self):
        return self.mock_bookfusion_client

    def sync_manager(self):
        return Mock()

    def ebook_parser(self):
        return Mock()

    def sync_clients(self):
        return {}

    def data_dir(self):
        return Path(tempfile.gettempdir()) / 'test_data'

    def books_dir(self):
        return Path(tempfile.gettempdir()) / 'test_books'

    def epub_cache_dir(self):
        return Path(tempfile.gettempdir()) / 'test_epub_cache'


class TestReadingRoutes(unittest.TestCase):
    def setUp(self):
        import shutil

        import src.db.migration_utils

        # Scope nh3 stub to this test class
        self._nh3_original = sys.modules.get('nh3')
        sys.modules['nh3'] = SimpleNamespace(clean=lambda value, tags=None, attributes=None: value)

        self.temp_dir = tempfile.mkdtemp()
        original_data_dir = os.environ.get('DATA_DIR')
        original_books_dir = os.environ.get('BOOKS_DIR')
        original_template_dir = os.environ.get('TEMPLATE_DIR')
        original_static_dir = os.environ.get('STATIC_DIR')
        project_root = str(Path(__file__).parent.parent)
        os.environ['DATA_DIR'] = self.temp_dir
        os.environ['BOOKS_DIR'] = self.temp_dir
        os.environ['TEMPLATE_DIR'] = str(Path(project_root) / 'templates')
        os.environ['STATIC_DIR'] = str(Path(project_root) / 'static')
        self.mock_container = MockContainer()

        original_init_db = src.db.migration_utils.initialize_database
        src.db.migration_utils.initialize_database = lambda data_dir: self.mock_container.mock_database_service

        # Register cleanup so it runs even if setUp raises after this point
        def cleanup():
            src.db.migration_utils.initialize_database = original_init_db
            if original_data_dir is None:
                os.environ.pop('DATA_DIR', None)
            else:
                os.environ['DATA_DIR'] = original_data_dir
            if original_books_dir is None:
                os.environ.pop('BOOKS_DIR', None)
            else:
                os.environ['BOOKS_DIR'] = original_books_dir
            if original_template_dir is None:
                os.environ.pop('TEMPLATE_DIR', None)
            else:
                os.environ['TEMPLATE_DIR'] = original_template_dir
            if original_static_dir is None:
                os.environ.pop('STATIC_DIR', None)
            else:
                os.environ['STATIC_DIR'] = original_static_dir
            shutil.rmtree(self.temp_dir, ignore_errors=True)
            if self._nh3_original is None:
                sys.modules.pop('nh3', None)
            else:
                sys.modules['nh3'] = self._nh3_original

        self.addCleanup(cleanup)

        from src.web_server import create_app
        self.app, _ = create_app(test_container=self.mock_container)
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        self.db = self.mock_container.mock_database_service
        self.db.get_hardcover_details.return_value = None

    def test_stats_endpoint_returns_rich_payload(self):
        self.db.get_all_books.return_value = [
            Book(abs_id='done', title='Done', status='completed'),
            Book(abs_id='active', title='Active', status='active'),
            Book(abs_id='dnf', title='DNF', status='dnf'),
        ]
        self.db.get_all_books.return_value[0].finished_at = '2026-03-01'
        self.db.get_all_books.return_value[0].rating = 4.5
        self.db.get_all_books.return_value[2].finished_at = '2026-04-01'
        self.db.get_all_states.return_value = [
            State(abs_id='active', client_name='manual', percentage=0.4),
        ]
        self.db.get_reading_goal.return_value = SimpleNamespace(target_books=12)

        resp = self.client.get('/api/reading/stats/2026')
        data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(data['books_finished'], 1)
        self.assertEqual(data['currently_reading'], 1)
        self.assertEqual(data['total_tracked'], 3)
        self.assertEqual(data['goal_target'], 12)
        self.assertEqual(data['goal_completed'], 1)
        self.assertEqual(data['monthly_finished'][2], 1)
        self.assertAlmostEqual(data['average_rating'], 4.5)

    def test_rating_endpoint_returns_local_success_when_hardcover_sync_fails(self):
        book = Book(abs_id='book-1', title='Test', status='completed')
        book.id = 101
        book.rating = 3.5
        self.db.get_book_by_ref.return_value = book
        self.db.update_book_reading_fields.return_value = book
        self.mock_container.mock_hardcover_service.is_configured.return_value = True
        self.mock_container.mock_hardcover_service.push_local_rating.return_value = {
            'hardcover_synced': False,
            'hardcover_error': 'boom',
        }

        resp = self.client.post('/api/reading/book/book-1/rating', json={'rating': 3.5})
        data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])
        self.assertEqual(data['rating'], book.rating)
        self.assertFalse(data['hardcover_synced'])
        self.assertEqual(data['hardcover_error'], 'boom')

    def test_rating_endpoint_accepts_half_stars(self):
        book = Book(abs_id='book-1', title='Test', status='completed')
        book.id = 101
        book.rating = 4.5
        self.db.get_book_by_ref.return_value = book
        self.db.update_book_reading_fields.return_value = book

        resp = self.client.post('/api/reading/book/book-1/rating', json={'rating': 4.5})
        data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])
        self.db.update_book_reading_fields.assert_called_once_with(101, rating=4.5)

    def test_rating_endpoint_rejects_non_half_increment(self):
        book = Book(abs_id='book-1', title='Test', status='completed')
        book.id = 101
        self.db.get_book_by_ref.return_value = book
        resp = self.client.post('/api/reading/book/book-1/rating', json={'rating': 4.3})
        self.assertEqual(resp.status_code, 400)

    def test_rating_endpoint_accepts_numeric_book_ref(self):
        book = Book(abs_id='book-1', title='Test', status='completed')
        book.id = 42
        book.rating = 5.0
        self.db.get_book_by_ref.return_value = book
        self.db.update_book_reading_fields.return_value = book

        resp = self.client.post('/api/reading/book/42/rating', json={'rating': 5.0})
        data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])
        self.db.get_book_by_ref.assert_called_with('42')
        self.db.update_book_reading_fields.assert_called_with(42, rating=5.0)

    def test_reading_page_renders_log_and_stats_tabs(self):
        book = Book(abs_id='book-1', title='Test Book', status='active')
        state = State(abs_id='book-1', client_name='manual', percentage=0.5)
        self.db.get_all_books.return_value = [book]
        self.db.get_all_states.return_value = [state]
        self.db.get_states_by_book.return_value = {None: [state]}
        self.db.get_booklore_by_filename.return_value = {}
        self.db.get_all_booklore_books.return_value = []
        self.db.get_all_hardcover_details.return_value = []
        self.db.get_reading_goal.return_value = None

        resp = self.client.get('/reading')

        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'>Log<', resp.data)
        self.assertIn(b'>Stats<', resp.data)
        self.assertIn(b'Monthly Completions', resp.data)
