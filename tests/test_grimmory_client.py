import json
import os

# Adjust path to import src
import sys
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.api.grimmory_client import GrimmoryClient
from src.db.models import GrimmoryBook


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def grimmory_client(mock_db):
    with patch.dict(
        os.environ,
        {
            "GRIMMORY_SERVER": "http://mock-grimmory",
            "GRIMMORY_USER": "testuser",
            "GRIMMORY_PASSWORD": "testpass",
            "DATA_DIR": "/tmp/data",
        },
    ):
        client = GrimmoryClient(database_service=mock_db)
        yield client


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
        "authors": "Test Author",
    }

    mock_db.get_all_grimmory_books.return_value = [mock_book]

    with patch.dict(
        os.environ,
        {"GRIMMORY_SERVER": "http://mock", "GRIMMORY_USER": "u", "GRIMMORY_PASSWORD": "p", "DATA_DIR": "/tmp/data"},
    ):
        client = GrimmoryClient(database_service=mock_db)

        assert "test_book.epub" in client._book_cache
        assert client._book_cache["test_book.epub"]["id"] == "123"
        assert client._book_id_cache["123"]["title"] == "Test Book"


def test_migration_from_legacy_json(mock_db):
    # Setup: DB is empty, Legacy JSON exists
    mock_db.get_all_grimmory_books.side_effect = [[], []]  # First call empty, second call empty

    legacy_data = {"books": {"legacy.epub": {"id": "999", "title": "Legacy Book", "authors": "Old Author"}}}

    # Mock open AND json.load to ensure data is returned correctly
    with patch("builtins.open", mock_open(read_data=json.dumps(legacy_data))):
        # Need to ensure json.load reads from the mock
        with patch("json.load", return_value=legacy_data):
            with patch.object(Path, "exists", return_value=True):
                with patch.object(Path, "rename") as mock_rename:
                    with patch.dict(
                        os.environ,
                        {
                            "GRIMMORY_SERVER": "http://mock",
                            "GRIMMORY_USER": "u",
                            "GRIMMORY_PASSWORD": "p",
                            "DATA_DIR": "/tmp/data",
                        },
                    ):
                        GrimmoryClient(database_service=mock_db)

                        # Verification
                        mock_db.save_grimmory_book.assert_called_once()
                        call_args = mock_db.save_grimmory_book.call_args[0][0]
                        assert isinstance(call_args, GrimmoryBook)
                        assert call_args.filename == "legacy.epub"
                        assert call_args.title == "Legacy Book"

                        # Verify rename was called
                        mock_rename.assert_called()


def test_save_to_db_on_fetch(mock_db):
    # Setup basic client
    with patch.dict(
        os.environ,
        {
            "GRIMMORY_SERVER": "http://mock-grimmory",
            "GRIMMORY_USER": "test",
            "GRIMMORY_PASSWORD": "pass",
            "DATA_DIR": "/tmp/data",
        },
    ):
        client = GrimmoryClient(database_service=mock_db)

        # Mock dependencies
        mock_response = MagicMock()
        mock_response.status_code = 200
        # First call returns list with full Book DTO, second empty to stop loop
        mock_response.json.side_effect = [
            [
                {
                    "id": "new1",
                    "title": "New Book",
                    "primaryFile": {"id": 42, "fileName": "newbook.epub", "bookType": "EPUB"},
                    "metadata": {"authors": ["New Author"]},
                }
            ],
            [],
        ]

        # Mock token and request
        client._get_fresh_token = MagicMock(return_value="fake_token")
        client._make_request = MagicMock(side_effect=[mock_response, mock_response])

        client._refresh_book_cache()

        # Verify processing happened
        mock_db.save_grimmory_book.assert_called()
        saved_book = mock_db.save_grimmory_book.call_args[0][0]
        assert saved_book.filename == "newbook.epub"


def test_extract_progress_epub(grimmory_client):
    book_info = {
        "epubProgress": {"percentage": 45.5, "cfi": "epubcfi(/6/4)"},
        "pdfProgress": None,
        "cbxProgress": None,
    }
    pct, cfi = grimmory_client.extract_progress(book_info)
    assert pct == pytest.approx(0.455)
    assert cfi == "epubcfi(/6/4)"


def test_extract_progress_pdf(grimmory_client):
    book_info = {
        "epubProgress": None,
        "pdfProgress": {"percentage": 30.0},
        "cbxProgress": None,
    }
    pct, cfi = grimmory_client.extract_progress(book_info)
    assert pct == pytest.approx(0.30)
    assert cfi is None


def test_extract_progress_none(grimmory_client):
    book_info = {
        "epubProgress": None,
        "pdfProgress": None,
        "cbxProgress": None,
    }
    pct, cfi = grimmory_client.extract_progress(book_info)
    assert pct is None
    assert cfi is None


def test_extract_progress_zero(grimmory_client):
    """percentage=0 should return 0.0, not None."""
    book_info = {
        "epubProgress": {"percentage": 0, "cfi": None},
        "pdfProgress": None,
        "cbxProgress": None,
    }
    pct, cfi = grimmory_client.extract_progress(book_info)
    assert pct == 0.0
    assert cfi is None


def test_update_progress_file_progress(grimmory_client):
    """When bookFileId is present, use modern fileProgress payload."""
    from src.sync_clients.sync_client_interface import LocatorResult

    grimmory_client._book_cache = {
        "test.epub": {
            "id": 10,
            "fileName": "test.epub",
            "bookType": "EPUB",
            "bookFileId": 42,
            "epubProgress": None,
            "pdfProgress": None,
            "cbxProgress": None,
        }
    }
    grimmory_client._book_id_cache = {10: grimmory_client._book_cache["test.epub"]}
    grimmory_client._cache_timestamp = 9999999999

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    grimmory_client._make_request = MagicMock(return_value=mock_resp)

    locator = LocatorResult(percentage=0.5, cfi="epubcfi(/6/4)", href="chapter2.xhtml")
    result = grimmory_client.update_progress("test.epub", 0.5, locator)

    assert result is True
    call_args = grimmory_client._make_request.call_args
    payload = call_args[0][2]
    assert "fileProgress" in payload
    assert payload["fileProgress"]["bookFileId"] == 42
    assert payload["fileProgress"]["progressPercent"] == 50.0
    assert payload["fileProgress"]["positionData"] == "epubcfi(/6/4)"
    assert payload["fileProgress"]["positionHref"] == "chapter2.xhtml"


def test_update_progress_legacy_fallback(grimmory_client):
    """When bookFileId is missing, fall back to legacy format."""
    grimmory_client._book_cache = {
        "test.epub": {
            "id": 10,
            "fileName": "test.epub",
            "bookType": "EPUB",
            "epubProgress": None,
            "pdfProgress": None,
            "cbxProgress": None,
        }
    }
    grimmory_client._book_id_cache = {10: grimmory_client._book_cache["test.epub"]}
    grimmory_client._cache_timestamp = 9999999999

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    grimmory_client._make_request = MagicMock(return_value=mock_resp)

    result = grimmory_client.update_progress("test.epub", 0.5)
    assert result is True
    payload = grimmory_client._make_request.call_args[0][2]
    assert "epubProgress" in payload
    assert "fileProgress" not in payload
    assert payload["epubProgress"]["percentage"] == 50.0


def test_update_progress_no_page_field(grimmory_client):
    """PDF/CBX legacy payload must not include page field."""
    grimmory_client._book_cache = {
        "test.pdf": {
            "id": 20,
            "fileName": "test.pdf",
            "bookType": "PDF",
            "epubProgress": None,
            "pdfProgress": None,
            "cbxProgress": None,
        }
    }
    grimmory_client._book_id_cache = {20: grimmory_client._book_cache["test.pdf"]}
    grimmory_client._cache_timestamp = 9999999999

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    grimmory_client._make_request = MagicMock(return_value=mock_resp)

    result = grimmory_client.update_progress("test.pdf", 0.75)
    assert result is True
    payload = grimmory_client._make_request.call_args[0][2]
    assert "pdfProgress" in payload
    assert "page" not in payload["pdfProgress"]
    assert payload["pdfProgress"]["percentage"] == 75.0


def test_fetch_bulk_state():
    """GrimmorySyncClient.fetch_bulk_state keys by lowercase filename."""
    from src.sync_clients.grimmory_sync_client import GrimmorySyncClient

    mock_client = MagicMock()
    mock_client.is_configured.return_value = True
    mock_client.get_all_books.return_value = [
        {"fileName": "Book1.epub", "id": 1, "epubProgress": {"percentage": 50}},
        {"fileName": "Book2.PDF", "id": 2, "pdfProgress": {"percentage": 30}},
    ]

    sync_client = GrimmorySyncClient(grimmory_client=mock_client, ebook_parser=MagicMock())
    bulk = sync_client.fetch_bulk_state()

    assert "book1.epub" in bulk
    assert "book2.pdf" in bulk
    assert bulk["book1.epub"]["id"] == 1


# ── ABS -> Grimmory migration write/match methods ──


def test_update_read_status_by_id(grimmory_client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    grimmory_client._make_request = MagicMock(return_value=mock_resp)

    result = grimmory_client.update_read_status_by_id(77, "READ")

    assert result is True
    method, endpoint, payload = grimmory_client._make_request.call_args[0]
    assert method == "POST"
    assert endpoint == "/api/v1/books/status"
    assert payload == {"bookIds": [77], "status": "READ"}


def test_update_read_status_delegates_to_by_id(grimmory_client):
    """The filename-based method resolves the id then delegates."""
    grimmory_client._book_cache = {"test.epub": {"id": 88, "fileName": "test.epub", "bookType": "EPUB"}}
    grimmory_client._book_id_cache = {88: grimmory_client._book_cache["test.epub"]}
    grimmory_client._cache_timestamp = 9999999999
    grimmory_client.update_read_status_by_id = MagicMock(return_value=True)

    result = grimmory_client.update_read_status("test.epub", "READ")

    assert result is True
    grimmory_client.update_read_status_by_id.assert_called_once_with(88, "READ")


def test_set_finished_date(grimmory_client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    grimmory_client._make_request = MagicMock(return_value=mock_resp)

    result = grimmory_client.set_finished_date(12, "EPUB", "2024-03-09")

    assert result is True
    method, endpoint, payload = grimmory_client._make_request.call_args[0]
    assert method == "POST"
    assert endpoint == "/api/v1/books/progress"
    assert payload["bookId"] == 12
    assert payload["dateFinished"] == "2024-03-09"


def test_add_reading_session(grimmory_client):
    mock_resp = MagicMock()
    mock_resp.status_code = 202
    grimmory_client._make_request = MagicMock(return_value=mock_resp)

    result = grimmory_client.add_reading_session(
        5, "AUDIOBOOK", start_time="2024-01-01T10:00:00Z", end_time="2024-01-01T11:00:00Z", duration_seconds=3600
    )

    assert result is True
    method, endpoint, payload = grimmory_client._make_request.call_args[0]
    assert method == "POST"
    assert endpoint == "/api/v1/reading-sessions"
    assert payload["bookId"] == 5
    assert payload["startTime"] == "2024-01-01T10:00:00Z"
    assert payload["endTime"] == "2024-01-01T11:00:00Z"
    assert payload["durationSeconds"] == 3600


def test_add_bookmark_audiobook(grimmory_client):
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    grimmory_client._make_request = MagicMock(return_value=mock_resp)

    result = grimmory_client.add_bookmark(9, position_ms=125000, track_index=2, title="Chapter 3")

    assert result is True
    method, endpoint, payload = grimmory_client._make_request.call_args[0]
    assert method == "POST"
    assert endpoint == "/api/v1/bookmarks"
    assert payload["bookId"] == 9
    assert payload["positionMs"] == 125000
    assert payload["trackIndex"] == 2
    assert payload["title"] == "Chapter 3"


def test_match_book_by_identifiers_isbn_wins(grimmory_client):
    grimmory_client.get_all_books = MagicMock(
        return_value=[
            {"id": 1, "title": "Other", "authors": "Someone", "isbn13": "111"},
            {"id": 2, "title": "Target", "authors": "Author A", "isbn13": "9780000000001", "asin": "B00ASIN"},
        ]
    )

    book, matched_by = grimmory_client.match_book_by_identifiers(
        isbn="9780000000001", asin="B00ASIN", title="Target", author="Author A"
    )

    assert book["id"] == 2
    assert matched_by == "isbn"


def test_match_book_by_identifiers_asin_when_no_isbn(grimmory_client):
    grimmory_client.get_all_books = MagicMock(
        return_value=[{"id": 3, "title": "T", "authors": "A", "asin": "B00XYZ"}]
    )

    book, matched_by = grimmory_client.match_book_by_identifiers(isbn=None, asin="B00XYZ", title="T", author="A")

    assert book["id"] == 3
    assert matched_by == "asin"


def test_match_book_by_identifiers_title_author(grimmory_client):
    grimmory_client.get_all_books = MagicMock(
        return_value=[{"id": 4, "title": "The Great Book", "authors": "Jane Doe"}]
    )

    book, matched_by = grimmory_client.match_book_by_identifiers(
        isbn=None, asin=None, title="The Great Book", author="Jane Doe"
    )

    assert book["id"] == 4
    assert matched_by in ("title_author", "title")


def test_match_book_by_identifiers_no_match(grimmory_client):
    grimmory_client.get_all_books = MagicMock(return_value=[{"id": 5, "title": "Nope", "authors": "Nobody"}])

    book, matched_by = grimmory_client.match_book_by_identifiers(
        isbn="999", asin="X", title="Totally Different", author="Other Person"
    )

    assert book is None
    assert matched_by is None
