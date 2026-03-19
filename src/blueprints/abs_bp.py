"""ABS blueprint — routes specific to Audiobookshelf integration."""

import logging
import re

import requests
from flask import Blueprint, Response, jsonify, send_from_directory

from src.blueprints.helpers import get_abs_service, get_container, get_covers_dir, get_database_service

logger = logging.getLogger(__name__)

abs_bp = Blueprint('abs', __name__)


@abs_bp.route('/api/abs/libraries', methods=['GET'])
def get_abs_libraries():
    """Return available ABS libraries."""
    abs_service = get_abs_service()
    if not abs_service.is_available():
        return jsonify({"error": "ABS not configured"}), 400
    libraries = abs_service.get_libraries()
    return jsonify(libraries)


@abs_bp.route('/api/cover-proxy/<book_ref>')
def proxy_cover(book_ref):
    """Proxy cover access with local caching for offline resilience."""
    book = get_database_service().get_book_by_ref(book_ref)
    abs_id = book.abs_id if book else book_ref

    if not re.fullmatch(r'[a-zA-Z0-9_\-]+', abs_id):
        return "Invalid ID", 400

    covers_dir = get_covers_dir()
    cache_file = covers_dir / f"abs-{abs_id}.jpg"

    # Try upstream when ABS is available
    abs_service = get_abs_service()
    if abs_service.is_available():
        try:
            container = get_container()
            token = container.abs_client().token
            base_url = container.abs_client().base_url
            url = f"{base_url.rstrip('/')}/api/items/{abs_id}/cover"
            req = requests.get(url, headers={"Authorization": f"Bearer {token}"}, stream=True, timeout=10)
            if req.status_code == 200:
                data = req.content
                try:
                    cache_file.write_bytes(data)
                except Exception:
                    logger.debug(f"Failed to cache cover for '{abs_id}'")
                resp = Response(data, content_type=req.headers.get('content-type', 'image/jpeg'))
                resp.headers['Cache-Control'] = 'public, max-age=86400, immutable'
                return resp
        except Exception as e:
            logger.error(f"Error proxying cover for '{abs_id}': {e}")

    # Fall back to local cache
    if cache_file.exists():
        resp = send_from_directory(covers_dir, cache_file.name)
        resp.headers['Cache-Control'] = 'public, max-age=86400, immutable'
        return resp

    return "Cover not found", 404
