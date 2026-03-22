"""Tests for BookFusion blueprint routes."""

from datetime import datetime
from unittest.mock import Mock

from tests.conftest import MockContainer

# ── Helpers ────────────────────────────────────────────────────────

def _make_mock_book(abs_id='test-abs-id', title='Test Book', book_id=1, status='active'):
    """Return a Mock that behaves like a Book ORM instance."""
    book = Mock()
    book.id = book_id
    book.abs_id = abs_id
    book.title = title
    book.status = status
    book.started_at = None
    book.finished_at = None
    book.sync_mode = 'audiobook'
    return book


def _make_bf_book(bookfusion_id='bf-123', title='BF Book', authors='Author',
                  filename='book.epub', highlight_count=3, matched_abs_id=None,
                  matched_book_id=None, hidden=False, series=None, tags=None):
    """Return a Mock that behaves like a BookfusionBook ORM instance."""
    bf = Mock()
    bf.bookfusion_id = bookfusion_id
    bf.title = title
    bf.authors = authors
    bf.filename = filename
    bf.highlight_count = highlight_count
    bf.matched_abs_id = matched_abs_id
    bf.matched_book_id = matched_book_id
    bf.hidden = hidden
    bf.series = series
    bf.tags = tags
    bf.frontmatter = None
    return bf


def _make_bf_highlight(hl_id=1, highlight_id='hl-1', bookfusion_book_id='bf-123',
                       book_title='BF Book', content='Some highlight text',
                       quote_text=None, chapter_heading=None, matched_abs_id=None,
                       highlighted_at=None):
    """Return a Mock that behaves like a BookfusionHighlight ORM instance."""
    hl = Mock()
    hl.id = hl_id
    hl.highlight_id = highlight_id
    hl.bookfusion_book_id = bookfusion_book_id
    hl.book_title = book_title
    hl.content = content
    hl.quote_text = quote_text
    hl.chapter_heading = chapter_heading
    hl.matched_abs_id = matched_abs_id
    hl.highlighted_at = highlighted_at
    return hl


# ── Booklore Books ─────────────────────────────────────────────────

def test_booklore_books_returns_supported_formats(client, mock_container):
    mock_container.mock_booklore_client.is_configured.return_value = True
    mock_container.mock_booklore_client.get_all_books.return_value = [
        {'id': 1, 'title': 'Book One', 'authors': 'Author A', 'fileName': 'book1.epub'},
        {'id': 2, 'title': 'Book Two', 'authors': 'Author B', 'fileName': 'book2.txt'},
        {'id': 3, 'title': 'Book Three', 'authors': 'Author C', 'fileName': 'book3.pdf'},
    ]
    resp = client.get('/api/bookfusion/booklore-books')
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 2
    assert data[0]['title'] == 'Book One'
    assert data[1]['title'] == 'Book Three'


def test_booklore_books_with_search_query(client, mock_container):
    mock_container.mock_booklore_client.is_configured.return_value = True
    mock_container.mock_booklore_client.search_books.return_value = [
        {'id': 5, 'title': 'Searched', 'authors': 'A', 'fileName': 'searched.epub'},
    ]
    resp = client.get('/api/bookfusion/booklore-books?q=searched')
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 1
    mock_container.mock_booklore_client.search_books.assert_called_once_with('searched')


def test_booklore_books_not_configured(client, mock_container):
    mock_container.mock_booklore_client.is_configured.return_value = False
    resp = client.get('/api/bookfusion/booklore-books')
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_booklore_books_exception_returns_empty(client, mock_container):
    mock_container.mock_booklore_client.is_configured.return_value = True
    mock_container.mock_booklore_client.get_all_books.side_effect = Exception('Booklore down')
    resp = client.get('/api/bookfusion/booklore-books')
    assert resp.status_code == 200
    assert resp.get_json() == []


# ── Upload Book ────────────────────────────────────────────────────

def test_upload_book_success(client, mock_container):
    mock_container.mock_bookfusion_client.upload_api_key = 'key-123'
    mock_container.mock_booklore_client.is_configured.return_value = True
    mock_container.mock_booklore_client.download_book.return_value = b'file-bytes'
    mock_container.mock_bookfusion_client.upload_book.return_value = {'id': 'new-bf-id'}

    resp = client.post('/api/bookfusion/upload', json={
        'book_id': 10, 'title': 'My Book', 'authors': 'Auth', 'fileName': 'my.epub',
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True
    assert data['result'] == {'id': 'new-bf-id'}


def test_upload_book_no_data(client, mock_container):
    resp = client.post('/api/bookfusion/upload', content_type='application/json', data='')
    assert resp.status_code == 400


def test_upload_book_missing_book_id(client, mock_container):
    resp = client.post('/api/bookfusion/upload', json={'title': 'Missing ID'})
    assert resp.status_code == 400
    assert 'book_id required' in resp.get_json()['error']


def test_upload_book_no_api_key(client, mock_container):
    mock_container.mock_bookfusion_client.upload_api_key = ''
    resp = client.post('/api/bookfusion/upload', json={'book_id': 1})
    assert resp.status_code == 400
    assert 'API key' in resp.get_json()['error']


def test_upload_book_booklore_not_configured(client, mock_container):
    mock_container.mock_bookfusion_client.upload_api_key = 'key'
    mock_container.mock_booklore_client.is_configured.return_value = False
    resp = client.post('/api/bookfusion/upload', json={'book_id': 1})
    assert resp.status_code == 400
    assert 'Booklore not configured' in resp.get_json()['error']


def test_upload_book_download_fails(client, mock_container):
    mock_container.mock_bookfusion_client.upload_api_key = 'key'
    mock_container.mock_booklore_client.is_configured.return_value = True
    mock_container.mock_booklore_client.download_book.return_value = None
    resp = client.post('/api/bookfusion/upload', json={'book_id': 1})
    assert resp.status_code == 500
    assert 'Failed to download' in resp.get_json()['error']


def test_upload_book_upload_fails(client, mock_container):
    mock_container.mock_bookfusion_client.upload_api_key = 'key'
    mock_container.mock_booklore_client.is_configured.return_value = True
    mock_container.mock_booklore_client.download_book.return_value = b'bytes'
    mock_container.mock_bookfusion_client.upload_book.return_value = None
    resp = client.post('/api/bookfusion/upload', json={
        'book_id': 1, 'fileName': 'x.epub',
    })
    assert resp.status_code == 500
    assert 'Upload to BookFusion failed' in resp.get_json()['error']


# ── Sync Highlights ────────────────────────────────────────────────

def test_sync_highlights_success(client, mock_container):
    mock_container.mock_bookfusion_client.highlights_api_key = 'hlkey'
    mock_container.mock_bookfusion_client.sync_all_highlights.return_value = {
        'new_highlights': 5, 'books_saved': 2, 'new_ids': ['a', 'b'],
    }
    mock_container.mock_database_service.get_unmatched_bookfusion_highlights.return_value = []

    resp = client.post('/api/bookfusion/sync-highlights')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True
    assert data['new_highlights'] == 5
    assert data['books_saved'] == 2
    assert data['auto_matched'] == 0


def test_sync_highlights_no_api_key(client, mock_container):
    mock_container.mock_bookfusion_client.highlights_api_key = ''
    resp = client.post('/api/bookfusion/sync-highlights')
    assert resp.status_code == 400
    assert 'API key' in resp.get_json()['error']


def test_sync_highlights_full_resync_clears_cursor(client, mock_container):
    mock_container.mock_bookfusion_client.highlights_api_key = 'hlkey'
    mock_container.mock_bookfusion_client.sync_all_highlights.return_value = {
        'new_highlights': 0, 'books_saved': 0, 'new_ids': [],
    }
    mock_container.mock_database_service.get_unmatched_bookfusion_highlights.return_value = []

    resp = client.post('/api/bookfusion/sync-highlights', json={'full_resync': True})
    assert resp.status_code == 200
    mock_container.mock_database_service.set_bookfusion_sync_cursor.assert_called_once_with(None)


def test_sync_highlights_exception(client, mock_container):
    mock_container.mock_bookfusion_client.highlights_api_key = 'hlkey'
    mock_container.mock_bookfusion_client.sync_all_highlights.side_effect = Exception('API error')

    resp = client.post('/api/bookfusion/sync-highlights')
    assert resp.status_code == 500
    assert 'failed' in resp.get_json()['error'].lower()


# ── Get Highlights ─────────────────────────────────────────────────

def test_get_highlights_empty(client, mock_container):
    mock_container.mock_database_service.get_bookfusion_highlights.return_value = []
    mock_container.mock_database_service.get_bookfusion_sync_cursor.return_value = None
    mock_container.mock_database_service.get_all_books.return_value = []

    resp = client.get('/api/bookfusion/highlights')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['highlights'] == {}
    assert data['has_synced'] is False


def test_get_highlights_with_data(client, mock_container):
    hl = _make_bf_highlight(
        highlighted_at=datetime(2025, 1, 15, 10, 30, 0),
        quote_text='A great quote',
        matched_abs_id='book-1',
    )
    mock_container.mock_database_service.get_bookfusion_highlights.return_value = [hl]
    mock_container.mock_database_service.get_bookfusion_sync_cursor.return_value = 'cursor-abc'
    mock_container.mock_database_service.get_all_books.return_value = [
        _make_mock_book(abs_id='book-1', title='Dashboard Book'),
    ]

    resp = client.get('/api/bookfusion/highlights')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['has_synced'] is True
    assert len(data['books']) == 1
    # The highlight should be grouped under the book title
    assert len(data['highlights']) == 1


# ── Link Highlight ─────────────────────────────────────────────────

def test_link_highlight_success(client, mock_container):
    book = _make_mock_book()
    mock_container.mock_database_service.get_book_by_ref.return_value = book

    resp = client.post('/api/bookfusion/link-highlight', json={
        'bookfusion_book_id': 'bf-123', 'abs_id': 'test-abs-id',
    })
    assert resp.status_code == 200
    assert resp.get_json()['success'] is True
    mock_container.mock_database_service.link_bookfusion_highlights_by_book_id.assert_called_once_with(
        'bf-123', book.id,
    )


def test_link_highlight_unlink(client, mock_container):
    resp = client.post('/api/bookfusion/link-highlight', json={
        'bookfusion_book_id': 'bf-123', 'abs_id': '',
    })
    assert resp.status_code == 200
    mock_container.mock_database_service.link_bookfusion_highlights_by_book_id.assert_called_once_with(
        'bf-123', None,
    )


def test_link_highlight_no_data(client, mock_container):
    resp = client.post('/api/bookfusion/link-highlight', content_type='application/json', data='')
    assert resp.status_code == 400


def test_link_highlight_missing_bookfusion_id(client, mock_container):
    resp = client.post('/api/bookfusion/link-highlight', json={'abs_id': 'x'})
    assert resp.status_code == 400
    assert 'bookfusion_book_id required' in resp.get_json()['error']


# ── Save Journal ───────────────────────────────────────────────────

def test_save_journal_success(client, mock_container):
    book = _make_mock_book()
    mock_container.mock_database_service.get_book_by_ref.return_value = book
    mock_container.mock_database_service.cleanup_bookfusion_import_notes.return_value = {'deleted': 0}

    resp = client.post('/api/bookfusion/save-journal', json={
        'abs_id': 'test-abs-id',
        'highlights': [
            {'quote': 'Great quote', 'chapter': 'Ch 1', 'highlighted_at': '2025-01-15 10:30:00'},
            {'quote': 'Another quote'},
        ],
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True
    assert data['saved'] == 2


def test_save_journal_no_data(client, mock_container):
    resp = client.post('/api/bookfusion/save-journal', content_type='application/json', data='')
    assert resp.status_code == 400


def test_save_journal_missing_abs_id(client, mock_container):
    resp = client.post('/api/bookfusion/save-journal', json={'highlights': []})
    assert resp.status_code == 400
    assert 'abs_id required' in resp.get_json()['error']


def test_save_journal_book_not_found(client, mock_container):
    mock_container.mock_database_service.get_book_by_ref.return_value = None
    mock_container.mock_database_service.get_bookfusion_highlights_for_book_by_book_id.return_value = []

    resp = client.post('/api/bookfusion/save-journal', json={'abs_id': 'nonexistent'})
    # No highlights provided and none server-side -> error
    assert resp.status_code in (400, 404)


def test_save_journal_skips_empty_quotes(client, mock_container):
    book = _make_mock_book()
    mock_container.mock_database_service.get_book_by_ref.return_value = book
    mock_container.mock_database_service.cleanup_bookfusion_import_notes.return_value = {}

    resp = client.post('/api/bookfusion/save-journal', json={
        'abs_id': 'test-abs-id',
        'highlights': [
            {'quote': '', 'chapter': 'Ch 1'},
            {'quote': '   '},
            {'quote': 'Valid quote'},
        ],
    })
    assert resp.status_code == 200
    assert resp.get_json()['saved'] == 1


def test_save_journal_fetches_server_side_highlights(client, mock_container):
    book = _make_mock_book()
    mock_container.mock_database_service.get_book_by_ref.return_value = book
    mock_container.mock_database_service.cleanup_bookfusion_import_notes.return_value = {}

    hl = _make_bf_highlight(quote_text='Server-side quote', highlighted_at=datetime(2025, 3, 1))
    mock_container.mock_database_service.get_bookfusion_highlights_for_book_by_book_id.return_value = [hl]

    resp = client.post('/api/bookfusion/save-journal', json={'abs_id': 'test-abs-id'})
    assert resp.status_code == 200
    assert resp.get_json()['saved'] == 1


# ── Library ────────────────────────────────────────────────────────

def test_library_returns_books(client, mock_container):
    bf = _make_bf_book(bookfusion_id='bf-1', title='Library Book')
    mock_container.mock_database_service.get_bookfusion_books.return_value = [bf]
    mock_container.mock_database_service.get_all_books.return_value = []

    resp = client.get('/api/bookfusion/library')
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data['books']) == 1
    assert data['books'][0]['title'] == 'Library Book'
    assert data['books'][0]['on_dashboard'] is False


def test_library_marks_on_dashboard(client, mock_container):
    bf = _make_bf_book(bookfusion_id='bf-1', matched_abs_id='dash-1')
    dashboard_book = _make_mock_book(abs_id='dash-1', title='Dashboard Book')
    mock_container.mock_database_service.get_bookfusion_books.return_value = [bf]
    mock_container.mock_database_service.get_all_books.return_value = [dashboard_book]

    resp = client.get('/api/bookfusion/library')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['books'][0]['on_dashboard'] is True
    assert data['books'][0]['abs_id'] == 'dash-1'


def test_library_merges_duplicate_titles(client, mock_container):
    bf1 = _make_bf_book(bookfusion_id='bf-1', title='Same Title', filename='book.epub', highlight_count=5)
    bf2 = _make_bf_book(bookfusion_id='bf-2', title='Same Title', filename='book.mobi', highlight_count=2)
    mock_container.mock_database_service.get_bookfusion_books.return_value = [bf1, bf2]
    mock_container.mock_database_service.get_all_books.return_value = []

    resp = client.get('/api/bookfusion/library')
    assert resp.status_code == 200
    data = resp.get_json()
    # Duplicate titles should be merged into a single entry
    assert len(data['books']) == 1
    assert data['books'][0]['highlight_count'] == 7
    assert len(data['books'][0]['bookfusion_ids']) == 2


def test_library_hidden_books(client, mock_container):
    bf = _make_bf_book(bookfusion_id='bf-1', title='Hidden Book', hidden=True)
    mock_container.mock_database_service.get_bookfusion_books.return_value = [bf]
    mock_container.mock_database_service.get_all_books.return_value = []

    resp = client.get('/api/bookfusion/library')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data is not None
    assert data['books'][0]['hidden'] is True


def test_library_empty(client, mock_container):
    mock_container.mock_database_service.get_bookfusion_books.return_value = []
    mock_container.mock_database_service.get_all_books.return_value = []

    resp = client.get('/api/bookfusion/library')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['books'] == []


# ── Add to Dashboard ───────────────────────────────────────────────

def test_add_to_dashboard_success(client, mock_container):
    bf = _make_bf_book(bookfusion_id='bf-new', title='New Book')
    mock_container.mock_database_service.get_bookfusion_book.return_value = bf

    saved_book = _make_mock_book(abs_id='bf-bf-new', title='New Book', book_id=42)
    saved_book.started_at = None
    saved_book.finished_at = None
    # First call: not yet on dashboard (None), second: after save, third: _estimate_reading_dates
    mock_container.mock_database_service.get_book_by_ref.side_effect = [None, saved_book, saved_book]
    mock_container.mock_database_service.get_hardcover_details.return_value = None
    mock_container.mock_database_service.get_bookfusion_highlight_date_range.return_value = None
    mock_container.mock_hardcover_client.is_configured.return_value = False

    resp = client.post('/api/bookfusion/add-to-dashboard', json={
        'bookfusion_ids': ['bf-new'],
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True
    assert data['abs_id'] == 'bf-bf-new'


def test_add_to_dashboard_already_exists(client, mock_container):
    existing = _make_mock_book(abs_id='bf-bf-1', title='Already There')
    mock_container.mock_database_service.get_bookfusion_book.return_value = _make_bf_book()
    mock_container.mock_database_service.get_book_by_ref.return_value = existing

    resp = client.post('/api/bookfusion/add-to-dashboard', json={
        'bookfusion_ids': ['bf-1'],
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['already_existed'] is True


def test_add_to_dashboard_no_data(client, mock_container):
    resp = client.post('/api/bookfusion/add-to-dashboard', content_type='application/json', data='')
    assert resp.status_code == 400


def test_add_to_dashboard_missing_id(client, mock_container):
    # Empty dict is falsy in Python, so this gets "No data provided"
    resp = client.post('/api/bookfusion/add-to-dashboard', json={})
    assert resp.status_code == 400

    # With a key but no bookfusion_ids, we get "bookfusion_id required"
    resp = client.post('/api/bookfusion/add-to-dashboard', json={'foo': 'bar'})
    assert resp.status_code == 400
    assert 'bookfusion_id required' in resp.get_json()['error']


def test_add_to_dashboard_book_not_in_catalog(client, mock_container):
    mock_container.mock_database_service.get_bookfusion_book.return_value = None
    resp = client.post('/api/bookfusion/add-to-dashboard', json={
        'bookfusion_id': 'nonexistent',
    })
    assert resp.status_code == 404
    assert 'not found' in resp.get_json()['error'].lower()


def test_add_to_dashboard_single_id_fallback(client, mock_container):
    """When bookfusion_ids is absent, falls back to bookfusion_id."""
    bf = _make_bf_book(bookfusion_id='bf-single', title='Single')
    mock_container.mock_database_service.get_bookfusion_book.return_value = bf
    existing = _make_mock_book(abs_id='bf-bf-single')
    mock_container.mock_database_service.get_book_by_ref.return_value = existing

    resp = client.post('/api/bookfusion/add-to-dashboard', json={
        'bookfusion_id': 'bf-single',
    })
    assert resp.status_code == 200


# ── Match to Book ──────────────────────────────────────────────────

def test_match_to_book_link(client, mock_container):
    book = _make_mock_book(abs_id='dash-1', title='Dashboard Book')
    book.started_at = None
    book.finished_at = None
    mock_container.mock_database_service.get_book_by_ref.return_value = book
    mock_container.mock_database_service.get_hardcover_details.return_value = None
    mock_container.mock_database_service.get_bookfusion_highlight_date_range.return_value = None
    mock_container.mock_hardcover_client.is_configured.return_value = False

    resp = client.post('/api/bookfusion/match-to-book', json={
        'bookfusion_ids': ['bf-1', 'bf-2'], 'abs_id': 'dash-1',
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True
    assert data['abs_id'] == 'dash-1'
    assert mock_container.mock_database_service.set_bookfusion_book_match_by_book_id.call_count == 2


def test_match_to_book_unlink(client, mock_container):
    resp = client.post('/api/bookfusion/match-to-book', json={
        'bookfusion_ids': ['bf-1'],
    })
    assert resp.status_code == 200
    mock_container.mock_database_service.set_bookfusion_book_match_by_book_id.assert_called_once_with(
        'bf-1', None,
    )


def test_match_to_book_no_data(client, mock_container):
    resp = client.post('/api/bookfusion/match-to-book', content_type='application/json', data='')
    assert resp.status_code == 400


def test_match_to_book_missing_bf_id(client, mock_container):
    resp = client.post('/api/bookfusion/match-to-book', json={'abs_id': 'x'})
    assert resp.status_code == 400
    assert 'bookfusion_id required' in resp.get_json()['error']


def test_match_to_book_book_not_found(client, mock_container):
    mock_container.mock_database_service.get_book_by_ref.return_value = None
    resp = client.post('/api/bookfusion/match-to-book', json={
        'bookfusion_ids': ['bf-1'], 'abs_id': 'nonexistent',
    })
    assert resp.status_code == 404
    assert 'not found' in resp.get_json()['error'].lower()


def test_match_to_book_single_id_fallback(client, mock_container):
    resp = client.post('/api/bookfusion/match-to-book', json={
        'bookfusion_id': 'bf-single',
    })
    assert resp.status_code == 200
    mock_container.mock_database_service.set_bookfusion_book_match_by_book_id.assert_called_once()


# ── Hide / Unhide ──────────────────────────────────────────────────

def test_hide_book_success(client, mock_container):
    resp = client.post('/api/bookfusion/hide', json={
        'bookfusion_ids': ['bf-1', 'bf-2'], 'hidden': True,
    })
    assert resp.status_code == 200
    assert resp.get_json()['success'] is True
    mock_container.mock_database_service.set_bookfusion_books_hidden.assert_called_once_with(
        ['bf-1', 'bf-2'], True,
    )


def test_unhide_book(client, mock_container):
    resp = client.post('/api/bookfusion/hide', json={
        'bookfusion_ids': ['bf-1'], 'hidden': False,
    })
    assert resp.status_code == 200
    mock_container.mock_database_service.set_bookfusion_books_hidden.assert_called_once_with(
        ['bf-1'], False,
    )


def test_hide_book_no_data(client, mock_container):
    resp = client.post('/api/bookfusion/hide', content_type='application/json', data='')
    assert resp.status_code == 400


def test_hide_book_missing_id(client, mock_container):
    resp = client.post('/api/bookfusion/hide', json={'hidden': True})
    assert resp.status_code == 400
    assert 'bookfusion_id required' in resp.get_json()['error']


def test_hide_book_single_id_fallback(client, mock_container):
    resp = client.post('/api/bookfusion/hide', json={
        'bookfusion_id': 'bf-single', 'hidden': True,
    })
    assert resp.status_code == 200
    mock_container.mock_database_service.set_bookfusion_books_hidden.assert_called_once_with(
        ['bf-single'], True,
    )


# ── Unlink Book ────────────────────────────────────────────────────

def test_unlink_book_success(client, mock_container):
    book = _make_mock_book(book_id=7)
    mock_container.mock_database_service.get_book_by_ref.return_value = book

    resp = client.post('/api/bookfusion/unlink', json={'abs_id': 'test-abs-id'})
    assert resp.status_code == 200
    assert resp.get_json()['success'] is True
    mock_container.mock_database_service.unlink_bookfusion_by_book_id.assert_called_once_with(7)


def test_unlink_book_not_found_still_succeeds(client, mock_container):
    mock_container.mock_database_service.get_book_by_ref.return_value = None
    resp = client.post('/api/bookfusion/unlink', json={'abs_id': 'missing'})
    assert resp.status_code == 200
    assert resp.get_json()['success'] is True
    mock_container.mock_database_service.unlink_bookfusion_by_book_id.assert_not_called()


def test_unlink_book_no_data(client, mock_container):
    resp = client.post('/api/bookfusion/unlink', content_type='application/json', data='')
    assert resp.status_code == 400


def test_unlink_book_missing_abs_id(client, mock_container):
    # Empty dict is falsy → "No data provided"
    resp = client.post('/api/bookfusion/unlink', json={})
    assert resp.status_code == 400

    # With a key but no abs_id → "abs_id required"
    resp = client.post('/api/bookfusion/unlink', json={'foo': 'bar'})
    assert resp.status_code == 400
    assert 'abs_id required' in resp.get_json()['error']


# ── BookFusion Page ────────────────────────────────────────────────

def test_bookfusion_page_renders(client, mock_container):
    resp = client.get('/bookfusion')
    assert resp.status_code == 200
