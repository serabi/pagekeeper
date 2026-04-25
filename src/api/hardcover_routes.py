# pyright: reportMissingImports=false
# Hardcover Routes - Flask Blueprint for Hardcover API endpoints
import ipaddress
import logging
import socket
from typing import Any, cast
from urllib.parse import urlparse

from flask import Blueprint, current_app, flash, jsonify, redirect, request, url_for

from src.utils.http import json_error

logger = logging.getLogger(__name__)

# Create Blueprint for Hardcover endpoints
hardcover_bp = Blueprint("hardcover", __name__)


def _get_dependencies():
    database_service = current_app.config.get("database_service")
    container = current_app.config.get("container")
    if database_service is None or container is None:
        logger.error("Hardcover routes not initialized")
        return (
            None,
            None,
            (
                jsonify({"found": False, "message": "Hardcover routes not initialized"}),
                500,
            ),
        )
    return cast(Any, database_service), cast(Any, container), None


def _validate_custom_cover_url(raw_url):
    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"}:
        return "Custom cover URL must start with http:// or https://"
    if not parsed.netloc or not parsed.hostname:
        return "Custom cover URL must include a valid host"

    hostname = parsed.hostname.strip().lower()
    if hostname == "localhost":
        return "Custom cover URL cannot use a local address"

    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_unspecified:
            return "Custom cover URL cannot use a local or private address"
        return None
    except ValueError:
        if "." not in hostname:
            return "Custom cover URL must use a fully qualified public hostname"

    try:
        resolved = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return "Custom cover URL host could not be resolved"

    for _family, _socktype, _proto, _canonname, sockaddr in resolved:
        address = sockaddr[0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_unspecified:
            return "Custom cover URL cannot use a local or private address"

    return None


@hardcover_bp.route("/api/hardcover/resolve", methods=["GET"])
def api_hardcover_resolve():
    """
    Resolve a Hardcover book and return all editions.
    Auto-matches using book metadata if no input provided.

    GET /api/hardcover/resolve?abs_id={abs_id}&input={optional_url_or_id}
    """
    database_service, container, error_response = _get_dependencies()
    if error_response:
        return error_response
    database_service = cast(Any, database_service)
    container = cast(Any, container)

    abs_id = request.args.get("abs_id", "").strip()
    manual_input = request.args.get("input", "").strip()

    if not abs_id:
        return json_error("Missing abs_id parameter", 400, found=False, user_message="Missing abs_id parameter")

    hardcover_client = container.hardcover_client()
    if not hardcover_client.is_configured():
        return json_error("Hardcover not configured", 400, found=False, user_message="Hardcover not configured")

    book_data = None
    author = None
    book = database_service.get_book_by_ref(abs_id)
    existing_details = database_service.get_hardcover_details(book.id) if book else None

    if manual_input:
        # Manual input provided - resolve directly
        book_data = hardcover_client.resolve_book_from_input(manual_input)
    else:
        # Check if there's an existing Hardcover link for this ABS book
        if existing_details and existing_details.hardcover_book_id:
            # Use the existing linked book instead of auto-matching
            book_data = hardcover_client.resolve_book_from_input(existing_details.hardcover_book_id)

        if not book_data:
            # No existing link (or fetch failed) - fall back to auto-match from ABS metadata
            if not book:
                return json_error("Book not found", 404, found=False, user_message="Book not found")

            # Get metadata from ABS (if available)
            item = container.abs_client().get_item_details(abs_id)
            if item:
                meta = item.get("media", {}).get("metadata", {})
                isbn = meta.get("isbn")
                asin = meta.get("asin")
                title = meta.get("title")
                author = meta.get("authorName")
            else:
                # ABS unavailable — fall back to DB book title
                isbn = None
                asin = None
                title = book.title
                author = None

            # Try match cascade: ISBN -> ASIN -> title+author -> title only
            if isbn:
                book_data = hardcover_client.search_by_isbn(isbn)

            if not book_data and asin:
                book_data = hardcover_client.search_by_isbn(asin)

            if not book_data and title and author:
                book_data = hardcover_client.search_by_title_author(title, author)

            if not book_data and title:
                book_data = hardcover_client.search_by_title_author(title, "")

    if not book_data:
        return jsonify(
            {
                "found": False,
                "message": "Could not find book. Please enter Hardcover URL or ID.",
            }
        ), 404

    # Fetch all editions for this book
    book_id = book_data["book_id"]
    editions = hardcover_client.get_book_editions(book_id)

    # Get author from Hardcover (prefer over ABS since we're linking to Hardcover)
    hardcover_author = hardcover_client.get_book_author(book_id)

    # Only show linked_edition_id if we're displaying the same book that's linked
    linked_edition_id = None
    if existing_details and str(existing_details.hardcover_book_id) == str(book_id):
        linked_edition_id = existing_details.hardcover_edition_id

    return jsonify(
        {
            "found": True,
            "book_id": book_id,
            "title": book_data.get("title"),
            "author": hardcover_author or author or "",
            "slug": book_data.get("slug"),
            "cached_image": book_data.get("cached_image"),
            "editions": editions,
            "linked_edition_id": linked_edition_id,
        }
    )


@hardcover_bp.route("/link-hardcover/<abs_id>", methods=["POST"])
def link_hardcover(abs_id):
    """
    Link a book to Hardcover with a specific edition.
    Supports both JSON (new modal flow) and form data (legacy flow).
    """
    from src.db.models import HardcoverDetails

    database_service, container, error_response = _get_dependencies()
    if error_response:
        return error_response
    database_service = cast(Any, database_service)
    container = cast(Any, container)

    # Check if JSON request (new flow) or form data (legacy flow)
    if request.is_json:
        data = request.get_json()
        book_id = data.get("book_id")
        edition_id = data.get("edition_id")
        pages = data.get("pages")
        audio_seconds = data.get("audio_seconds")
        title = data.get("title")
        slug = data.get("slug")
        cached_image = data.get("cached_image")

        if not book_id:
            return jsonify({"error": "Missing book_id"}), 400

        try:
            # Use pages if available, otherwise use -1 for audiobooks (indicates no page count)
            hardcover_pages = pages if pages is not None else (-1 if audio_seconds else None)

            # Determine cover URL: use provided cached_image, or preserve existing
            cover_url = cached_image
            book = database_service.get_book_by_ref(abs_id)
            if not book:
                return jsonify({"error": "Book not found"}), 404
            if not cover_url and book:
                existing = database_service.get_hardcover_details(book.id)
                if existing and existing.hardcover_cover_url:
                    cover_url = existing.hardcover_cover_url
            hardcover_details = HardcoverDetails(
                abs_id=abs_id,
                book_id=book.id,
                hardcover_book_id=str(book_id),
                hardcover_slug=slug or "",
                hardcover_edition_id=str(edition_id) if edition_id else "",
                hardcover_pages=int(hardcover_pages) if hardcover_pages is not None else 0,
                hardcover_audio_seconds=int(audio_seconds) if audio_seconds is not None else 0,
                hardcover_cover_url=cover_url,
                matched_by="manual",
            )

            database_service.save_hardcover_details(hardcover_details)
        except Exception as e:
            logger.error(f"Failed to save hardcover details: {e}")
            return jsonify({"error": "Database update failed"}), 500

        # Post-link setup (non-fatal): resolve editions, create user_book, pull dates, push progress
        try:
            hc_service = container.hardcover_service()
            hc_service.resolve_editions(hardcover_details)

            book = database_service.get_book_by_ref(abs_id)
            if book:
                hc_service._get_or_create_user_book(book, hardcover_details, edition_id)
                hc_service._pull_dates_at_match(book)
                # Re-fetch: _pull_dates_at_match may have updated dates in DB
                book = database_service.get_book_by_ref(abs_id)
                hc_service.push_initial_progress(book, container.hardcover_sync_client())
        except Exception as e:
            logger.warning(f"Post-link Hardcover setup failed (link saved): {e}")

        return jsonify({"success": True, "title": title})

    # Legacy form data flow
    url = request.form.get("hardcover_url", "").strip()
    if not url:
        return redirect(url_for("dashboard.index"))

    # Resolve book
    book_data = container.hardcover_client().resolve_book_from_input(url)
    if not book_data:
        flash(f"Could not find book for: {url}", "error")
        return redirect(url_for("dashboard.index"))

    try:
        book = database_service.get_book_by_ref(abs_id)
        if not book:
            flash("Book not found", "error")
            return redirect(url_for("dashboard.index"))
        hardcover_details = HardcoverDetails(
            abs_id=abs_id,
            book_id=book.id,
            hardcover_book_id=str(book_data["book_id"]),
            hardcover_slug=book_data.get("slug") or "",
            hardcover_edition_id=str(book_data.get("edition_id") or ""),
            hardcover_pages=int(book_data.get("pages") or 0),
            matched_by="manual",
        )

        database_service.save_hardcover_details(hardcover_details)
    except Exception as e:
        logger.error(f"Failed to save hardcover details: {e}")
        flash("Database update failed", "error")
        return redirect(url_for("dashboard.index"))

    # Post-link setup (non-fatal): resolve editions, create user_book, pull dates, push progress
    try:
        hc_service = container.hardcover_service()
        hc_service.resolve_editions(hardcover_details)

        book = database_service.get_book_by_ref(abs_id)
        if book:
            hc_service._get_or_create_user_book(book, hardcover_details, book_data.get("edition_id"))
            hc_service._pull_dates_at_match(book)
            # Re-fetch: _pull_dates_at_match may have updated dates in DB
            book = database_service.get_book_by_ref(abs_id)
            hc_service.push_initial_progress(book, container.hardcover_sync_client())
    except Exception as e:
        logger.warning(f"Post-link Hardcover setup failed (link saved): {e}")

    flash(f"Linked Hardcover: {book_data.get('title')}", "success")

    return redirect(url_for("dashboard.index"))


@hardcover_bp.route("/api/hardcover/cover-search", methods=["GET"])
def api_cover_search():
    """Search Hardcover for books with cover images (cover picker)."""
    database_service, container, error_response = _get_dependencies()
    if error_response:
        return error_response
    container = cast(Any, container)

    query = request.args.get("query", "").strip()
    if not query:
        return jsonify({"results": []}), 400

    hardcover_client = container.hardcover_client()
    if not hardcover_client.is_configured():
        return jsonify({"results": [], "error": "Hardcover not configured"}), 200

    results = hardcover_client.search_books_with_covers(query)
    return jsonify({"results": results})


@hardcover_bp.route("/api/book/<abs_id>/cover", methods=["POST"])
def set_book_cover(abs_id):
    """Set or update a book's cover image from Hardcover or a custom URL."""
    from src.db.models import HardcoverDetails

    database_service, container, error_response = _get_dependencies()
    if error_response:
        return error_response
    database_service = cast(Any, database_service)

    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    book = database_service.get_book_by_ref(abs_id)
    if not book:
        return jsonify({"error": "Book not found"}), 404

    source = data.get("source")
    cover_url = None

    if source == "hardcover":
        cached_image = data.get("cached_image", "")
        book_id = data.get("book_id")
        slug = data.get("slug")

        if not cached_image:
            return jsonify({"error": "No image URL provided"}), 400

        cover_url = cached_image

        # Update or create HardcoverDetails with the cover URL
        existing = database_service.get_hardcover_details(book.id)
        if existing:
            existing.hardcover_cover_url = cover_url
            if book_id:
                existing.hardcover_book_id = str(book_id)
            if slug:
                existing.hardcover_slug = slug
            database_service.save_hardcover_details(existing)
        else:
            details = HardcoverDetails(
                abs_id=abs_id,
                book_id=book.id,
                hardcover_book_id=str(book_id) if book_id else "",
                hardcover_slug=slug or "",
                hardcover_cover_url=cover_url,
                matched_by="cover_picker",
            )
            database_service.save_hardcover_details(details)

        # Promote to custom_cover_url so it takes highest waterfall priority
        book.custom_cover_url = cover_url
        database_service.save_book(book)

    elif source == "custom":
        url = (data.get("url") or "").strip()
        if not url:
            return jsonify({"error": "No URL provided"}), 400
        validation_error = _validate_custom_cover_url(url)
        if validation_error:
            return jsonify({"error": validation_error}), 400
        book.custom_cover_url = url
        database_service.save_book(book)
        cover_url = url

        # Clear hardcover_cover_url when picking a custom cover
        hc_details = database_service.get_hardcover_details(book.id)
        if hc_details and hc_details.hardcover_cover_url:
            hc_details.hardcover_cover_url = None
            database_service.save_hardcover_details(hc_details)

    else:
        return jsonify({"error": "Invalid source"}), 400

    return jsonify({"success": True, "cover_url": cover_url})


@hardcover_bp.route("/api/book/<abs_id>/cover", methods=["DELETE"])
def delete_book_cover(abs_id):
    """Clear custom and Hardcover cover URLs for a book."""
    database_service, container, error_response = _get_dependencies()
    if error_response:
        return error_response
    database_service = cast(Any, database_service)

    book = database_service.get_book_by_ref(abs_id)
    if not book:
        return jsonify({"error": "Book not found"}), 404

    if book.custom_cover_url:
        book.custom_cover_url = None
        database_service.save_book(book)

    hc_details = database_service.get_hardcover_details(book.id)
    if hc_details and hc_details.hardcover_cover_url:
        hc_details.hardcover_cover_url = None
        database_service.save_hardcover_details(hc_details)

    return jsonify({"success": True})
