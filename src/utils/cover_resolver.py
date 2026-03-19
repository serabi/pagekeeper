"""Cover URL resolution waterfall for book display."""


def resolve_book_covers(book, abs_service, database_service, book_type,
                        booklore_meta=None, hardcover_details=None):
    """Resolve cover URLs for a book using the priority waterfall.

    Priority chain:
        custom_cover_url -> Booklore cover -> KoSync cover -> Hardcover cover

    When no custom cover is set and an ABS cover is available, the ABS cover
    becomes the primary ``cover_url`` and the best non-ABS source becomes
    ``fallback_cover_url``.

    Returns dict with 'cover_url', 'custom_cover_url', 'abs_cover_url',
    'fallback_cover_url'.
    """
    custom_cover_url = book.custom_cover_url or None
    abs_cover_url = None
    if book.abs_id and book_type != 'ebook-only' and not book.abs_id.startswith('bf-'):
        abs_cover_url = f"/api/cover-proxy/{book.abs_id}"

    # Cover URL -- preserve custom override, otherwise walk the waterfall.
    cover_url = custom_cover_url
    fallback_cover_url = None

    # Booklore cover (authenticated proxy, always available if metadata exists)
    if not cover_url and booklore_meta:
        bl_id = (booklore_meta.raw_metadata_dict or {}).get('id')
        if bl_id:
            from src.blueprints.helpers import booklore_cover_proxy_prefix
            prefix = booklore_cover_proxy_prefix(booklore_meta.server_id)
            cover_url = f"{prefix}/{bl_id}"

    if not cover_url and book.kosync_doc_id:
        cover_url = f'/covers/{book.kosync_doc_id}.jpg'

    # Hardcover cover fallback
    if not cover_url and book.id:
        hc = hardcover_details if hardcover_details is not None else database_service.get_hardcover_details(book.id)
        if hc and hc.hardcover_cover_url:
            cover_url = hc.hardcover_cover_url

    non_abs_cover_url = cover_url
    if not custom_cover_url and abs_cover_url:
        fallback_cover_url = non_abs_cover_url if non_abs_cover_url != abs_cover_url else None
        cover_url = abs_cover_url
    elif custom_cover_url:
        fallback_cover_url = None

    return {
        'cover_url': cover_url,
        'custom_cover_url': custom_cover_url,
        'abs_cover_url': abs_cover_url,
        'fallback_cover_url': fallback_cover_url,
    }
