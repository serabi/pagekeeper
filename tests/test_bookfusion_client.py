"""Unit tests for BookFusion API client internals."""

import base64
import hashlib
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))

from src.api.bookfusion_client import (
    BookFusionClient,
    _build_multipart,
    _calibre_auth_header,
    _calibre_digest,
    _calibre_headers,
    _parse_frontmatter,
    _parse_frontmatter_title,
    _parse_highlight_date,
    _parse_highlight_quote,
)


def _client_env(**env):
    base = {
        "BOOKFUSION_ENABLED": "true",
        "BOOKFUSION_API_KEY": "hl-key",
        "BOOKFUSION_UPLOAD_API_KEY": "up-key",
    }
    base.update(env)
    return patch.dict(os.environ, base, clear=True)


class TestBuildMultipart:
    def test_text_field_has_no_content_type(self):
        body, content_type = _build_multipart([("name", "value")])
        boundary = content_type.split("boundary=")[1]

        assert body.startswith(f"--{boundary}\r\n".encode())
        assert b'Content-Disposition: form-data; name="name"' in body
        assert b"Content-Type" not in body

    def test_file_field_includes_filename(self):
        body, _ = _build_multipart([("file", ("book.epub", b"data"))])
        assert b'filename="book.epub"' in body
        assert b"data" in body

    def test_multiple_fields_are_separated(self):
        body, content_type = _build_multipart([("a", "1"), ("b", "2")])
        boundary = content_type.split("boundary=")[1]
        assert body.count(f"--{boundary}\r\n".encode()) == 2
        assert body.endswith(f"--{boundary}--\r\n".encode())


class TestCalibreHelpers:
    def test_auth_header_matches_basic_format(self):
        expected = base64.b64encode(b"abc:").decode("ascii")
        assert _calibre_auth_header("abc") == f"Basic {expected}"

    def test_headers_include_user_agent_and_accept(self):
        headers = _calibre_headers("abc")
        assert headers["User-Agent"] == "BookFusion Calibre Plugin 0.5.2"
        assert headers["Accept"] == "application/json"
        assert headers["Authorization"].startswith("Basic ")

    def test_digest_matches_calibre_format(self):
        data = b"hello"
        expected = hashlib.sha256()
        expected.update(b"5")
        expected.update(b"\0")
        expected.update(b"hello")
        assert _calibre_digest(data) == expected.hexdigest()


class TestParsers:
    def test_parse_frontmatter_title(self):
        assert _parse_frontmatter_title("title: Dune") == "Dune"

    def test_parse_frontmatter_title_quoted(self):
        assert _parse_frontmatter_title('title: "Dune"') == "Dune"

    def test_parse_frontmatter_fields(self):
        parsed = _parse_frontmatter("title: Dune\nauthors: Frank Herbert\ntags: sci-fi\nseries: Saga")
        assert parsed == {
            "title": "Dune",
            "authors": "Frank Herbert",
            "tags": "sci-fi",
            "series": "Saga",
        }

    def test_parse_highlight_date(self):
        result = _parse_highlight_date("**Date Created**: 2025-01-15 10:30:00 UTC")
        assert result == datetime(2025, 1, 15, 10, 30, 0, tzinfo=UTC)

    def test_parse_highlight_quote(self):
        result = _parse_highlight_quote("> line one\n> line two")
        assert result == "line one line two"


class TestBookFusionClient:
    def test_is_configured_false_when_disabled(self):
        with patch.dict(os.environ, {"BOOKFUSION_ENABLED": "false"}, clear=True):
            assert BookFusionClient().is_configured() is False

    def test_is_configured_true_with_api_key(self):
        with _client_env(BOOKFUSION_UPLOAD_API_KEY=""):
            assert BookFusionClient().is_configured() is True

    @patch("src.api.bookfusion_client.requests.Session")
    def test_check_connection_success(self, mock_session_cls):
        session = Mock()
        response = Mock(status_code=200)
        session.post.return_value = response
        mock_session_cls.return_value = session

        with patch.dict(os.environ, {"BOOKFUSION_API_KEY": "hl-key"}, clear=True):
            ok, msg = BookFusionClient().check_connection()

        assert ok is True
        assert msg == "Connected"

    @patch("src.api.bookfusion_client.requests.Session")
    def test_check_upload_connection_http_error(self, mock_session_cls):
        session = Mock()
        response = Mock(status_code=401)
        session.get.return_value = response
        mock_session_cls.return_value = session

        with patch.dict(os.environ, {"BOOKFUSION_UPLOAD_API_KEY": "up-key"}, clear=True):
            ok, msg = BookFusionClient().check_upload_connection()

        assert ok is False
        assert msg == "HTTP 401"

    @patch("src.api.bookfusion_client.requests.Session")
    def test_check_exists_returns_json_for_existing_book(self, mock_session_cls):
        session = Mock()
        response = Mock(status_code=200)
        response.json.return_value = {"id": "book-1"}
        session.get.return_value = response
        mock_session_cls.return_value = session

        with patch.dict(os.environ, {"BOOKFUSION_UPLOAD_API_KEY": "up-key"}, clear=True):
            client = BookFusionClient()
            client.session = session
            assert client.check_exists("digest") == {"id": "book-1"}

    @patch("src.api.bookfusion_client.requests.Session")
    def test_fetch_library_returns_empty_without_key(self, mock_session_cls):
        mock_session_cls.return_value = Mock()
        with patch.dict(os.environ, {}, clear=True):
            assert BookFusionClient().fetch_library() == []

    @patch("src.api.bookfusion_client.requests.Session")
    def test_fetch_library_paginates(self, mock_session_cls):
        session = Mock()
        resp1 = Mock(status_code=200)
        resp1.json.return_value = [{"id": str(i), "title": f"Book {i}"} for i in range(100)]
        resp2 = Mock(status_code=200)
        resp2.json.return_value = [{"id": "100", "title": "Book 100"}]
        session.get.side_effect = [resp1, resp2]
        mock_session_cls.return_value = session

        with patch.dict(os.environ, {"BOOKFUSION_UPLOAD_API_KEY": "up-key"}, clear=True):
            client = BookFusionClient()
            client.session = session
            books = client.fetch_library()

        assert len(books) == 101
        assert session.get.call_count == 2

    def test_fetch_highlights_requires_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="Highlights API key not configured"):
                BookFusionClient().fetch_highlights()

    @patch("src.api.bookfusion_client.requests.Session")
    def test_sync_all_highlights_saves_books_and_highlights(self, mock_session_cls):
        session = Mock()
        highlights_response = Mock(status_code=200)
        highlights_response.json.return_value = {
            "pages": [
                {
                    "type": "book",
                    "id": "bf-1",
                    "filename": "test.md",
                    "frontmatter": "title: Test Book\nauthor: Author One",
                    "highlights": [
                        {
                            "id": "hl-1",
                            "content": "> A quote\n\n**Date Created**: 2025-01-15 10:00:00 UTC",
                            "chapter_heading": "# Chapter 1",
                        }
                    ],
                }
            ],
            "cursor": None,
            "next_sync_cursor": "cursor-1",
        }
        highlights_response.raise_for_status = Mock()
        session.post.return_value = highlights_response

        library_response = Mock(status_code=200)
        library_response.json.return_value = []
        session.get.return_value = library_response
        mock_session_cls.return_value = session

        db_service = Mock()
        db_service.get_bookfusion_sync_cursor.return_value = None
        db_service.save_bookfusion_highlights.return_value = {"saved": 1, "new_ids": ["hl-1"]}
        db_service.save_bookfusion_books.return_value = 1

        with patch.dict(
            os.environ, {"BOOKFUSION_API_KEY": "hl-key", "BOOKFUSION_UPLOAD_API_KEY": "up-key"}, clear=True
        ):
            client = BookFusionClient()
            client.session = session
            result = client.sync_all_highlights(db_service)

        assert result == {"new_highlights": 1, "books_saved": 1, "new_ids": ["hl-1"]}
        db_service.set_bookfusion_sync_cursor.assert_called_once_with("cursor-1")
