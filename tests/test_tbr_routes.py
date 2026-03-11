"""Route tests for TBR (To Be Read) API endpoints."""

import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.models import Book, TbrItem


def _make_tbr_item(id=1, title='Test Book', author='Test Author', source='manual',
                   cover_url=None, notes=None, hardcover_book_id=None,
                   hardcover_slug=None, ol_work_key=None, isbn=None,
                   book_abs_id=None, hardcover_list_name=None):
    """Create a TbrItem with manually set id and added_at."""
    item = TbrItem(
        title=title, author=author, cover_url=cover_url, notes=notes,
        source=source, hardcover_book_id=hardcover_book_id,
        hardcover_slug=hardcover_slug, ol_work_key=ol_work_key, isbn=isbn,
        book_abs_id=book_abs_id, hardcover_list_name=hardcover_list_name,
    )
    item.id = id
    item.added_at = datetime(2026, 3, 10, 12, 0, 0)
    return item


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

    def booklore_client(self):
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


class TestTbrRoutes(unittest.TestCase):

    def setUp(self):
        import src.db.migration_utils

        self._nh3_original = sys.modules.get('nh3')
        sys.modules['nh3'] = SimpleNamespace(clean=lambda value, tags=None, attributes=None: value)

        self.temp_dir = tempfile.mkdtemp()
        original_data_dir = os.environ.get('DATA_DIR')
        original_books_dir = os.environ.get('BOOKS_DIR')
        os.environ['DATA_DIR'] = self.temp_dir
        os.environ['BOOKS_DIR'] = self.temp_dir
        self.mock_container = MockContainer()

        original_init_db = src.db.migration_utils.initialize_database
        src.db.migration_utils.initialize_database = lambda data_dir: self.mock_container.mock_database_service

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
        self.db.get_tbr_count.return_value = 0

    # ── GET /api/reading/tbr ──

    def test_get_tbr_empty(self):
        """Empty TBR list returns empty JSON array."""
        self.db.get_tbr_items.return_value = []
        resp = self.client.get('/api/reading/tbr')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), [])

    def test_get_tbr_with_data(self):
        """TBR list serializes all fields correctly."""
        item = _make_tbr_item(
            id=1, title='Dune', author='Frank Herbert',
            source='hardcover_search', hardcover_book_id=42,
            hardcover_slug='dune', book_abs_id='abs-1',
        )
        self.db.get_tbr_items.return_value = [item]

        resp = self.client.get('/api/reading/tbr')
        data = resp.get_json()

        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['id'], 1)
        self.assertEqual(data[0]['title'], 'Dune')
        self.assertEqual(data[0]['author'], 'Frank Herbert')
        self.assertEqual(data[0]['source'], 'hardcover_search')
        self.assertEqual(data[0]['hardcover_book_id'], 42)
        self.assertEqual(data[0]['hardcover_slug'], 'dune')
        self.assertEqual(data[0]['book_abs_id'], 'abs-1')
        self.assertIn('added_at', data[0])

    # ── POST /api/reading/tbr/add ──

    def test_add_manual(self):
        """Manual add with just title and author."""
        item = _make_tbr_item(id=1, title='My Book', author='Me', source='manual')
        self.db.add_tbr_item.return_value = (item, True)
        self.db.get_all_hardcover_details.return_value = []

        resp = self.client.post('/api/reading/tbr/add', json={
            'title': 'My Book', 'author': 'Me',
        })
        data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])
        self.assertTrue(data['created'])
        self.assertEqual(data['item']['title'], 'My Book')

    def test_add_hardcover_source(self):
        """Adding with hardcover_book_id sets source to hardcover_search."""
        item = _make_tbr_item(source='hardcover_search', hardcover_book_id=42)
        self.db.add_tbr_item.return_value = (item, True)
        self.db.get_all_hardcover_details.return_value = []

        resp = self.client.post('/api/reading/tbr/add', json={
            'title': 'Dune', 'hardcover_book_id': 42,
        })
        data = resp.get_json()

        self.assertTrue(data['success'])
        # Verify source was passed as hardcover_search
        call_kwargs = self.db.add_tbr_item.call_args
        self.assertEqual(call_kwargs.kwargs.get('source') or call_kwargs[1].get('source'), 'hardcover_search')

    def test_add_ol_source(self):
        """Adding with ol_work_key sets source to open_library."""
        item = _make_tbr_item(source='open_library', ol_work_key='/works/OL123')
        self.db.add_tbr_item.return_value = (item, True)
        self.db.get_all_hardcover_details.return_value = []

        resp = self.client.post('/api/reading/tbr/add', json={
            'title': 'Neuromancer', 'ol_work_key': '/works/OL123',
        })

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()['success'])

    def test_add_missing_title(self):
        """Missing title returns 400."""
        resp = self.client.post('/api/reading/tbr/add', json={'author': 'Someone'})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Title is required', resp.get_json()['error'])

    def test_add_empty_title(self):
        """Whitespace-only title returns 400."""
        resp = self.client.post('/api/reading/tbr/add', json={'title': '   '})
        self.assertEqual(resp.status_code, 400)

    def test_add_duplicate(self):
        """Duplicate returns created=false."""
        existing = _make_tbr_item(id=1, title='Dune')
        self.db.add_tbr_item.return_value = (existing, False)
        self.db.get_all_hardcover_details.return_value = []

        resp = self.client.post('/api/reading/tbr/add', json={'title': 'Dune'})
        data = resp.get_json()

        self.assertTrue(data['success'])
        self.assertFalse(data['created'])

    def test_add_auto_links_via_hardcover_details(self):
        """When HC book_id matches an existing HardcoverDetails, book_abs_id is set."""
        hc_detail = SimpleNamespace(hardcover_book_id=42, abs_id='abs-owned')
        self.db.get_all_hardcover_details.return_value = [hc_detail]

        item = _make_tbr_item(hardcover_book_id=42, book_abs_id='abs-owned')
        self.db.add_tbr_item.return_value = (item, True)

        resp = self.client.post('/api/reading/tbr/add', json={
            'title': 'Dune', 'hardcover_book_id': 42,
        })
        data = resp.get_json()

        self.assertTrue(data['success'])
        # Verify book_abs_id was passed to add_tbr_item
        call_kwargs = self.db.add_tbr_item.call_args
        self.assertEqual(
            call_kwargs.kwargs.get('book_abs_id') or call_kwargs[1].get('book_abs_id'),
            'abs-owned',
        )

    def test_add_hc_item_pushes_want_to_read(self):
        """Adding a new TBR item with hardcover_book_id pushes WTR to Hardcover."""
        self.mock_container.mock_hardcover_client.is_configured.return_value = True
        item = _make_tbr_item(source='hardcover_search', hardcover_book_id=42)
        self.db.add_tbr_item.return_value = (item, True)
        self.db.get_all_hardcover_details.return_value = []

        resp = self.client.post('/api/reading/tbr/add', json={
            'title': 'Dune', 'hardcover_book_id': 42,
        })
        self.assertEqual(resp.status_code, 200)

        # Verify update_status called with book_id=42, status=1 (Want to Read)
        self.mock_container.mock_hardcover_client.update_status.assert_called_once_with(42, 1)

    def test_add_hc_item_skips_push_when_not_configured(self):
        """Adding a TBR item with HC ID does NOT push when HC is not configured."""
        self.mock_container.mock_hardcover_client.is_configured.return_value = False
        item = _make_tbr_item(source='hardcover_search', hardcover_book_id=42)
        self.db.add_tbr_item.return_value = (item, True)
        self.db.get_all_hardcover_details.return_value = []

        resp = self.client.post('/api/reading/tbr/add', json={
            'title': 'Dune', 'hardcover_book_id': 42,
        })
        self.assertEqual(resp.status_code, 200)

        # update_status should NOT have been called
        self.mock_container.mock_hardcover_client.update_status.assert_not_called()

    def test_add_duplicate_hc_item_skips_push(self):
        """Duplicate HC item (created=False) does NOT push to Hardcover."""
        self.mock_container.mock_hardcover_client.is_configured.return_value = True
        existing = _make_tbr_item(id=1, title='Dune', hardcover_book_id=42)
        self.db.add_tbr_item.return_value = (existing, False)
        self.db.get_all_hardcover_details.return_value = []

        resp = self.client.post('/api/reading/tbr/add', json={
            'title': 'Dune', 'hardcover_book_id': 42,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.get_json()['created'])

        # No push for duplicates
        self.mock_container.mock_hardcover_client.update_status.assert_not_called()

    # ── DELETE /api/reading/tbr/<id> ──

    def test_delete_success(self):
        """Successful delete returns success."""
        self.db.delete_tbr_item.return_value = True
        resp = self.client.delete('/api/reading/tbr/1')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()['success'])

    def test_delete_not_found(self):
        """Delete of missing item returns 404."""
        self.db.get_tbr_item.return_value = None
        resp = self.client.delete('/api/reading/tbr/999')
        self.assertEqual(resp.status_code, 404)

    # ── POST /api/reading/tbr/<id>/start ──

    @patch('src.blueprints.tbr_bp._pull_started_at', return_value='2026-03-10')
    def test_start_success(self, mock_pull):
        """Start transitions book to active, adds journal entry, deletes TBR item."""
        item = _make_tbr_item(id=1, book_abs_id='abs-1')
        self.db.get_tbr_item.return_value = item

        book = Book(abs_id='abs-1', abs_title='Dune', status='paused')
        book.started_at = None
        self.db.get_book.return_value = book
        self.db.save_book.return_value = book
        self.db.update_book_reading_fields.return_value = book
        self.db.add_reading_journal.return_value = Mock()
        self.db.delete_tbr_item.return_value = True

        resp = self.client.post('/api/reading/tbr/1/start')
        data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])
        self.assertEqual(data['abs_id'], 'abs-1')

        # Book saved as active
        saved_book = self.db.save_book.call_args[0][0]
        self.assertEqual(saved_book.status, 'active')

        # Journal entry added
        self.db.add_reading_journal.assert_called_once_with('abs-1', event='started')

        # TBR item deleted
        self.db.delete_tbr_item.assert_called_once_with(1)

    def test_start_not_found(self):
        """Start with missing TBR item returns 404."""
        self.db.get_tbr_item.return_value = None
        resp = self.client.post('/api/reading/tbr/999/start')
        self.assertEqual(resp.status_code, 404)

    def test_start_no_linked_book(self):
        """Start without linked book returns 400."""
        item = _make_tbr_item(id=1, book_abs_id=None)
        self.db.get_tbr_item.return_value = item

        resp = self.client.post('/api/reading/tbr/1/start')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('not in library', resp.get_json()['error'])

    def test_start_linked_book_missing(self):
        """Start with linked book that doesn't exist returns 404."""
        item = _make_tbr_item(id=1, book_abs_id='abs-gone')
        self.db.get_tbr_item.return_value = item
        self.db.get_book.return_value = None

        resp = self.client.post('/api/reading/tbr/1/start')
        self.assertEqual(resp.status_code, 404)
        self.assertIn('Linked book not found', resp.get_json()['error'])

    # ── POST /api/reading/tbr/search ──

    def test_search_hardcover_provider(self):
        """Search with hardcover provider uses HC client."""
        self.mock_container.mock_hardcover_client.is_configured.return_value = True
        self.mock_container.mock_hardcover_client.search_books_with_covers.return_value = [
            {'title': 'Dune', 'author': 'Frank Herbert', 'cached_image': 'http://img.jpg',
             'book_id': 42, 'slug': 'dune'},
        ]

        resp = self.client.post('/api/reading/tbr/search', json={
            'query': 'Dune', 'provider': 'hardcover',
        })
        data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(data['provider'], 'hardcover')
        self.assertEqual(len(data['results']), 1)
        self.assertEqual(data['results'][0]['title'], 'Dune')
        self.assertEqual(data['results'][0]['hardcover_book_id'], 42)

    @patch('src.blueprints.tbr_bp.OpenLibraryClient', create=True)
    def test_search_ol_provider(self, MockOLClient):
        """Search with open_library provider uses OL client."""
        # The route does `from src.api.open_library_client import OpenLibraryClient`
        # so we need to patch at the import location
        with patch('src.api.open_library_client.OpenLibraryClient') as MockOL:
            mock_instance = Mock()
            mock_instance.search_books.return_value = [
                {'title': 'Dune', 'author': 'Frank Herbert', 'cover_url': 'http://ol.jpg',
                 'ol_work_key': '/works/OL123', 'isbn': '9780441013593'},
            ]
            MockOL.return_value = mock_instance

            # We need to patch at the module level where it's imported inline
            with patch.dict('sys.modules', {}):
                # Simpler: just patch the class directly where it gets imported
                pass

        # Use a simpler approach — patch at the actual usage point
        mock_ol = Mock()
        mock_ol.search_books.return_value = [
            {'title': 'Dune', 'author': 'Frank Herbert', 'cover_url': 'http://ol.jpg',
             'ol_work_key': '/works/OL123', 'isbn': '9780441013593'},
        ]
        with patch('src.api.open_library_client.OpenLibraryClient', return_value=mock_ol):
            resp = self.client.post('/api/reading/tbr/search', json={
                'query': 'Dune', 'provider': 'open_library',
            })

        data = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(data['provider'], 'open_library')
        self.assertEqual(len(data['results']), 1)
        self.assertEqual(data['results'][0]['ol_work_key'], '/works/OL123')

    def test_search_auto_selects_hardcover_when_configured(self):
        """Without explicit provider, auto-selects hardcover if configured."""
        self.mock_container.mock_hardcover_client.is_configured.return_value = True
        self.mock_container.mock_hardcover_client.search_books_with_covers.return_value = []

        resp = self.client.post('/api/reading/tbr/search', json={'query': 'Dune'})
        data = resp.get_json()

        self.assertEqual(data['provider'], 'hardcover')

    def test_search_auto_selects_ol_when_hc_not_configured(self):
        """Without explicit provider, falls back to OL if HC not configured."""
        self.mock_container.mock_hardcover_client.is_configured.return_value = False

        mock_ol = Mock()
        mock_ol.search_books.return_value = []
        with patch('src.api.open_library_client.OpenLibraryClient', return_value=mock_ol):
            resp = self.client.post('/api/reading/tbr/search', json={'query': 'Dune'})
        data = resp.get_json()

        self.assertEqual(data['provider'], 'open_library')

    def test_search_empty_query(self):
        """Empty or too-short query returns empty results."""
        resp = self.client.post('/api/reading/tbr/search', json={'query': ''})
        data = resp.get_json()
        self.assertEqual(data['results'], [])
        self.assertIsNone(data['provider'])

    def test_search_short_query(self):
        """Single-char query returns empty results."""
        resp = self.client.post('/api/reading/tbr/search', json={'query': 'D'})
        data = resp.get_json()
        self.assertEqual(data['results'], [])

    def test_search_hc_failure_falls_back_to_ol(self):
        """If HC search raises, falls back to Open Library."""
        self.mock_container.mock_hardcover_client.is_configured.return_value = True
        self.mock_container.mock_hardcover_client.search_books_with_covers.side_effect = Exception("HC down")

        mock_ol = Mock()
        mock_ol.search_books.return_value = [
            {'title': 'Dune', 'author': 'Herbert', 'cover_url': None,
             'ol_work_key': '/works/OL1', 'isbn': None},
        ]
        with patch('src.api.open_library_client.OpenLibraryClient', return_value=mock_ol):
            resp = self.client.post('/api/reading/tbr/search', json={
                'query': 'Dune', 'provider': 'hardcover',
            })
        data = resp.get_json()

        self.assertEqual(data['provider'], 'open_library')
        self.assertEqual(len(data['results']), 1)

    # ── POST /api/reading/tbr/import-hardcover ──

    def test_import_hardcover_success(self):
        """Import HC want-to-read books with counts."""
        self.mock_container.mock_hardcover_client.is_configured.return_value = True
        self.mock_container.mock_hardcover_client.get_want_to_read_books.return_value = [
            {'book_id': 1, 'title': 'Book A', 'author': 'Auth A', 'slug': 'book-a'},
            {'book_id': 2, 'title': 'Book B', 'author': 'Auth B', 'slug': 'book-b'},
        ]
        self.db.get_all_hardcover_details.return_value = []
        # First created, second skipped
        self.db.add_tbr_item.side_effect = [
            (_make_tbr_item(id=10), True),
            (_make_tbr_item(id=11), False),
        ]

        resp = self.client.post('/api/reading/tbr/import-hardcover')
        data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])
        self.assertEqual(data['imported'], 1)
        self.assertEqual(data['skipped'], 1)

    def test_import_hardcover_not_configured(self):
        """Import when HC not configured returns 400."""
        self.mock_container.mock_hardcover_client.is_configured.return_value = False
        resp = self.client.post('/api/reading/tbr/import-hardcover')
        self.assertEqual(resp.status_code, 400)

    def test_import_hardcover_auto_links(self):
        """Import auto-links books that match existing HardcoverDetails."""
        self.mock_container.mock_hardcover_client.is_configured.return_value = True
        self.mock_container.mock_hardcover_client.get_want_to_read_books.return_value = [
            {'book_id': 42, 'title': 'Owned Book', 'slug': 'owned'},
        ]
        hc_detail = SimpleNamespace(hardcover_book_id=42, abs_id='abs-owned')
        self.db.get_all_hardcover_details.return_value = [hc_detail]
        self.db.add_tbr_item.return_value = (_make_tbr_item(id=10, book_abs_id='abs-owned'), True)

        resp = self.client.post('/api/reading/tbr/import-hardcover')
        self.assertEqual(resp.status_code, 200)

        # Verify book_abs_id was passed
        call_kwargs = self.db.add_tbr_item.call_args
        self.assertEqual(
            call_kwargs.kwargs.get('book_abs_id') or call_kwargs[1].get('book_abs_id'),
            'abs-owned',
        )

    # ── GET /api/reading/tbr/hardcover-lists ──

    def test_hardcover_lists_success(self):
        """Returns formatted lists when HC is configured."""
        self.mock_container.mock_hardcover_client.is_configured.return_value = True
        self.mock_container.mock_hardcover_client.get_user_lists.return_value = [
            {'id': 1, 'name': 'SciFi', 'description': 'My sci-fi list', 'books_count': 5},
        ]

        resp = self.client.get('/api/reading/tbr/hardcover-lists')
        data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['name'], 'SciFi')
        self.assertEqual(data[0]['books_count'], 5)

    def test_hardcover_lists_not_configured(self):
        """Returns empty list when HC not configured."""
        self.mock_container.mock_hardcover_client.is_configured.return_value = False
        resp = self.client.get('/api/reading/tbr/hardcover-lists')
        self.assertEqual(resp.get_json(), [])

    # ── POST /api/reading/tbr/import-hardcover-list ──

    def test_import_list_success(self):
        """Import from specific HC list with counts."""
        self.mock_container.mock_hardcover_client.is_configured.return_value = True
        self.mock_container.mock_hardcover_client.get_list_books.return_value = {
            'name': 'SciFi',
            'books': [
                {'book_id': 10, 'title': 'Book X', 'slug': 'book-x'},
                {'book_id': 11, 'title': 'Book Y', 'slug': 'book-y'},
            ],
        }
        self.db.get_all_hardcover_details.return_value = []
        self.db.add_tbr_item.side_effect = [
            (_make_tbr_item(id=20), True),
            (_make_tbr_item(id=21), True),
        ]

        resp = self.client.post('/api/reading/tbr/import-hardcover-list', json={'list_id': 1})
        data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])
        self.assertEqual(data['imported'], 2)
        self.assertEqual(data['skipped'], 0)
        self.assertEqual(data['list_name'], 'SciFi')

    def test_import_list_missing_list_id(self):
        """Missing list_id returns 400."""
        resp = self.client.post('/api/reading/tbr/import-hardcover-list', json={})
        self.assertEqual(resp.status_code, 400)

    def test_import_list_not_configured(self):
        """HC not configured returns 400."""
        self.mock_container.mock_hardcover_client.is_configured.return_value = False
        resp = self.client.post('/api/reading/tbr/import-hardcover-list', json={'list_id': 1})
        self.assertEqual(resp.status_code, 400)

    def test_import_list_invalid_list_id(self):
        """Non-numeric list_id returns 400."""
        resp = self.client.post('/api/reading/tbr/import-hardcover-list', json={'list_id': 'abc'})
        self.assertEqual(resp.status_code, 400)

    def test_import_list_not_found(self):
        """List that returns no data gives 404."""
        self.mock_container.mock_hardcover_client.is_configured.return_value = True
        self.mock_container.mock_hardcover_client.get_list_books.return_value = None

        resp = self.client.post('/api/reading/tbr/import-hardcover-list', json={'list_id': 999})
        self.assertEqual(resp.status_code, 404)


if __name__ == '__main__':
    unittest.main()
