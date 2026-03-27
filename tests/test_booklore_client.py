
import json
import os

# Adjust path to import src
import sys
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.api.booklore_client import BookloreClient
from src.db.models import BookloreBook


@pytest.fixture
def mock_db():
    return MagicMock()

@pytest.fixture
def booklore_client(mock_db):
    with patch.dict(os.environ, {
        "BOOKLORE_SERVER": "http://mock-booklore",
        "BOOKLORE_USER": "testuser",
        "BOOKLORE_PASSWORD": "testpass",
        "DATA_DIR": "/tmp/data"
    }):
        client = BookloreClient(database_service=mock_db)
        return client

def test_init_loads_from_db(mock_db):
    # Setup mock DB return
    mock_book = MagicMock()
    mock_book.filename = "test_book.epub"
    mock_book.title = "Test Book"
    mock_book.authors = "Test Author"
    mock_book.raw_metadata_dict = {
        "id": "123",
        "fileName": "test_book.epub",
        "title": "Test Book",
        "authors": "Test Author"
    }

    mock_db.get_all_booklore_books.return_value = [mock_book]

    with patch.dict(os.environ, {"BOOKLORE_SERVER": "http://mock", "BOOKLORE_USER": "u", "BOOKLORE_PASSWORD": "p", "DATA_DIR": "/tmp/data"}):
        client = BookloreClient(database_service=mock_db)

        assert "test_book.epub" in client._book_cache
        assert client._book_cache["test_book.epub"]["id"] == "123"
        assert client._book_id_cache["123"]["title"] == "Test Book"

def test_migration_from_legacy_json(mock_db):
    # Setup: DB is empty, Legacy JSON exists
    mock_db.get_all_booklore_books.side_effect = [[], []] # First call empty, second call empty

    legacy_data = {
        "books": {
            "legacy.epub": {
                "id": "999",
                "title": "Legacy Book",
                "authors": "Old Author"
            }
        }
    }

    # Mock open AND json.load to ensure data is returned correctly
    with patch("builtins.open", mock_open(read_data=json.dumps(legacy_data))):
         # Need to ensure json.load reads from the mock
         with patch("json.load", return_value=legacy_data):
            with patch.object(Path, "exists", return_value=True):
                 with patch.object(Path, "rename") as mock_rename:
                    with patch.dict(os.environ, {"BOOKLORE_SERVER": "http://mock", "BOOKLORE_USER": "u", "BOOKLORE_PASSWORD": "p", "DATA_DIR": "/tmp/data"}):
                        BookloreClient(database_service=mock_db)

                        # Verification
                        mock_db.save_booklore_book.assert_called_once()
                        call_args = mock_db.save_booklore_book.call_args[0][0]
                        assert isinstance(call_args, BookloreBook)
                        assert call_args.filename == "legacy.epub"
                        assert call_args.title == "Legacy Book"

                        # Verify rename was called
                        mock_rename.assert_called()

def test_save_to_db_on_fetch(mock_db):
    # Setup basic client
    with patch.dict(os.environ, {
        "BOOKLORE_SERVER": "http://mock-booklore",
        "BOOKLORE_USER": "test",
        "BOOKLORE_PASSWORD": "pass",
        "DATA_DIR": "/tmp/data"
    }):
        client = BookloreClient(database_service=mock_db)

        # Mock dependencies
        mock_response = MagicMock()
        mock_response.status_code = 200
        # First call returns list with full Book DTO, second empty to stop loop
        mock_response.json.side_effect = [
            [
                {
                    "id": "new1",
                    "title": "New Book",
                    "primaryFile": {
                        "id": 42,
                        "fileName": "newbook.epub",
                        "bookType": "EPUB"
                    },
                    "metadata": {
                        "authors": ["New Author"]
                    }
                }
            ],
            []
        ]

        # Mock token and request
        client._get_fresh_token = MagicMock(return_value="fake_token")
        client._make_request = MagicMock(side_effect=[mock_response, mock_response])

        client._refresh_book_cache()

        # Verify processing happened
        mock_db.save_booklore_book.assert_called()
        saved_book = mock_db.save_booklore_book.call_args[0][0]
        assert saved_book.filename == "newbook.epub"


def test_extract_progress_epub(booklore_client):
    book_info = {
        'epubProgress': {'percentage': 45.5, 'cfi': 'epubcfi(/6/4)'},
        'pdfProgress': None,
        'cbxProgress': None,
    }
    pct, cfi = booklore_client.extract_progress(book_info)
    assert pct == pytest.approx(0.455)
    assert cfi == 'epubcfi(/6/4)'


def test_extract_progress_pdf(booklore_client):
    book_info = {
        'epubProgress': None,
        'pdfProgress': {'percentage': 30.0},
        'cbxProgress': None,
    }
    pct, cfi = booklore_client.extract_progress(book_info)
    assert pct == pytest.approx(0.30)
    assert cfi is None


def test_extract_progress_none(booklore_client):
    book_info = {
        'epubProgress': None,
        'pdfProgress': None,
        'cbxProgress': None,
    }
    pct, cfi = booklore_client.extract_progress(book_info)
    assert pct is None
    assert cfi is None


def test_extract_progress_zero(booklore_client):
    """percentage=0 should return 0.0, not None."""
    book_info = {
        'epubProgress': {'percentage': 0, 'cfi': None},
        'pdfProgress': None,
        'cbxProgress': None,
    }
    pct, cfi = booklore_client.extract_progress(book_info)
    assert pct == 0.0
    assert cfi is None


def test_update_progress_file_progress(booklore_client):
    """When bookFileId is present, use modern fileProgress payload."""
    from src.sync_clients.sync_client_interface import LocatorResult

    booklore_client._book_cache = {
        'test.epub': {
            'id': 10, 'fileName': 'test.epub', 'bookType': 'EPUB',
            'bookFileId': 42,
            'epubProgress': None, 'pdfProgress': None, 'cbxProgress': None,
        }
    }
    booklore_client._book_id_cache = {10: booklore_client._book_cache['test.epub']}
    booklore_client._cache_timestamp = 9999999999

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    booklore_client._make_request = MagicMock(return_value=mock_resp)

    locator = LocatorResult(percentage=0.5, cfi='epubcfi(/6/4)', href='chapter2.xhtml')
    result = booklore_client.update_progress('test.epub', 0.5, locator)

    assert result is True
    call_args = booklore_client._make_request.call_args
    payload = call_args[0][2]
    assert 'fileProgress' in payload
    assert payload['fileProgress']['bookFileId'] == 42
    assert payload['fileProgress']['progressPercent'] == 50.0
    assert payload['fileProgress']['positionData'] == 'epubcfi(/6/4)'
    assert payload['fileProgress']['positionHref'] == 'chapter2.xhtml'


def test_update_progress_legacy_fallback(booklore_client):
    """When bookFileId is missing, fall back to legacy format."""
    booklore_client._book_cache = {
        'test.epub': {
            'id': 10, 'fileName': 'test.epub', 'bookType': 'EPUB',
            'epubProgress': None, 'pdfProgress': None, 'cbxProgress': None,
        }
    }
    booklore_client._book_id_cache = {10: booklore_client._book_cache['test.epub']}
    booklore_client._cache_timestamp = 9999999999

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    booklore_client._make_request = MagicMock(return_value=mock_resp)

    result = booklore_client.update_progress('test.epub', 0.5)
    assert result is True
    payload = booklore_client._make_request.call_args[0][2]
    assert 'epubProgress' in payload
    assert 'fileProgress' not in payload
    assert payload['epubProgress']['percentage'] == 50.0


def test_update_progress_no_page_field(booklore_client):
    """PDF/CBX legacy payload must not include page field."""
    booklore_client._book_cache = {
        'test.pdf': {
            'id': 20, 'fileName': 'test.pdf', 'bookType': 'PDF',
            'epubProgress': None, 'pdfProgress': None, 'cbxProgress': None,
        }
    }
    booklore_client._book_id_cache = {20: booklore_client._book_cache['test.pdf']}
    booklore_client._cache_timestamp = 9999999999

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    booklore_client._make_request = MagicMock(return_value=mock_resp)

    result = booklore_client.update_progress('test.pdf', 0.75)
    assert result is True
    payload = booklore_client._make_request.call_args[0][2]
    assert 'pdfProgress' in payload
    assert 'page' not in payload['pdfProgress']
    assert payload['pdfProgress']['percentage'] == 75.0


def test_fetch_bulk_state():
    """BookloreSyncClient.fetch_bulk_state keys by lowercase filename."""
    from src.sync_clients.booklore_sync_client import BookloreSyncClient

    mock_client = MagicMock()
    mock_client.is_configured.return_value = True
    mock_client.get_all_books.return_value = [
        {'fileName': 'Book1.epub', 'id': 1, 'epubProgress': {'percentage': 50}},
        {'fileName': 'Book2.PDF', 'id': 2, 'pdfProgress': {'percentage': 30}},
    ]

    sync_client = BookloreSyncClient(booklore_client=mock_client, ebook_parser=MagicMock())
    bulk = sync_client.fetch_bulk_state()

    assert 'book1.epub' in bulk
    assert 'book2.pdf' in bulk
    assert bulk['book1.epub']['id'] == 1
