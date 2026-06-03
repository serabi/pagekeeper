from datetime import datetime
from unittest.mock import Mock


def _make_book(book_id=42, abs_id="test-abs-id"):
    book = Mock()
    book.id = book_id
    book.abs_id = abs_id
    return book


def _make_highlight(quote_text, chapter_heading, highlighted_at=None):
    highlight = Mock()
    highlight.quote_text = quote_text
    highlight.content = quote_text
    highlight.chapter_heading = chapter_heading
    highlight.highlighted_at = highlighted_at
    return highlight


def _make_existing_journal(entry):
    journal = Mock()
    journal.entry = entry
    return journal


def test_save_journal_dedupes_legacy_plain_text_entry(client, mock_container):
    book = _make_book()
    highlight = _make_highlight("Duplicate quote", "## Chapter 1", datetime(2026, 1, 15))
    existing = _make_existing_journal("Duplicate quote\n— Chapter 1")

    mock_db = mock_container.mock_database_service
    mock_db.get_book_by_ref.return_value = book
    mock_db.get_bookfusion_highlights_for_book_by_book_id.return_value = [highlight]
    mock_db.get_reading_journal_entries_for_book.return_value = [existing]

    resp = client.post("/api/bookfusion/save-journal", json={"abs_id": book.abs_id})

    assert resp.status_code == 200
    assert resp.get_json() == {"success": True, "saved": 0, "skipped": 1}
    mock_db.cleanup_bookfusion_import_notes.assert_called_once_with(book.id)
    mock_db.add_reading_journal.assert_not_called()


def test_save_journal_saves_markdown_formatted_chapter(client, mock_container):
    book = _make_book()
    highlight = _make_highlight("Fresh quote", "# Chapter 2", datetime(2026, 2, 1))

    mock_db = mock_container.mock_database_service
    mock_db.get_book_by_ref.return_value = book
    mock_db.get_bookfusion_highlights_for_book_by_book_id.return_value = [highlight]
    mock_db.get_reading_journal_entries_for_book.return_value = []

    resp = client.post("/api/bookfusion/save-journal", json={"abs_id": book.abs_id})

    assert resp.status_code == 200
    assert resp.get_json() == {"success": True, "saved": 1, "skipped": 0}
    mock_db.cleanup_bookfusion_import_notes.assert_called_once_with(book.id)
    mock_db.add_reading_journal.assert_called_once_with(
        book.id,
        "highlight",
        entry="Fresh quote\n— *Chapter 2*",
        created_at=datetime(2026, 2, 1),
        abs_id=book.abs_id,
    )
