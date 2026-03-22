"""Tests for Booklore cover proxy endpoint and auth contract."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.blueprints.covers import _proxy_booklore_cover_for  # noqa: E402


class TestBookloreCoverProxy(unittest.TestCase):
    """Verify _proxy_booklore_cover_for sends correct URL, auth, and headers."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.covers_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _make_client(self, configured=True, token='fake-jwt-token'):
        client = Mock()
        client.is_configured.return_value = configured
        client.base_url = 'http://booklore.local'
        client._get_fresh_token.return_value = token
        return client

    # ── URL and auth contract ──────────────────────────────────────

    @patch('src.blueprints.covers.get_covers_dir')
    @patch('src.blueprints.covers.requests.get')
    def test_uses_media_endpoint_path(self, mock_get, mock_dir):
        """API path must be /api/v1/media/book/{id}/cover."""
        mock_dir.return_value = self.covers_dir
        mock_get.return_value = Mock(status_code=404)
        bl = self._make_client()

        _proxy_booklore_cover_for(bl, 3880)

        called_url = mock_get.call_args[0][0]
        self.assertEqual(called_url, 'http://booklore.local/api/v1/media/book/3880/cover')

    @patch('src.blueprints.covers.get_covers_dir')
    @patch('src.blueprints.covers.requests.get')
    def test_auth_via_query_param_not_header(self, mock_get, mock_dir):
        """JWT must be sent as ?token= query param, not Authorization header."""
        mock_dir.return_value = self.covers_dir
        mock_get.return_value = Mock(status_code=404)
        bl = self._make_client(token='my-secret-jwt')

        _proxy_booklore_cover_for(bl, 42)

        kwargs = mock_get.call_args[1]
        self.assertEqual(kwargs['params'], {'token': 'my-secret-jwt'})
        self.assertNotIn('headers', kwargs,
                         'Should not send Authorization header — Booklore media uses query-param auth')

    # ── Response contract ──────────────────────────────────────────

    @patch('src.blueprints.covers.get_covers_dir')
    @patch('src.blueprints.covers.requests.get')
    def test_content_type_hardcoded_jpeg(self, mock_get, mock_dir):
        """Response must be image/jpeg regardless of upstream Content-Type."""
        mock_dir.return_value = self.covers_dir
        upstream = Mock(status_code=200, content=b'\xff\xd8\xff\xe0')
        mock_get.return_value = upstream
        bl = self._make_client()

        resp = _proxy_booklore_cover_for(bl, 1)

        self.assertEqual(resp.content_type, 'image/jpeg')

    @patch('src.blueprints.covers.get_covers_dir')
    @patch('src.blueprints.covers.requests.get')
    def test_cache_control_header_set(self, mock_get, mock_dir):
        """Successful proxy response must set long-lived cache headers."""
        mock_dir.return_value = self.covers_dir
        upstream = Mock(status_code=200, content=b'imgdata')
        mock_get.return_value = upstream
        bl = self._make_client()

        resp = _proxy_booklore_cover_for(bl, 1)

        self.assertEqual(resp.headers.get('Cache-Control'), 'public, max-age=86400, immutable')

    @patch('src.blueprints.covers.get_covers_dir')
    @patch('src.blueprints.covers.requests.get')
    def test_streams_upstream_body(self, mock_get, mock_dir):
        """Proxy must pass the upstream body content through."""
        mock_dir.return_value = self.covers_dir
        upstream = Mock(status_code=200, content=b'chunk1chunk2')
        mock_get.return_value = upstream
        bl = self._make_client()

        resp = _proxy_booklore_cover_for(bl, 1)

        body = b''.join(resp.response)
        self.assertEqual(body, b'chunk1chunk2')

    # ── Caching behaviour ─────────────────────────────────────────

    @patch('src.blueprints.covers.get_covers_dir')
    @patch('src.blueprints.covers.requests.get')
    def test_successful_fetch_writes_cache_file(self, mock_get, mock_dir):
        """On upstream 200, cover bytes must be written to cache file."""
        mock_dir.return_value = self.covers_dir
        upstream = Mock(status_code=200, content=b'cover-bytes')
        mock_get.return_value = upstream
        bl = self._make_client()

        _proxy_booklore_cover_for(bl, 42, cache_prefix="bl")

        cache_file = self.covers_dir / "bl-42.jpg"
        self.assertTrue(cache_file.exists())
        self.assertEqual(cache_file.read_bytes(), b'cover-bytes')

    @patch('src.blueprints.covers.send_from_directory')
    @patch('src.blueprints.covers.get_covers_dir')
    def test_serves_from_cache_when_unconfigured(self, mock_dir, mock_send):
        """When Booklore is not configured but cache exists, serve cached cover."""
        mock_dir.return_value = self.covers_dir
        cache_file = self.covers_dir / "bl-7.jpg"
        cache_file.write_bytes(b'cached-data')
        mock_send.return_value = Mock(headers={})
        bl = self._make_client(configured=False)

        _proxy_booklore_cover_for(bl, 7)

        mock_send.assert_called_once_with(self.covers_dir, "bl-7.jpg")

    # ── Error paths ────────────────────────────────────────────────

    @patch('src.blueprints.covers.get_covers_dir')
    def test_not_configured_no_cache_returns_404(self, mock_dir):
        mock_dir.return_value = self.covers_dir
        bl = self._make_client(configured=False)
        result = _proxy_booklore_cover_for(bl, 1)
        self.assertEqual(result, ("Cover not found", 404))

    @patch('src.blueprints.covers.get_covers_dir')
    def test_auth_failure_no_cache_returns_404(self, mock_dir):
        mock_dir.return_value = self.covers_dir
        bl = self._make_client(token=None)
        result = _proxy_booklore_cover_for(bl, 1)
        self.assertEqual(result, ("Cover not found", 404))

    @patch('src.blueprints.covers.get_covers_dir')
    @patch('src.blueprints.covers.requests.get')
    def test_upstream_404_no_cache_returns_404(self, mock_get, mock_dir):
        mock_dir.return_value = self.covers_dir
        mock_get.return_value = Mock(status_code=404)
        bl = self._make_client()
        result = _proxy_booklore_cover_for(bl, 9999)
        self.assertEqual(result, ("Cover not found", 404))

    @patch('src.blueprints.covers.get_covers_dir')
    @patch('src.blueprints.covers.requests.get')
    def test_network_error_no_cache_returns_404(self, mock_get, mock_dir):
        mock_dir.return_value = self.covers_dir
        mock_get.side_effect = ConnectionError("refused")
        bl = self._make_client()
        result = _proxy_booklore_cover_for(bl, 1)
        self.assertEqual(result, ("Cover not found", 404))

    @patch('src.blueprints.covers.send_from_directory')
    @patch('src.blueprints.covers.get_covers_dir')
    @patch('src.blueprints.covers.requests.get')
    def test_network_error_with_cache_serves_cached(self, mock_get, mock_dir, mock_send):
        """When upstream fails but cache exists, serve cached cover."""
        mock_dir.return_value = self.covers_dir
        cache_file = self.covers_dir / "bl-5.jpg"
        cache_file.write_bytes(b'old-cover')
        mock_get.side_effect = ConnectionError("refused")
        mock_send.return_value = Mock(headers={})
        bl = self._make_client()

        _proxy_booklore_cover_for(bl, 5)

        mock_send.assert_called_once_with(self.covers_dir, "bl-5.jpg")


if __name__ == '__main__':
    unittest.main()
