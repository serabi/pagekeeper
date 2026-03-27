"""Tests for error paths in reading blueprint (src/blueprints/reading_bp.py)."""

from unittest.mock import Mock


def _make_mock_book(**overrides):
    """Create a mock Book object with reading-relevant fields."""
    defaults = {
        "id": 1,
        "abs_id": "test-abs",
        "title": "Test Book",
        "author": "Author",
        "ebook_filename": "test.epub",
        "original_ebook_filename": None,
        "kosync_doc_id": "hash123",
        "status": "active",
        "sync_mode": "audiobook",
        "duration": 3600,
        "started_at": "2026-01-01",
        "finished_at": None,
        "rating": None,
        "read_count": 1,
        "subtitle": None,
        "storyteller_uuid": None,
        "custom_cover_url": None,
        "abs_ebook_item_id": None,
        "ebook_item_id": None,
    }
    defaults.update(overrides)
    book = Mock()
    for k, v in defaults.items():
        setattr(book, k, v)
    return book


def _setup_reading_db_defaults(mock_db, book=None):
    """Configure mock DB with defaults for reading routes."""
    mock_db.get_all_books.return_value = [book] if book else []
    mock_db.get_all_states.return_value = []
    mock_db.get_states_by_book.return_value = {}
    mock_db.get_states_for_book.return_value = []
    mock_db.get_booklore_by_filename.return_value = {}
    mock_db.get_all_hardcover_details.return_value = []
    mock_db.get_hardcover_details.return_value = None
    mock_db.get_reading_goal.return_value = None
    mock_db.get_reading_journals.return_value = []
    mock_db.get_bookfusion_highlights_for_book_by_book_id.return_value = []
    mock_db.is_bookfusion_linked_by_book_id.return_value = False
    mock_db.find_tbr_by_book_id.return_value = None
    mock_db.get_tbr_count.return_value = 0
    mock_db.get_tbr_items.return_value = []
    mock_db.get_book_by_ref.return_value = book


# ── Reading index: ABS metadata fetch fails ───────────────────────

def test_reading_index_renders_when_abs_metadata_fails(flask_app, mock_container):
    """Reading page should render 200 even when ABS get_audiobooks raises."""
    book = _make_mock_book()
    _setup_reading_db_defaults(mock_container.mock_database_service, book)

    failing_abs = Mock()
    failing_abs.get_audiobooks.side_effect = Exception("ABS unavailable")
    failing_abs.is_available.return_value = False
    failing_abs.get_cover_proxy_url.return_value = None
    flask_app.config['abs_service'] = failing_abs

    with flask_app.test_client() as client:
        response = client.get("/reading")

    assert response.status_code == 200


def test_reading_index_renders_when_hardcover_check_fails(flask_app, mock_container):
    """Reading page should render when Hardcover is_configured raises."""
    _setup_reading_db_defaults(mock_container.mock_database_service)
    mock_container.mock_hardcover_client.is_configured.side_effect = Exception("HC error")

    with flask_app.test_client() as client:
        response = client.get("/reading")

    assert response.status_code == 200


def test_reading_index_renders_when_tbr_items_fail(flask_app, mock_container):
    """Reading page should render when TBR items query fails."""
    _setup_reading_db_defaults(mock_container.mock_database_service)
    mock_container.mock_database_service.get_tbr_items.side_effect = Exception("TBR DB error")

    with flask_app.test_client() as client:
        response = client.get("/reading")

    assert response.status_code == 200


# ── Rating: Hardcover sync fails but local save succeeds ──────────

def test_update_rating_hardcover_sync_fails_local_succeeds(flask_app, mock_container):
    """Rating update should succeed locally even when Hardcover push throws."""
    book = _make_mock_book(rating=4.0)
    mock_container.mock_database_service.get_book_by_ref.return_value = book

    updated_book = _make_mock_book(rating=4.0)
    mock_container.mock_database_service.update_book_reading_fields.return_value = updated_book

    mock_container.mock_hardcover_service.is_configured.return_value = True
    mock_container.mock_hardcover_service.push_local_rating.side_effect = Exception("HC push failed")

    with flask_app.test_client() as client:
        response = client.post(
            "/api/reading/book/test-abs/rating",
            json={"rating": 4.0},
        )

    data = response.get_json()
    assert response.status_code == 200
    assert data["success"] is True
    assert data["rating"] == 4.0
    assert data["hardcover_synced"] is False
    assert data["hardcover_error"] == "HC push failed"


def test_update_rating_hardcover_not_configured(flask_app, mock_container):
    """Rating update should succeed when Hardcover is not configured."""
    book = _make_mock_book(rating=3.5)
    mock_container.mock_database_service.get_book_by_ref.return_value = book

    updated_book = _make_mock_book(rating=3.5)
    mock_container.mock_database_service.update_book_reading_fields.return_value = updated_book

    mock_container.mock_hardcover_service.is_configured.return_value = False

    with flask_app.test_client() as client:
        response = client.post(
            "/api/reading/book/test-abs/rating",
            json={"rating": 3.5},
        )

    data = response.get_json()
    assert response.status_code == 200
    assert data["success"] is True
    assert data["hardcover_synced"] is False
    assert data["hardcover_error"] is None


def test_update_rating_invalid_value_returns_400(flask_app, mock_container):
    """Rating update with non-numeric value returns 400."""
    book = _make_mock_book()
    mock_container.mock_database_service.get_book_by_ref.return_value = book

    with flask_app.test_client() as client:
        response = client.post(
            "/api/reading/book/test-abs/rating",
            json={"rating": "not-a-number"},
        )

    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False


def test_update_rating_out_of_range_returns_400(flask_app, mock_container):
    """Rating outside 0-5 returns 400."""
    book = _make_mock_book()
    mock_container.mock_database_service.get_book_by_ref.return_value = book

    with flask_app.test_client() as client:
        response = client.post(
            "/api/reading/book/test-abs/rating",
            json={"rating": 6.0},
        )

    assert response.status_code == 400


# ── Reading detail: alignment info failure ────────────────────────

def test_reading_detail_alignment_failure_swallowed(flask_app, mock_container):
    """Reading detail page should render when alignment_service raises."""
    book = _make_mock_book(sync_mode="audiobook")
    mock_container.mock_database_service.get_book_by_ref.return_value = book
    mock_container.mock_database_service.get_hardcover_details.return_value = None
    mock_container.mock_database_service.get_reading_journals.return_value = []
    mock_container.mock_database_service.get_bookfusion_highlights_for_book_by_book_id.return_value = []
    mock_container.mock_database_service.is_bookfusion_linked_by_book_id.return_value = False
    mock_container.mock_database_service.get_states_by_book.return_value = {}
    mock_container.mock_database_service.get_booklore_by_filename.return_value = {}
    mock_container.mock_database_service.find_tbr_by_book_id.return_value = None

    failing_alignment = Mock()
    failing_alignment.get_alignment_info.side_effect = Exception("Alignment DB error")
    mock_container.alignment_service = lambda: failing_alignment

    with flask_app.test_client() as client:
        response = client.get("/reading/book/test-abs")

    assert response.status_code == 200


# ── TBR detail: HC check failure ──────────────────────────────────

def test_tbr_detail_hardcover_check_fails(flask_app, mock_container):
    """TBR detail page renders when Hardcover is_configured raises."""
    tbr_item = Mock()
    tbr_item.id = 1
    tbr_item.title = "TBR Book"
    tbr_item.author = "Author"
    tbr_item.genres = None
    tbr_item.book_id = None
    tbr_item.book_abs_id = None
    tbr_item.cover_url = None
    tbr_item.hardcover_book_id = None
    tbr_item.notes = None
    tbr_item.priority = 0
    tbr_item.source = "manual"
    tbr_item.created_at = None
    tbr_item.rating = None
    tbr_item.page_count = None
    tbr_item.release_year = None
    tbr_item.added_at = None
    tbr_item.description = None
    tbr_item.status = "want_to_read"

    mock_container.mock_database_service.get_tbr_item.return_value = tbr_item
    mock_container.mock_hardcover_client.is_configured.side_effect = Exception("HC config error")

    with flask_app.test_client() as client:
        response = client.get("/reading/tbr/1")

    assert response.status_code == 200
