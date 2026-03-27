"""Assemble enriched book metadata from all linked services."""

import logging

from src.utils.service_url_helper import get_hardcover_book_url, get_service_web_url

logger = logging.getLogger(__name__)


def build_book_metadata(book, container, database_service, abs_service, booklore_client=None):
    """Assemble enriched metadata dict from all linked services for a book.

    Returns a dict with keys like 'author', 'narrator', 'description', 'genres',
    'duration', 'isbn', 'asin', 'pages', 'hardcover_url', 'booklore_url',
    'bf_tags', 'bf_series', 'abs_url', etc.
    """
    abs_id = book.abs_id
    metadata = {}
    sync_mode = book.sync_mode

    # ABS metadata (subtitle, author, narrator, duration, genres, description)
    if sync_mode != 'ebook_only':
        try:
            abs_item = abs_service.get_item_details(abs_id)
            if abs_item:
                abs_meta = abs_item.get('media', {}).get('metadata', {})
                metadata['author'] = abs_meta.get('authorName') or ''
                metadata['narrator'] = abs_meta.get('narratorName') or ''
                metadata['subtitle'] = abs_meta.get('subtitle') or ''
                metadata['description'] = abs_meta.get('description') or ''
                metadata['genres'] = abs_meta.get('genres') or []
                duration = abs_item.get('media', {}).get('duration')
                if duration:
                    hrs = int(duration // 3600)
                    mins = int((duration % 3600) // 60)
                    metadata['duration'] = f"{hrs}h {mins}m" if hrs else f"{mins}m"
        except Exception as e:
            logger.debug("abs_service.get_item_details failed for abs_id=%s: %s", abs_id, e, exc_info=True)

    # Fall back to cached book metadata when ABS data is unavailable
    if not metadata.get('author') and book.author:
        metadata['author'] = book.author
    if not metadata.get('subtitle') and book.subtitle:
        metadata['subtitle'] = book.subtitle

    # Fallback duration from stored book data (in case ABS API call failed or was skipped)
    if not metadata.get('duration') and book.duration and book.duration > 0:
        hrs = int(book.duration // 3600)
        mins = int((book.duration % 3600) // 60)
        metadata['duration'] = f"{hrs}h {mins}m" if hrs else f"{mins}m"

    # Hardcover details (ISBN, ASIN, pages, slug)
    hardcover = database_service.get_hardcover_details(book.id)
    if hardcover:
        metadata['isbn'] = hardcover.isbn
        metadata['asin'] = hardcover.asin
        if hardcover.hardcover_pages and hardcover.hardcover_pages > 0:
            metadata['pages'] = hardcover.hardcover_pages
        metadata['hardcover_slug'] = hardcover.hardcover_slug
        hardcover_ref = hardcover.hardcover_slug or hardcover.hardcover_book_id
        metadata['hardcover_url'] = get_hardcover_book_url(hardcover_ref)
        # Map HC status ID to a human-readable label for the detail page
        hc_status_labels = {1: 'Want to Read', 2: 'Currently Reading', 3: 'Read', 4: 'Paused', 5: 'DNF'}
        if hardcover.hardcover_status_id:
            metadata['hardcover_status'] = hc_status_labels.get(hardcover.hardcover_status_id)
            metadata['hardcover_status_id'] = hardcover.hardcover_status_id

    # Hardcover metadata enrichment (description, tags, subtitle, release_year)
    # Only use description/tags from user-verified matches to avoid wrong-book data
    if hardcover and hardcover.hardcover_book_id:
        hc_verified = hardcover.matched_by in ('manual', 'cover_picker')
        try:
            hc_client = container.hardcover_client()
            if hc_client and hc_client.is_configured():
                hc_meta = hc_client.get_book_metadata(int(hardcover.hardcover_book_id))
                if hc_meta:
                    if hc_verified:
                        if not metadata.get('description') and hc_meta.get('description'):
                            metadata['description'] = hc_meta['description']
                        if not metadata.get('genres') and hc_meta.get('genres'):
                            metadata['genres'] = hc_meta['genres']
                        if hc_meta.get('tags'):
                            metadata['hc_tags'] = hc_meta['tags']
                        if not metadata.get('subtitle') and hc_meta.get('subtitle'):
                            metadata['subtitle'] = hc_meta['subtitle']
                    if hc_meta.get('release_year'):
                        metadata['release_year'] = hc_meta['release_year']
        except Exception as e:
            logger.debug("Hardcover metadata fetch failed: %s", e)

    # Booklore metadata (description, publisher, language)
    if book.ebook_filename:
        bl_client = booklore_client or container.booklore_client_group()
        try:
            if bl_client and bl_client.is_configured():
                bl_book = bl_client.find_book_by_filename(book.ebook_filename, allow_refresh=False)
                if not bl_book and book.original_ebook_filename:
                    bl_book = bl_client.find_book_by_filename(book.original_ebook_filename, allow_refresh=False)
                if bl_book:
                    if not metadata.get('description') and bl_book.get('description'):
                        metadata['description'] = bl_book['description']
                    instance_id = bl_book.get('_instance_id', 'default')
                    bl_prefix = f"BOOKLORE{'_2' if instance_id == '2' else ''}"
                    bl_base = get_service_web_url(bl_prefix) or getattr(bl_client, 'base_url', '')
                    bl_url = f"{bl_base}/book/{bl_book.get('id')}?tab=view"
                    metadata['booklore_url'] = bl_url
        except Exception as e:
            logger.debug("Booklore lookup failed for ebook_filename=%s, original=%s, client=%s: %s",
                         book.ebook_filename, getattr(book, 'original_ebook_filename', None),
                         getattr(bl_client, 'base_url', '?'), e)

    # BookFusion catalog entry (tags, series)
    bf_book = database_service.get_bookfusion_book_by_book_id(book.id)
    if bf_book:
        metadata['bf_tags'] = bf_book.tags or ''
        metadata['bf_series'] = bf_book.series or ''

    # ABS item URL
    if sync_mode != 'ebook_only':
        abs_base = get_service_web_url('ABS') or (abs_service.abs_client.base_url if abs_service.is_available() else '')
        metadata['abs_url'] = f"{abs_base}/item/{abs_id}" if abs_base else None

    # Stash the hardcover row for callers that need it (service info, template flags)
    metadata['_hardcover'] = hardcover

    return metadata


def build_service_info(book, states_by_book, container, abs_service, metadata,
                       has_bookfusion_link):
    """Build per-service state data, integration flags, and enabled-service map.

    Returns (service_states, integrations, services_enabled).
    """
    sync_mode = book.sync_mode
    hardcover = metadata.get('_hardcover')

    # Build per-service state data for the Services tab
    service_states = {}
    for state in states_by_book.get(book.id, []):
        pct = round(state.percentage * 100, 1) if state.percentage else 0
        service_states[state.client_name] = {'percentage': pct, 'timestamp': state.timestamp}

    integrations = {
        'abs': sync_mode != 'ebook_only',
        'kosync': book.kosync_doc_id is not None,
        'storyteller': book.storyteller_uuid is not None,
        'hardcover': hardcover is not None,
        'bookfusion': has_bookfusion_link,
        'booklore': bool(metadata.get('booklore_url')),
    }

    # Which services are enabled system-wide (for showing "Link" on unconnected services)
    storyteller = container.storyteller_client()
    hardcover = container.hardcover_client()
    bookfusion = container.bookfusion_client()
    bl_group = container.booklore_client_group()
    services_enabled = {
        'abs': abs_service is not None and abs_service.is_available(),
        'kosync': True,  # KoSync is always available (built-in server)
        'storyteller': storyteller is not None and storyteller.is_configured(),
        'hardcover': hardcover is not None and hardcover.is_configured(),
        'bookfusion': bookfusion is not None and bookfusion.is_configured(),
        'booklore': bl_group is not None and bl_group.is_configured(),
    }

    return service_states, integrations, services_enabled
