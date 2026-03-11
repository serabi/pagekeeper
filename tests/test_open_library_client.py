"""Unit tests for OpenLibraryClient — all HTTP mocked."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.api.open_library_client import OpenLibraryClient


class TestOpenLibraryClient(unittest.TestCase):

    def setUp(self):
        self.client = OpenLibraryClient()
        self.sample_doc = {
            "key": "/works/OL45883W",
            "title": "Dune",
            "author_name": ["Frank Herbert"],
            "cover_i": 8231856,
            "isbn": ["9780441013593", "0441013597"],
            "first_publish_year": 1965,
        }

    def _mock_response(self, docs, status_code=200):
        """Create a mock requests.get response."""
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"docs": docs}
        return mock_resp

    # -- Output shape --

    @patch('src.api.open_library_client.requests.get')
    def test_normalized_output_shape(self, mock_get):
        """Verify all expected fields are present in result."""
        mock_get.return_value = self._mock_response([self.sample_doc])

        results = self.client.search_books("Dune")

        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r['title'], 'Dune')
        self.assertEqual(r['author'], 'Frank Herbert')
        self.assertIn('covers.openlibrary.org', r['cover_url'])
        self.assertEqual(r['isbn'], '9780441013593')
        self.assertEqual(r['ol_work_key'], '/works/OL45883W')
        self.assertEqual(r['first_publish_year'], 1965)

    # -- Missing fields --

    @patch('src.api.open_library_client.requests.get')
    def test_missing_cover(self, mock_get):
        """No cover_i → cover_url is None."""
        doc = {**self.sample_doc}
        del doc['cover_i']
        mock_get.return_value = self._mock_response([doc])

        results = self.client.search_books("Dune")
        self.assertIsNone(results[0]['cover_url'])

    @patch('src.api.open_library_client.requests.get')
    def test_missing_author(self, mock_get):
        """No author_name → author is None."""
        doc = {**self.sample_doc, 'author_name': []}
        mock_get.return_value = self._mock_response([doc])

        results = self.client.search_books("Dune")
        self.assertIsNone(results[0]['author'])

    @patch('src.api.open_library_client.requests.get')
    def test_missing_isbn(self, mock_get):
        """Empty isbn list → isbn is None."""
        doc = {**self.sample_doc, 'isbn': []}
        mock_get.return_value = self._mock_response([doc])

        results = self.client.search_books("Dune")
        self.assertIsNone(results[0]['isbn'])

    # -- ISBN selection --

    def test_pick_isbn_prefers_isbn13(self):
        """_pick_isbn prefers ISBN-13 over ISBN-10."""
        result = OpenLibraryClient._pick_isbn(["0441013597", "9780441013593"])
        self.assertEqual(result, "9780441013593")

    def test_pick_isbn_fallback_to_isbn10(self):
        """_pick_isbn falls back to ISBN-10 when no ISBN-13 present."""
        result = OpenLibraryClient._pick_isbn(["0441013597"])
        self.assertEqual(result, "0441013597")

    def test_pick_isbn_empty(self):
        """_pick_isbn returns None for empty list."""
        result = OpenLibraryClient._pick_isbn([])
        self.assertIsNone(result)

    # -- Error handling --

    @patch('src.api.open_library_client.requests.get')
    def test_request_exception_returns_empty(self, mock_get):
        """RequestException → empty list."""
        import requests
        mock_get.side_effect = requests.RequestException("timeout")

        results = self.client.search_books("anything")
        self.assertEqual(results, [])

    @patch('src.api.open_library_client.requests.get')
    def test_bad_json_returns_empty(self, mock_get):
        """Invalid JSON response → empty list."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.side_effect = ValueError("bad json")
        mock_get.return_value = mock_resp

        results = self.client.search_books("anything")
        self.assertEqual(results, [])

    # -- Limit param --

    @patch('src.api.open_library_client.requests.get')
    def test_limit_passed_to_api(self, mock_get):
        """The limit parameter is forwarded to the API call."""
        mock_get.return_value = self._mock_response([])

        self.client.search_books("test", limit=5)

        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs['params']['limit'], 5)


if __name__ == '__main__':
    unittest.main()
