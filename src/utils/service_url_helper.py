"""Service URL resolution helpers — pure functions with no Flask dependency."""

import os


def get_service_web_url(service_prefix):
    """Return the preferred web URL for a service based on its URL mode setting.

    Reads {PREFIX}_HEADER_URL_MODE (default: 'external') and returns the
    internal or external URL accordingly, with fallbacks to legacy server URLs.
    """
    prefix = service_prefix.upper()
    mode = os.environ.get(f'{prefix}_HEADER_URL_MODE', 'external').lower()

    legacy_fallbacks = {
        'ABS': os.environ.get('ABS_SERVER', ''),
        'BOOKLORE': os.environ.get('BOOKLORE_SERVER', ''),
        'STORYTELLER': os.environ.get('STORYTELLER_API_URL', ''),
        'CWA': os.environ.get('CWA_SERVER', ''),
    }
    internal_url = os.environ.get(f'{prefix}_WEB_URL_INTERNAL', '') or legacy_fallbacks.get(prefix, '')
    external_url = os.environ.get(f'{prefix}_WEB_URL_EXTERNAL', '') or os.environ.get(f'{prefix}_WEB_URL', '')

    if prefix == 'HARDCOVER' and not external_url:
        external_url = 'https://hardcover.app'

    if mode == 'internal':
        return (internal_url or external_url).rstrip('/')
    return (external_url or internal_url).rstrip('/')


def get_hardcover_book_url(slug_or_id):
    """Return a Hardcover book URL using the configured service base when available."""
    if not slug_or_id:
        return None
    base_url = get_service_web_url('HARDCOVER') or 'https://hardcover.app'
    return f"{base_url}/books/{slug_or_id}"
