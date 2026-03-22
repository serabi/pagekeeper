"""Covers blueprint — /covers/<filename> and /api/cover-proxy/booklore/."""

import logging

import requests
from flask import Blueprint, Response, send_from_directory

from src.blueprints.helpers import get_container, get_covers_dir, get_database_service

logger = logging.getLogger(__name__)

covers_bp = Blueprint('covers', __name__)


@covers_bp.route('/covers/<path:filename>')
def serve_cover(filename):
    """Serve cover images with lazy extraction."""
    COVERS_DIR = get_covers_dir()
    doc_hash = filename.replace('.jpg', '')

    # 1. Check if file exists
    cover_path = COVERS_DIR / filename
    if cover_path.exists():
        resp = send_from_directory(COVERS_DIR, filename)
        resp.headers['Cache-Control'] = 'public, max-age=86400, immutable'
        return resp

    # 2. Try to extract
    database_service = get_database_service()
    container = get_container()
    book = database_service.get_book_by_kosync_id(doc_hash)

    if book and book.ebook_filename:
        try:
            parser = container.ebook_parser()
            full_book_path = parser.resolve_book_path(book.ebook_filename)

            if parser.extract_cover(full_book_path, cover_path):
                resp = send_from_directory(COVERS_DIR, filename)
                resp.headers['Cache-Control'] = 'public, max-age=86400, immutable'
                return resp
        except Exception as e:
            logger.debug(f"Lazy cover extraction failed: {e}")

    return "Cover not found", 404


@covers_bp.route('/api/cover-proxy/booklore/<int:book_id>')
def proxy_booklore_cover(book_id):
    """Proxy cover access to Booklore (auth via query-parameter JWT)."""
    container = get_container()
    return _proxy_booklore_cover_for(container.booklore_client(), book_id, cache_prefix="bl")


@covers_bp.route('/api/cover-proxy/booklore2/<int:book_id>')
def proxy_booklore2_cover(book_id):
    """Proxy cover access to Booklore 2nd instance."""
    container = get_container()
    return _proxy_booklore_cover_for(container.booklore_client_2(), book_id, cache_prefix="bl2")


def _proxy_booklore_cover_for(bl_client, book_id, cache_prefix="bl"):
    """Shared cover proxy logic with local caching for offline resilience."""
    covers_dir = get_covers_dir()
    cache_file = covers_dir / f"{cache_prefix}-{book_id}.jpg"

    if bl_client.is_configured():
        try:
            token = bl_client._get_fresh_token()
            if token:
                url = f"{bl_client.base_url}/api/v1/media/book/{book_id}/cover"
                req = requests.get(url, params={"token": token}, timeout=10)
                if req.status_code == 200:
                    data = req.content
                    try:
                        cache_file.write_bytes(data)
                    except Exception:
                        logger.debug(f"Failed to cache Booklore cover for book {book_id}")
                    resp = Response(data, content_type='image/jpeg')
                    resp.headers['Cache-Control'] = 'public, max-age=86400, immutable'
                    return resp
        except Exception as e:
            logger.error(f"Error proxying Booklore cover for book {book_id}: {e}")

    # Fall back to local cache
    if cache_file.exists():
        resp = send_from_directory(covers_dir, cache_file.name)
        resp.headers['Cache-Control'] = 'public, max-age=86400, immutable'
        return resp

    return "Cover not found", 404
