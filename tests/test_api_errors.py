"""Tests for error paths in API blueprint (src/blueprints/api.py)."""

from unittest.mock import Mock

import pytest


# ── Suggestion resolve/ignore/hide: DB raises ─────────────────────

def test_hide_suggestion_returns_404_when_not_found(client, mock_container):
    """hide_suggestion returns 404 when DB returns False (not found)."""
    mock_container.mock_database_service.hide_suggestion.return_value = False

    response = client.post("/api/suggestions/nonexistent/hide")

    assert response.status_code == 404
    data = response.get_json()
    assert data["success"] is False
    assert "Not found" in data["error"]


def test_hide_suggestion_succeeds(client, mock_container):
    """hide_suggestion returns 200 on success."""
    mock_container.mock_database_service.hide_suggestion.return_value = True

    response = client.post("/api/suggestions/abc123/hide")

    assert response.status_code == 200
    assert response.get_json()["success"] is True


def test_unhide_suggestion_returns_404_when_not_found(client, mock_container):
    """unhide_suggestion returns 404 when DB returns False."""
    mock_container.mock_database_service.unhide_suggestion.return_value = False

    response = client.post("/api/suggestions/nonexistent/unhide")

    assert response.status_code == 404


def test_ignore_suggestion_returns_404_when_not_found(client, mock_container):
    """ignore_suggestion returns 404 when DB returns False."""
    mock_container.mock_database_service.ignore_suggestion.return_value = False

    response = client.post("/api/suggestions/nonexistent/ignore")

    assert response.status_code == 404
    data = response.get_json()
    assert data["success"] is False


def test_ignore_suggestion_succeeds(client, mock_container):
    """ignore_suggestion returns 200 on success."""
    mock_container.mock_database_service.ignore_suggestion.return_value = True

    response = client.post("/api/suggestions/abc123/ignore")

    assert response.status_code == 200
    assert response.get_json()["success"] is True


def test_hide_suggestion_with_source_param(client, mock_container):
    """hide_suggestion passes source query param to DB service."""
    mock_container.mock_database_service.hide_suggestion.return_value = True

    response = client.post("/api/suggestions/abc/hide?source=kosync")

    assert response.status_code == 200
    mock_container.mock_database_service.hide_suggestion.assert_called_once_with("abc", source="kosync")


# ── Booklore search: client raises ────────────────────────────────

def test_booklore_search_returns_empty_when_not_configured(flask_app, mock_container):
    """Booklore search returns empty list when client is not configured."""
    mock_container.mock_booklore_client.is_configured.return_value = False

    with flask_app.test_client() as client:
        response = client.get("/api/booklore/search?q=test")

    assert response.status_code == 200
    assert response.get_json() == []


def test_booklore_search_returns_empty_when_client_raises(flask_app, mock_container):
    """Booklore search returns empty list when search_books throws."""
    mock_container.mock_booklore_client.is_configured.return_value = True
    mock_container.mock_booklore_client.search_books.side_effect = Exception("Booklore down")

    with flask_app.test_client() as client:
        response = client.get("/api/booklore/search?q=test")

    assert response.status_code == 200
    assert response.get_json() == []


def test_booklore_search_returns_empty_for_empty_query(flask_app, mock_container):
    """Booklore search returns empty list for empty query."""
    with flask_app.test_client() as client:
        response = client.get("/api/booklore/search?q=")

    assert response.status_code == 200
    assert response.get_json() == []


def test_booklore_search_returns_results(flask_app, mock_container):
    """Booklore search returns formatted results on success."""
    mock_container.mock_booklore_client.is_configured.return_value = True
    mock_container.mock_booklore_client.search_books.return_value = [
        {"id": 1, "title": "Dune", "authors": "Frank Herbert", "fileName": "dune.epub"},
    ]

    with flask_app.test_client() as client:
        response = client.get("/api/booklore/search?q=dune")

    assert response.status_code == 200
    data = response.get_json()
    assert len(data) == 1
    assert data[0]["title"] == "Dune"
    assert data[0]["source"] == "Booklore"


# ── Storyteller search: client raises ─────────────────────────────

def test_storyteller_search_raises(flask_app, mock_container):
    """Storyteller search returns 500 when search_books throws (no try/except in route)."""
    mock_container.mock_storyteller_client.is_configured.return_value = True
    mock_container.mock_storyteller_client.search_books.side_effect = Exception("Storyteller down")

    # Disable exception propagation so Flask returns a 500 response
    flask_app.config['TESTING'] = False
    flask_app.config['PROPAGATE_EXCEPTIONS'] = False

    with flask_app.test_client() as client:
        response = client.get("/api/storyteller/search?q=test")

    assert response.status_code == 500


def test_storyteller_search_missing_query(flask_app, mock_container):
    """Storyteller search returns 400 when query param is missing."""
    with flask_app.test_client() as client:
        response = client.get("/api/storyteller/search?q=")

    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False


# ── Suggestion link-bookfusion: edge cases ────────────────────────

def test_link_bookfusion_suggestion_not_found(client, mock_container):
    """link-bookfusion returns 404 when suggestion doesn't exist."""
    mock_container.mock_database_service.get_pending_suggestion.return_value = None

    response = client.post(
        "/api/suggestions/abc/link-bookfusion",
        json={"source": "abs", "match_index": 0},
    )

    assert response.status_code == 404
    data = response.get_json()
    assert data["success"] is False


def test_link_bookfusion_invalid_match_index(client, mock_container):
    """link-bookfusion returns 400 when match_index is out of range."""
    suggestion = Mock()
    suggestion.matches = [{"ebook_filename": "test.epub"}]
    mock_container.mock_database_service.get_pending_suggestion.return_value = suggestion

    response = client.post(
        "/api/suggestions/abc/link-bookfusion",
        json={"source": "abs", "match_index": 5},
    )

    assert response.status_code == 400


def test_link_bookfusion_non_bookfusion_match(client, mock_container):
    """link-bookfusion returns 400 when selected match is not a BookFusion candidate."""
    suggestion = Mock()
    suggestion.matches = [{"ebook_filename": "test.epub", "source_family": "booklore", "bookfusion_ids": []}]
    mock_container.mock_database_service.get_pending_suggestion.return_value = suggestion

    response = client.post(
        "/api/suggestions/abc/link-bookfusion",
        json={"source": "abs", "match_index": 0},
    )

    assert response.status_code == 400


def test_link_bookfusion_non_abs_source(client, mock_container):
    """link-bookfusion returns 400 for non-abs source."""
    response = client.post(
        "/api/suggestions/abc/link-bookfusion",
        json={"source": "kosync"},
    )

    assert response.status_code == 400
    data = response.get_json()
    assert "ABS" in data["error"]


# ── Booklore link: recompute KOSync ──────────────────────────────

def test_booklore_link_unlink(flask_app, mock_container):
    """Booklore link with null filename unlinks the book."""
    book = Mock()
    book.title = "Test"
    book.ebook_filename = "old.epub"
    book.original_ebook_filename = "old.epub"
    book.kosync_doc_id = "hash"
    mock_container.mock_database_service.get_book_by_ref.return_value = book

    with flask_app.test_client() as client:
        response = client.post(
            "/api/booklore/link/test-abs",
            json={"filename": None},
        )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert "unlinked" in data["message"].lower()


# ── Booklore libraries: not configured ────────────────────────────

def test_booklore_libraries_not_configured(flask_app, mock_container):
    """Booklore libraries returns 400 when not configured."""
    mock_container.mock_booklore_client.is_configured.return_value = False

    with flask_app.test_client() as client:
        response = client.get("/api/booklore/libraries")

    assert response.status_code == 400
    data = response.get_json()
    assert data["success"] is False


# ── Clear stale suggestions ───────────────────────────────────────

def test_clear_stale_suggestions(client, mock_container):
    """clear_stale_suggestions returns count of cleared items."""
    mock_container.mock_database_service.clear_stale_suggestions.return_value = 5

    response = client.post("/api/suggestions/clear_stale")

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["count"] == 5
