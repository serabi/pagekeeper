from datetime import datetime
from unittest.mock import Mock


def _make_book(book_id=42, abs_id="test-abs-id"):
    book = Mock()
    book.id = book_id
    book.abs_id = abs_id
    return book


def _make_journal(journal_id=7, event="note", entry="Line 1\n\nLine 2"):
    journal = Mock()
    journal.id = journal_id
    journal.book_id = 42
    journal.event = event
    journal.entry = entry
    journal.percentage = 0.5
    journal.created_at = datetime(2026, 1, 15)
    return journal


def test_add_journal_returns_rendered_entry_html(client, mock_container):
    book = _make_book()
    journal = _make_journal(entry="A **bold** note")

    mock_db = mock_container.mock_database_service
    mock_db.get_book_by_ref.return_value = book
    mock_db.get_states_for_book.return_value = []
    mock_db.add_reading_journal.return_value = journal

    resp = client.post(
        "/api/reading/book/test-abs-id/journal",
        json={"entry": journal.entry},
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["journal"]["entry"] == journal.entry
    assert data["journal"]["entry_html"] == f"<p>{journal.entry}</p>"


def test_update_note_returns_rendered_entry_html(client, mock_container):
    existing = _make_journal(entry="Old")
    updated = _make_journal(entry="Updated note")

    mock_db = mock_container.mock_database_service
    mock_db.get_reading_journal.side_effect = [existing]
    mock_db.update_reading_journal.return_value = updated

    resp = client.patch(
        "/api/reading/journal/7",
        json={"entry": updated.entry},
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["journal"]["entry"] == updated.entry
    assert data["journal"]["entry_html"] == f"<p>{updated.entry}</p>"
