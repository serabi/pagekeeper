"""Tests for BookFusion blueprint routes."""

from datetime import datetime
from unittest.mock import Mock, patch


def _make_mock_book(
    abs_id="test-abs-id",
    title="Test Book",
    book_id=1,
    status="active",
    ebook_filename="book.epub",
    original_ebook_filename=None,
    author="Test Author",
):
    book = Mock()
    book.id = book_id
    book.abs_id = abs_id
    book.title = title
    book.status = status
    book.started_at = None
    book.finished_at = None
    book.sync_mode = "audiobook"
    book.ebook_filename = ebook_filename
    book.original_ebook_filename = original_ebook_filename
    book.author = author
    return book


def _make_bf_book(
    bookfusion_id="bf-123",
    title="BF Book",
    authors="Author",
    filename="book.epub",
    highlight_count=3,
    matched_book_id=None,
):
    bf = Mock()
    bf.bookfusion_id = bookfusion_id
    bf.title = title
    bf.authors = authors
    bf.filename = filename
    bf.highlight_count = highlight_count
    bf.matched_book_id = matched_book_id
    return bf


def _make_bf_highlight(
    bookfusion_book_id="bf-123",
    book_title="BF Book",
    content="Some highlight text",
    quote_text=None,
    chapter_heading=None,
    highlighted_at=None,
):
    hl = Mock()
    hl.bookfusion_book_id = bookfusion_book_id
    hl.book_title = book_title
    hl.content = content
    hl.quote_text = quote_text
    hl.chapter_heading = chapter_heading
    hl.highlighted_at = highlighted_at
    return hl


def test_upload_requires_data(client):
    resp = client.post("/api/bookfusion/upload", json={})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "No data provided"


def test_upload_book_not_found(client, mock_container):
    mock_container.mock_bookfusion_client.upload_api_key = "test-key"
    mock_container.mock_database_service.get_book_by_ref.return_value = None

    resp = client.post("/api/bookfusion/upload", json={"abs_id": "nonexistent"})

    assert resp.status_code == 404
    assert resp.get_json()["error"] == "Book not found"


def test_upload_requires_ebook_file(client, mock_container):
    mock_container.mock_bookfusion_client.upload_api_key = "test-key"
    mock_container.mock_database_service.get_book_by_ref.return_value = _make_mock_book(
        ebook_filename=None,
        original_ebook_filename=None,
    )

    resp = client.post("/api/bookfusion/upload", json={"abs_id": "test-abs-id"})

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "No ebook file associated with this book"


def test_upload_requires_upload_api_key(client, mock_container):
    mock_container.mock_bookfusion_client.upload_api_key = None

    resp = client.post("/api/bookfusion/upload", json={"abs_id": "test-abs-id"})

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "BookFusion upload API key not configured"


def test_upload_success_when_local_epub_missing(client, mock_container):
    mock_container.mock_bookfusion_client.upload_api_key = "test-key"
    mock_container.mock_database_service.get_book_by_ref.return_value = _make_mock_book(ebook_filename="test.epub")

    with patch("src.utils.epub_resolver.get_local_epub", return_value=None):
        resp = client.post("/api/bookfusion/upload", json={"abs_id": "test-abs-id"})

    assert resp.status_code == 500
    assert resp.get_json()["error"] == "Could not locate ebook file"


def test_upload_saves_bookfusion_link(client, mock_container, tmp_path):
    book = _make_mock_book(ebook_filename="test.epub", book_id=42)
    mock_container.mock_bookfusion_client.upload_api_key = "test-key"
    mock_container.mock_bookfusion_client.upload_book.return_value = {"id": "bf-123", "title": "Test Book"}
    mock_container.mock_database_service.get_book_by_ref.return_value = book
    mock_container.mock_database_service.get_bookfusion_book_by_book_id.return_value = None

    test_file = tmp_path / "test.epub"
    test_file.write_bytes(b"fake epub content")

    with patch("src.utils.epub_resolver.get_local_epub", return_value=test_file):
        resp = client.post("/api/bookfusion/upload", json={"abs_id": "test-abs-id"})

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["already_linked"] is False
    mock_container.mock_database_service.save_bookfusion_book.assert_called_once()


def test_sync_highlights_requires_api_key(client, mock_container):
    mock_container.mock_bookfusion_client.highlights_api_key = None

    resp = client.post("/api/bookfusion/sync-highlights")

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "BookFusion highlights API key not configured"


def test_sync_highlights_success(client, mock_container):
    mock_container.mock_bookfusion_client.highlights_api_key = "test-key"
    mock_container.mock_bookfusion_client.sync_all_highlights.return_value = {
        "new_highlights": 5,
        "books_saved": 2,
        "new_ids": ["hl-1", "hl-2"],
    }

    resp = client.post("/api/bookfusion/sync-highlights")

    assert resp.status_code == 200
    assert resp.get_json() == {
        "success": True,
        "new_highlights": 5,
        "books_saved": 2,
        "new_ids": ["hl-1", "hl-2"],
    }


def test_sync_highlights_full_resync(client, mock_container):
    mock_container.mock_bookfusion_client.highlights_api_key = "test-key"
    mock_container.mock_bookfusion_client.sync_all_highlights.return_value = {
        "new_highlights": 10,
        "books_saved": 3,
        "new_ids": [],
    }

    resp = client.post("/api/bookfusion/sync-highlights", json={"full_resync": True})

    assert resp.status_code == 200
    mock_container.mock_database_service.set_bookfusion_sync_cursor.assert_called_once_with(None)


def test_sync_book_requires_data(client):
    resp = client.post("/api/bookfusion/sync-book", json={})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "No data provided"


def test_sync_book_book_not_found(client, mock_container):
    mock_container.mock_database_service.get_book_by_ref.return_value = None

    resp = client.post("/api/bookfusion/sync-book", json={"abs_id": "nonexistent"})

    assert resp.status_code == 404
    assert resp.get_json()["error"] == "Book not found"


def test_sync_book_requires_api_key(client, mock_container):
    book = _make_mock_book()
    mock_container.mock_database_service.get_book_by_ref.return_value = book
    mock_container.mock_bookfusion_client.highlights_api_key = None

    resp = client.post("/api/bookfusion/sync-book", json={"abs_id": book.abs_id})

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "BookFusion highlights API key not configured"


def test_sync_book_not_linked(client, mock_container):
    book = _make_mock_book()
    mock_container.mock_database_service.get_book_by_ref.return_value = book
    mock_container.mock_database_service.get_bookfusion_books_by_book_id.return_value = []
    mock_container.mock_bookfusion_client.highlights_api_key = "test-key"

    resp = client.post("/api/bookfusion/sync-book", json={"abs_id": book.abs_id})

    assert resp.status_code == 404
    assert resp.get_json()["error"] == "BookFusion link not found for this book"


def test_sync_book_success(client, mock_container):
    book = _make_mock_book(book_id=42)
    bf_book = _make_bf_book(bookfusion_id="bf-123", matched_book_id=42)
    mock_container.mock_database_service.get_book_by_ref.return_value = book
    mock_container.mock_database_service.get_bookfusion_books_by_book_id.return_value = [bf_book]
    mock_container.mock_bookfusion_client.highlights_api_key = "test-key"
    mock_container.mock_bookfusion_client.sync_all_highlights.return_value = {
        "new_highlights": 3,
        "books_saved": 1,
    }

    resp = client.post("/api/bookfusion/sync-book", json={"abs_id": book.abs_id})

    assert resp.status_code == 200
    assert resp.get_json() == {
        "success": True,
        "new_highlights": 3,
        "books_saved": 1,
        "linked_books": 1,
    }
    mock_container.mock_database_service.link_bookfusion_highlights_by_book_id.assert_called_once_with("bf-123", 42)


def test_save_journal_requires_data(client):
    resp = client.post("/api/bookfusion/save-journal", json={})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "No data provided"


def test_save_journal_book_not_found(client, mock_container):
    mock_container.mock_database_service.get_book_by_ref.return_value = None

    resp = client.post("/api/bookfusion/save-journal", json={"abs_id": "nonexistent"})

    assert resp.status_code == 404
    assert resp.get_json()["error"] == "Book not found"


def test_save_journal_no_highlights(client, mock_container):
    book = _make_mock_book()
    mock_container.mock_database_service.get_book_by_ref.return_value = book
    mock_container.mock_database_service.get_bookfusion_highlights_for_book_by_book_id.return_value = []

    resp = client.post("/api/bookfusion/save-journal", json={"abs_id": book.abs_id})

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "No highlights found for this book"


def test_save_journal_success(client, mock_container):
    book = _make_mock_book(book_id=42)
    highlight = _make_bf_highlight(
        quote_text="Test quote",
        chapter_heading="Chapter 1",
        highlighted_at=datetime(2026, 1, 15),
    )
    mock_container.mock_database_service.get_book_by_ref.return_value = book
    mock_container.mock_database_service.get_bookfusion_highlights_for_book_by_book_id.return_value = [highlight]
    mock_container.mock_database_service.get_reading_journal_entries_for_book.return_value = []

    resp = client.post("/api/bookfusion/save-journal", json={"abs_id": book.abs_id})

    assert resp.status_code == 200
    assert resp.get_json() == {"success": True, "saved": 1, "skipped": 0}
    mock_container.mock_database_service.cleanup_bookfusion_import_notes.assert_called_once_with(book.id)
    mock_container.mock_database_service.add_reading_journal.assert_called_once_with(
        42,
        "highlight",
        entry="Test quote\n— *Chapter 1*",
        created_at=datetime(2026, 1, 15),
        abs_id=book.abs_id,
    )


def test_save_journal_deduplicates_legacy_entry(client, mock_container):
    book = _make_mock_book(book_id=42)
    highlight = _make_bf_highlight(
        quote_text="Duplicate quote",
        chapter_heading="## Chapter 1",
        highlighted_at=datetime(2026, 1, 15),
    )
    existing_journal = Mock()
    existing_journal.entry = "Duplicate quote\n— Chapter 1"
    mock_container.mock_database_service.get_book_by_ref.return_value = book
    mock_container.mock_database_service.get_bookfusion_highlights_for_book_by_book_id.return_value = [highlight]
    mock_container.mock_database_service.get_reading_journal_entries_for_book.return_value = [existing_journal]

    resp = client.post("/api/bookfusion/save-journal", json={"abs_id": book.abs_id})

    assert resp.status_code == 200
    assert resp.get_json() == {"success": True, "saved": 0, "skipped": 1}
    mock_container.mock_database_service.add_reading_journal.assert_not_called()


def test_save_journal_with_existing_entries(client, mock_container):
    book = _make_mock_book(book_id=42)
    highlight = _make_bf_highlight(
        quote_text="New quote",
        chapter_heading="Chapter 2",
        highlighted_at=datetime(2026, 2, 1),
    )
    existing_journal = Mock()
    existing_journal.entry = "Old quote"
    mock_container.mock_database_service.get_book_by_ref.return_value = book
    mock_container.mock_database_service.get_bookfusion_highlights_for_book_by_book_id.return_value = [highlight]
    mock_container.mock_database_service.get_reading_journal_entries_for_book.return_value = [existing_journal]

    resp = client.post("/api/bookfusion/save-journal", json={"abs_id": book.abs_id})

    assert resp.status_code == 200
    assert resp.get_json() == {"success": True, "saved": 1, "skipped": 0}
    mock_container.mock_database_service.add_reading_journal.assert_called_once_with(
        42,
        "highlight",
        entry="New quote\n— *Chapter 2*",
        created_at=datetime(2026, 2, 1),
        abs_id=book.abs_id,
    )
