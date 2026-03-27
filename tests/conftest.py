"""Shared pytest fixtures and module stubs for the test suite."""

import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest

# Stub native modules only available inside Docker so that test files
# can import production code without raising ImportError.
for _mod_name in ('epubcfi',):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = ModuleType(_mod_name)


# ── MockABSService ─────────────────────────────────────────────────
# Lightweight stand-in for ABSService that avoids network calls.

class MockABSService:
    """Minimal ABS service mock suitable for route-level tests."""

    def is_available(self):
        return True

    def get_audiobooks(self):
        return []

    def get_cover_proxy_url(self, abs_id):
        return f'/covers/{abs_id}.jpg'

    def add_to_collection(self, abs_id, collection_name):
        pass


# ── Canonical MockContainer ────────────────────────────────────────
# Superset of every per-file variant.  Individual tests can override
# attributes after construction (e.g. ``mc.mock_abs_client.is_configured …``).

class MockContainer:
    """Test-friendly replacement for the DI Container.

    Every service accessor is backed by a ``Mock()`` instance stored as
    ``self.mock_<name>``.  Override attributes/return-values in your
    test setUp or fixture as needed.
    """

    def __init__(self):
        # ── Database ──
        self.mock_database_service = Mock()
        self.mock_database_service.get_all_settings.return_value = {}
        self.mock_database_service.get_book_by_ref.return_value = None
        self.mock_database_service.get_all_books.return_value = []
        self.mock_database_service.get_books_by_status.return_value = []
        self.mock_database_service.get_all_pending_suggestions.return_value = []
        self.mock_database_service.get_all_actionable_suggestions.return_value = []
        self.mock_database_service.get_bookfusion_books.return_value = []
        self.mock_database_service.get_bookfusion_linked_book_ids.return_value = set()
        self.mock_database_service.get_bookfusion_highlight_counts_by_book_id.return_value = {}

        # ── API Clients ──
        self.mock_abs_client = Mock()
        self.mock_abs_client.is_configured.return_value = False
        self.mock_booklore_client = Mock()
        self.mock_booklore_client.is_configured.return_value = False
        self.mock_storyteller_client = Mock()
        self.mock_storyteller_client.is_configured.return_value = False
        self.mock_hardcover_client = Mock()
        self.mock_hardcover_client.is_configured.return_value = False
        self.mock_bookfusion_client = Mock()
        self.mock_bookfusion_client.is_configured.return_value = False
        self.mock_bookfusion_client.highlights_api_key = ''
        self.mock_bookfusion_client.upload_api_key = ''

        # ── Services ──
        self.mock_abs_service = MockABSService()
        self.mock_hardcover_service = Mock()
        self.mock_hardcover_service.is_configured.return_value = False
        self.mock_reading_date_service = Mock()
        self.mock_reading_date_service.pull_reading_dates.return_value = {}
        self.mock_reading_date_service.push_dates_to_hardcover.return_value = (True, "Dates synced")

        # ── Sync Clients ──
        self.mock_hardcover_sync_client = Mock()
        self.mock_hardcover_sync_client.is_configured.return_value = False

        # ── Utilities ──
        self.mock_ebook_parser = Mock()

        # ── Manager ──
        self.mock_sync_manager = Mock()
        self.mock_sync_manager.abs_client = self.mock_abs_client
        self.mock_sync_manager.booklore_client = self.mock_booklore_client
        self.mock_sync_manager.storyteller_client = self.mock_storyteller_client
        self.mock_sync_manager.get_audiobook_title.return_value = 'Test Book Title'
        self.mock_sync_manager.get_duration.return_value = 3600
        self.mock_sync_manager.clear_progress = Mock()

        # ── Paths (temp) ──
        self._tmp = Path(tempfile.gettempdir())

    # ── Accessors (match Container's callable interface) ──

    def database_service(self):
        return self.mock_database_service

    def sync_manager(self):
        return self.mock_sync_manager

    def abs_client(self):
        return self.mock_abs_client

    def abs_service(self):
        return self.mock_abs_service

    def booklore_client(self):
        return self.mock_booklore_client

    def booklore_client_group(self):
        return self.mock_booklore_client

    def storyteller_client(self):
        return self.mock_storyteller_client

    def bookfusion_client(self):
        return self.mock_bookfusion_client

    def hardcover_client(self):
        return self.mock_hardcover_client

    def hardcover_service(self):
        return self.mock_hardcover_service

    def hardcover_sync_client(self):
        return self.mock_hardcover_sync_client

    def reading_date_service(self):
        return self.mock_reading_date_service

    def ebook_parser(self):
        return self.mock_ebook_parser

    def sync_clients(self):
        return {}

    def data_dir(self):
        return self._tmp / 'test_data'

    def books_dir(self):
        return self._tmp / 'test_books'

    def epub_cache_dir(self):
        return self._tmp / 'test_epub_cache'


# ── Pytest fixtures ────────────────────────────────────────────────

@pytest.fixture()
def mock_container():
    """Yield a fresh MockContainer for each test."""
    return MockContainer()


@pytest.fixture()
def flask_app(mock_container, tmp_path):
    """Create a Flask test app wired to the given mock_container."""
    saved_env = os.environ.copy()
    os.environ['DATA_DIR'] = str(tmp_path)

    import src.db.migration_utils
    original_init_db = src.db.migration_utils.initialize_database
    src.db.migration_utils.initialize_database = lambda data_dir: mock_container.mock_database_service

    try:
        from src.web_server import create_app
        app, _ = create_app(test_container=mock_container)
        app.config['TESTING'] = True
        yield app
    finally:
        src.db.migration_utils.initialize_database = original_init_db
        # Restore environment to avoid leaking bootstrapped settings
        os.environ.clear()
        os.environ.update(saved_env)


@pytest.fixture()
def client(flask_app):
    """Return a Flask test client."""
    return flask_app.test_client()


# ── Test data helpers ──────────────────────────────────────────────

def make_test_book(**overrides):
    """Build a dict resembling a book/mapping row, with sensible defaults.

    Usage::

        book = make_test_book(title='Dune', abs_id='abc-123')
    """
    defaults = {
        'id': 1,
        'abs_id': 'test-abs-id',
        'title': 'Test Book',
        'author': 'Test Author',
        'status': 'active',
        'ebook_source': None,
        'ebook_id': None,
        'audio_progress': 0.0,
        'ebook_progress': 0.0,
        'duration': 3600,
        'hardcover_book_id': None,
        'hardcover_edition_id': None,
    }
    defaults.update(overrides)
    return defaults


def make_test_state(**overrides):
    """Build a dict resembling a sync state row, with sensible defaults.

    Usage::

        state = make_test_state(abs_id='abc-123', audio_progress=0.5)
    """
    defaults = {
        'id': 1,
        'abs_id': 'test-abs-id',
        'audio_progress': 0.0,
        'audio_current_time': 0.0,
        'ebook_progress': 0.0,
        'ebook_cfi': None,
        'last_sync_source': None,
        'last_updated': None,
    }
    defaults.update(overrides)
    return defaults
