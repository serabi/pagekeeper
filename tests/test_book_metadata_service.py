"""Tests for book_metadata_service — focused on error paths and fallback behavior."""

import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.book_metadata_service import build_book_metadata, build_service_info


def _make_book(**overrides):
    book = Mock()
    book.id = overrides.get('id', 1)
    book.abs_id = overrides.get('abs_id', 'abs-123')
    book.sync_mode = overrides.get('sync_mode', 'audio_ebook')
    book.author = overrides.get('author', 'Cached Author')
    book.subtitle = overrides.get('subtitle', 'Cached Subtitle')
    book.duration = overrides.get('duration', 7200)
    book.ebook_filename = overrides.get('ebook_filename', 'book.epub')
    book.original_ebook_filename = overrides.get('original_ebook_filename', None)
    book.kosync_doc_id = overrides.get('kosync_doc_id', None)
    book.storyteller_uuid = overrides.get('storyteller_uuid', None)
    book.title = overrides.get('title', 'Test Book')
    book.status = overrides.get('status', 'active')
    return book


def _make_container(hc_configured=False, bl_configured=False):
    container = Mock()

    hc_client = Mock()
    hc_client.is_configured.return_value = hc_configured
    container.hardcover_client.return_value = hc_client

    bl_client = Mock()
    bl_client.is_configured.return_value = bl_configured
    container.booklore_client_group.return_value = bl_client
    container.booklore_client.return_value = bl_client

    container.storyteller_client.return_value = Mock(is_configured=Mock(return_value=False))
    container.bookfusion_client.return_value = Mock(is_configured=Mock(return_value=False))

    return container


def _make_abs_service(available=True, item_details=None):
    svc = Mock()
    svc.is_available.return_value = available
    svc.get_item_details.return_value = item_details
    svc.abs_client = Mock(base_url='http://abs:13378')
    return svc


def _make_db_service(hardcover=None, bf_book=None):
    db = Mock()
    db.get_hardcover_details.return_value = hardcover
    db.get_bookfusion_book_by_book_id.return_value = bf_book
    return db


# ---------------------------------------------------------------------------
# ABS API failure
# ---------------------------------------------------------------------------

class TestABSFailureFallback:
    """When abs_service.get_item_details raises, metadata should fall back to cached book fields."""

    def test_abs_api_exception_falls_back_to_cached_author(self):
        book = _make_book(author='Fallback Author', duration=3700)
        abs_service = _make_abs_service()
        abs_service.get_item_details.side_effect = ConnectionError("ABS unreachable")
        db = _make_db_service()
        container = _make_container()

        result = build_book_metadata(book, container, db, abs_service)

        assert result['author'] == 'Fallback Author'

    def test_abs_api_exception_falls_back_to_cached_subtitle(self):
        book = _make_book(subtitle='A Subtitle')
        abs_service = _make_abs_service()
        abs_service.get_item_details.side_effect = RuntimeError("timeout")
        db = _make_db_service()
        container = _make_container()

        result = build_book_metadata(book, container, db, abs_service)

        assert result['subtitle'] == 'A Subtitle'

    def test_abs_api_exception_falls_back_to_cached_duration(self):
        book = _make_book(duration=5400)  # 1h 30m
        abs_service = _make_abs_service()
        abs_service.get_item_details.side_effect = Exception("fail")
        db = _make_db_service()
        container = _make_container()

        result = build_book_metadata(book, container, db, abs_service)

        assert result['duration'] == '1h 30m'

    def test_abs_returns_none_falls_back_to_cached(self):
        book = _make_book(author='Cached Author')
        abs_service = _make_abs_service(item_details=None)
        db = _make_db_service()
        container = _make_container()

        result = build_book_metadata(book, container, db, abs_service)

        assert result['author'] == 'Cached Author'


# ---------------------------------------------------------------------------
# Booklore API failure
# ---------------------------------------------------------------------------

class TestBookloreFailure:
    """When Booklore client raises, the metadata build should still succeed."""

    def test_booklore_exception_does_not_crash(self):
        book = _make_book(ebook_filename='test.epub')
        abs_service = _make_abs_service(item_details=None)
        db = _make_db_service()

        bl_client = Mock()
        bl_client.is_configured.return_value = True
        bl_client.find_book_by_filename.side_effect = ConnectionError("Booklore down")

        result = build_book_metadata(book, Mock(), db, abs_service, booklore_client=bl_client)

        assert 'booklore_url' not in result

    def test_booklore_not_configured_skips_lookup(self):
        book = _make_book(ebook_filename='test.epub')
        abs_service = _make_abs_service(item_details=None)
        db = _make_db_service()

        bl_client = Mock()
        bl_client.is_configured.return_value = False

        result = build_book_metadata(book, Mock(), db, abs_service, booklore_client=bl_client)

        bl_client.find_book_by_filename.assert_not_called()
        assert 'booklore_url' not in result


# ---------------------------------------------------------------------------
# Hardcover metadata failure
# ---------------------------------------------------------------------------

class TestHardcoverFailure:
    """When hardcover_client.get_book_metadata raises, metadata build still succeeds."""

    def test_hardcover_metadata_exception_does_not_crash(self):
        book = _make_book(sync_mode='ebook_only')
        abs_service = _make_abs_service(item_details=None)

        hardcover = Mock()
        hardcover.isbn = '1234567890'
        hardcover.asin = None
        hardcover.hardcover_pages = 300
        hardcover.hardcover_slug = 'test-book'
        hardcover.hardcover_book_id = '42'
        hardcover.hardcover_status_id = None
        hardcover.matched_by = 'manual'

        db = _make_db_service(hardcover=hardcover)

        container = _make_container(hc_configured=True)
        hc_client = container.hardcover_client()
        hc_client.get_book_metadata.side_effect = TimeoutError("HC API timeout")

        result = build_book_metadata(book, container, db, abs_service)

        # Hardcover DB details should still be present
        assert result['isbn'] == '1234567890'
        assert result['pages'] == 300
        # But HC API metadata (description, tags) should be absent
        assert 'hc_tags' not in result


# ---------------------------------------------------------------------------
# Caching / fallback behavior
# ---------------------------------------------------------------------------

class TestCachingBehavior:
    """Verify that cached book fields are used when API data is missing."""

    def test_ebook_only_skips_abs_call(self):
        book = _make_book(sync_mode='ebook_only', author='Ebook Author')
        abs_service = _make_abs_service()
        db = _make_db_service()
        container = _make_container()

        result = build_book_metadata(book, container, db, abs_service)

        abs_service.get_item_details.assert_not_called()
        assert result['author'] == 'Ebook Author'

    def test_abs_author_overrides_cached(self):
        """When ABS returns metadata, it takes priority over cached fields."""
        book = _make_book(author='Old Author')
        abs_item = {
            'media': {
                'metadata': {
                    'authorName': 'ABS Author',
                    'narratorName': 'Narrator',
                    'subtitle': '',
                    'description': 'A book',
                    'genres': ['Fiction'],
                },
                'duration': 3661,
            }
        }
        abs_service = _make_abs_service(item_details=abs_item)
        db = _make_db_service()
        container = _make_container()

        result = build_book_metadata(book, container, db, abs_service)

        assert result['author'] == 'ABS Author'
        assert result['duration'] == '1h 1m'

    def test_duration_minutes_only_when_under_one_hour(self):
        book = _make_book(duration=1800, sync_mode='ebook_only')  # 30 minutes
        abs_service = _make_abs_service()
        db = _make_db_service()
        container = _make_container()

        result = build_book_metadata(book, container, db, abs_service)

        assert result['duration'] == '30m'

    def test_no_ebook_filename_skips_booklore(self):
        book = _make_book(ebook_filename=None)
        abs_service = _make_abs_service(item_details=None)
        db = _make_db_service()

        bl_client = Mock()
        container = _make_container()

        result = build_book_metadata(book, container, db, abs_service, booklore_client=bl_client)

        bl_client.find_book_by_filename.assert_not_called()
