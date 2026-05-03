"""TBR (To Be Read) blueprint — Want to Read list API endpoints."""

import json as _json
import logging

from flask import Blueprint, jsonify, request

from src.blueprints.helpers import get_container, get_database_service
from src.services.hardcover_service import HC_IGNORED, HC_WANT_TO_READ
from src.utils.http import json_error

logger = logging.getLogger(__name__)

tbr_bp = Blueprint("tbr", __name__)


# ── Helpers ──────────────────────────────────────────────────────────


def _serialize_tbr_item(item):
    """Serialize a TbrItem to a JSON-safe dict."""
    return {
        "id": item.id,
        "title": item.title,
        "author": item.author,
        "cover_url": item.cover_url,
        "notes": item.notes,
        "source": item.source,
        "added_at": item.added_at.isoformat() if item.added_at else None,
        "hardcover_book_id": item.hardcover_book_id,
        "hardcover_slug": item.hardcover_slug,
        "ol_work_key": item.ol_work_key,
        "isbn": item.isbn,
        "book_abs_id": item.book_abs_id,
        "hardcover_list_name": item.hardcover_list_name,
        "description": item.description,
        "page_count": item.page_count,
        "rating": item.rating,
        "ratings_count": item.ratings_count,
        "release_year": item.release_year,
        "genres": _json.loads(item.genres) if item.genres else [],
        "subtitle": item.subtitle,
        "priority": item.priority or 0,
    }


def _enrich_tbr_item(item, data, database_service):
    """Fetch full metadata from HC or OL and persist on the TBR item.

    Called once after a new item is created. Enrichment is best-effort —
    failures are logged but do not prevent the item from being saved.
    Returns the updated item if enrichment succeeded, else None.
    """
    updates = {}

    if item.hardcover_book_id and not item.description:
        try:
            hc_client = get_container().hardcover_client()
            if hc_client.is_configured():
                meta = hc_client.get_book_metadata(item.hardcover_book_id)
                if meta:
                    if meta.get("description"):
                        updates["description"] = meta["description"]
                    if meta.get("subtitle"):
                        updates["subtitle"] = meta["subtitle"]
                    if meta.get("pages") and not item.page_count:
                        updates["page_count"] = meta["pages"]
                    if meta.get("rating") is not None and item.rating is None:
                        updates["rating"] = meta["rating"]
                    if meta.get("ratings_count") is not None and item.ratings_count is None:
                        updates["ratings_count"] = meta["ratings_count"]
                    if meta.get("release_year") and not item.release_year:
                        updates["release_year"] = meta["release_year"]
                    genres = meta.get("genres") or []
                    if genres and not item.genres:
                        updates["genres"] = _json.dumps(genres)
        except Exception as e:
            logger.debug(f"HC enrichment failed for TBR item {item.id}: {e}")

    elif item.ol_work_key and not item.description:
        try:
            from src.api.open_library_client import OpenLibraryClient

            ol_client = OpenLibraryClient()
            details = ol_client.get_work_details(item.ol_work_key)
            if details:
                if details.get("description"):
                    updates["description"] = details["description"]
                subjects = details.get("subjects") or []
                if subjects and not item.genres:
                    updates["genres"] = _json.dumps(subjects[:8])
        except Exception as e:
            logger.debug(f"OL enrichment failed for TBR item {item.id}: {e}")

    if updates:
        return database_service.update_tbr_item(item.id, **updates)
    return None


def _pull_started_at(book_id):
    """Pull started_at from Hardcover/ABS before falling back to today."""
    dates = get_container().reading_date_service().pull_reading_dates(book_id)
    return dates.get("started_at")


# ── Endpoints ────────────────────────────────────────────────────────


@tbr_bp.route("/api/reading/tbr", methods=["GET"])
def get_tbr_items():
    """Get all TBR items."""
    database_service = get_database_service()
    items = database_service.get_tbr_items()
    return jsonify([_serialize_tbr_item(item) for item in items])


@tbr_bp.route("/api/reading/tbr/from-library", methods=["POST"])
def add_tbr_from_library():
    """Create a TBR item pre-linked to an existing library book."""
    database_service = get_database_service()
    data = request.json or {}
    book_ref = (data.get("abs_id") or data.get("book_ref") or "").strip()
    if not book_ref:
        return json_error("Book reference is required", 400)

    book = database_service.get_book_by_ref(book_ref)
    if not book:
        return json_error("Book not found", 404)

    # Dedup: if TBR item already linked to this book, return it
    existing = database_service.find_tbr_by_book_id(book.id)
    if existing:
        return jsonify({"success": True, "created": False, "item": _serialize_tbr_item(existing)})

    item, created = database_service.add_tbr_item(
        title=book.title or book_ref,
        author=book.author,
        source="library",
        book_abs_id=book.abs_id,
        book_id=book.id,
    )

    return jsonify({"success": True, "created": created, "item": _serialize_tbr_item(item)})


@tbr_bp.route("/api/reading/tbr/enrich", methods=["POST"])
def enrich_tbr_items():
    """Backfill enrichment metadata for items that lack it.

    Processes up to 10 items per call to avoid timeouts.
    Returns the count of enriched items and remaining items needing enrichment.
    """
    database_service = get_database_service()
    items = database_service.get_tbr_items()

    needs_enrichment = [item for item in items if not item.description and (item.hardcover_book_id or item.ol_work_key)]

    enriched_count = 0
    batch = needs_enrichment[:10]
    for item in batch:
        result = _enrich_tbr_item(item, {}, database_service)
        if result:
            enriched_count += 1

    remaining = len(needs_enrichment) - len(batch)
    return jsonify(
        {
            "success": True,
            "enriched": enriched_count,
            "remaining": remaining,
        }
    )


@tbr_bp.route("/api/reading/tbr/add", methods=["POST"])
def add_tbr_item():
    """Add a book to the TBR list from search result or manual entry."""
    database_service = get_database_service()
    data = request.json or {}

    title = (data.get("title") or "").strip()
    if not title:
        return json_error("Title is required", 400)

    # Determine source from which fields are present
    source = "manual"
    if data.get("hardcover_book_id"):
        source = "hardcover_search"
    elif data.get("ol_work_key"):
        source = "open_library"

    # Auto-link to existing Book via HardcoverDetails if HC book_id provided
    book_abs_id = None
    hc_book_id = data.get("hardcover_book_id")
    if hc_book_id:
        try:
            hc_book_id = int(hc_book_id)
            all_hc = database_service.get_all_hardcover_details()
            for hc in all_hc:
                if hc.hardcover_book_id and str(hc.hardcover_book_id) == str(hc_book_id):
                    book_abs_id = hc.abs_id
                    break
        except (TypeError, ValueError):
            hc_book_id = None

    # Collect enrichment fields from the search result payload
    enrichment = {}
    for field in ("page_count", "rating", "ratings_count", "release_year", "subtitle"):
        val = data.get(field)
        if val is not None:
            enrichment[field] = val
    # Genres: passed as list from search, store as JSON string
    raw_genres = data.get("genres")
    if isinstance(raw_genres, list) and raw_genres:
        enrichment["genres"] = _json.dumps(raw_genres)

    item, created = database_service.add_tbr_item(
        title=title,
        author=(data.get("author") or "").strip() or None,
        cover_url=data.get("cover_url"),
        notes=(data.get("notes") or "").strip() or None,
        source=source,
        hardcover_book_id=hc_book_id,
        hardcover_slug=data.get("hardcover_slug"),
        ol_work_key=data.get("ol_work_key"),
        isbn=data.get("isbn"),
        book_abs_id=book_abs_id,
        **enrichment,
    )

    # Push "Want to Read" to Hardcover if this is a new HC-sourced item
    if created and hc_book_id:
        try:
            hc_client = get_container().hardcover_client()
            if hc_client.is_configured():
                hc_client.update_status(hc_book_id, HC_WANT_TO_READ)
        except Exception as e:
            logger.debug(f"Could not push Want to Read to Hardcover: {e}")

    # Enrich with full metadata (description, genres, etc.)
    if created:
        enriched = _enrich_tbr_item(item, data, database_service)
        if enriched:
            item = enriched

    return jsonify(
        {
            "success": True,
            "created": created,
            "item": _serialize_tbr_item(item),
        }
    )


@tbr_bp.route("/api/reading/tbr/<int:item_id>", methods=["DELETE"])
def delete_tbr_item(item_id):
    """Remove a book from the TBR list.

    Query params:
        remove_from_hc: 'true' to also set Hardcover status to Ignored (6),
                        which removes it from Want to Read and prevents re-import.
    """
    database_service = get_database_service()
    item = database_service.get_tbr_item(item_id)
    if not item:
        return json_error("Item not found", 404)

    # Optionally push "Ignored" status to Hardcover before deleting locally
    hc_removed = False
    remove_from_hc = request.args.get("remove_from_hc") == "true"
    if remove_from_hc and item.hardcover_book_id:
        try:
            hc_client = get_container().hardcover_client()
            if hc_client.is_configured():
                result = hc_client.update_status(int(item.hardcover_book_id), HC_IGNORED)
                hc_removed = result is not None
                if hc_removed:
                    logger.info(f"Hardcover: set book {item.hardcover_book_id} to Ignored (removed from Want to Read)")
                else:
                    logger.warning(f"Hardcover rejected status 6 for book {item.hardcover_book_id}")
        except Exception as e:
            logger.warning(f"Could not remove from Hardcover: {e}")

    database_service.delete_tbr_item(item_id)
    return jsonify({"success": True, "hc_removed": hc_removed})


@tbr_bp.route("/api/reading/tbr/<int:item_id>", methods=["PATCH"])
def update_tbr_item(item_id):
    """Update fields on a TBR item (notes, cover, and metadata for any source)."""
    database_service = get_database_service()
    item = database_service.get_tbr_item(item_id)
    if not item:
        return json_error("Item not found", 404)

    data = request.json or {}
    if not data:
        return json_error("No fields to update", 400)

    allowed = {
        "notes",
        "priority",
        "title",
        "author",
        "cover_url",
        "description",
        "page_count",
        "release_year",
        "subtitle",
        "hardcover_book_id",
        "hardcover_slug",
    }

    updates = {}
    for key, value in data.items():
        if key in allowed:
            updates[key] = value

    if not updates:
        return json_error("No valid fields to update", 400)

    # Dedupe check: prevent reassigning hardcover_book_id to a duplicate
    new_hc_id = updates.get("hardcover_book_id")
    if new_hc_id and str(new_hc_id) != str(item.hardcover_book_id or ""):
        existing = database_service.find_tbr_by_hardcover_id(new_hc_id)
        if existing and existing.id != item_id:
            return json_error("Another TBR item already has this Hardcover ID", 409)

    updated = database_service.update_tbr_item(item_id, **updates)
    if not updated:
        return json_error("Update failed", 500)

    return jsonify({"success": True, "item": _serialize_tbr_item(updated)})


@tbr_bp.route("/api/reading/tbr/<int:item_id>/start", methods=["POST"])
def start_tbr_item(item_id):
    """Transition a TBR item to active reading (requires linked Book)."""
    database_service = get_database_service()
    item = database_service.get_tbr_item(item_id)
    if not item:
        return json_error("TBR item not found", 404)
    if not item.book_id and not item.book_abs_id:
        return json_error("Book not in library — cannot start reading", 400)

    book = (
        database_service.get_book_by_id(item.book_id)
        if item.book_id
        else database_service.get_book_by_abs_id(item.book_abs_id)
    )
    if not book:
        return json_error("Linked book not found", 404)

    # Transition book to active
    book.status = "active"
    book.activity_flag = False
    database_service.save_book(book)

    if not book.started_at:
        database_service.update_book_reading_fields(book.id, started_at=_pull_started_at(book.id))
    database_service.add_reading_journal(book.id, event="started", abs_id=book.abs_id)

    # Push to Hardcover
    try:
        container = get_container()
        hc_service = container.hardcover_service()
        if hc_service.is_configured():
            hc_service.push_local_status(book, "active")
    except Exception as e:
        logger.debug(f"Could not push active status to Hardcover: {e}")

    # Delete the TBR item — it has served its purpose
    database_service.delete_tbr_item(item_id)

    return jsonify({"success": True, "abs_id": book.abs_id})


@tbr_bp.route("/api/reading/tbr/search", methods=["POST"])
def search_tbr():
    """Search for books to add to TBR. Uses Hardcover or Open Library."""
    data = request.json or {}
    query = (data.get("query") or "").strip()
    if not query or len(query) < 2:
        return jsonify({"results": [], "provider": None})

    provider = data.get("provider")

    # Auto-select provider if not specified
    if not provider:
        try:
            hc_client = get_container().hardcover_client()
            provider = "hardcover" if hc_client.is_configured() else "open_library"
        except Exception:
            provider = "open_library"

    results = []

    if provider == "hardcover":
        try:
            hc_client = get_container().hardcover_client()
            if hc_client.is_configured():
                hc_results = hc_client.search_books_with_covers(query, limit=15)
                results = [
                    {
                        "title": r.get("title", ""),
                        "author": r.get("author", ""),
                        "cover_url": r.get("cached_image"),
                        "provider": "hardcover",
                        "hardcover_book_id": r.get("book_id"),
                        "hardcover_slug": r.get("slug"),
                        "ol_work_key": None,
                        "isbn": None,
                        "page_count": r.get("pages"),
                        "rating": r.get("rating"),
                        "release_year": r.get("release_year"),
                    }
                    for r in hc_results
                ]
        except Exception as e:
            logger.warning(f"Hardcover search failed, falling back to Open Library: {e}")
            provider = "open_library"

    if provider == "open_library":
        try:
            from src.api.open_library_client import OpenLibraryClient

            ol_client = OpenLibraryClient()
            ol_results = ol_client.search_books(query, limit=10)
            results = [
                {
                    "title": r.get("title", ""),
                    "author": r.get("author", ""),
                    "cover_url": r.get("cover_url"),
                    "provider": "open_library",
                    "hardcover_book_id": None,
                    "hardcover_slug": None,
                    "ol_work_key": r.get("ol_work_key"),
                    "isbn": r.get("isbn"),
                    "page_count": r.get("page_count"),
                    "rating": r.get("rating"),
                    "ratings_count": r.get("ratings_count"),
                    "release_year": r.get("first_publish_year"),
                    "genres": r.get("genres"),
                }
                for r in ol_results
            ]
        except Exception as e:
            logger.error(f"Open Library search failed: {e}")

    return jsonify({"results": results, "provider": provider})


@tbr_bp.route("/api/reading/tbr/import-hardcover", methods=["POST"])
def import_hardcover_wtr():
    """Bulk import all Hardcover 'Want to Read' books into TBR."""
    try:
        hc_client = get_container().hardcover_client()
        if not hc_client.is_configured():
            return json_error("Hardcover not configured", 400)
    except Exception:
        return json_error("Hardcover not available", 400)

    database_service = get_database_service()
    wtr_books = hc_client.get_want_to_read_books()

    # Pre-fetch all HC details for auto-linking
    all_hc_details = database_service.get_all_hardcover_details()
    hc_id_to_abs = {}
    for hc in all_hc_details:
        if hc.hardcover_book_id:
            hc_id_to_abs[str(hc.hardcover_book_id)] = hc.abs_id

    # Skip books already being read or finished in the local library
    library_books = {b.abs_id: b for b in database_service.get_all_books()}
    already_reading = {
        hc_id
        for hc_id, abs_id in hc_id_to_abs.items()
        if abs_id in library_books and library_books[abs_id].status in ("active", "completed", "paused", "dnf")
    }

    imported = 0
    skipped = 0
    filtered = 0
    for book in wtr_books:
        hc_book_id = book.get("book_id")
        if not hc_book_id:
            continue

        if str(hc_book_id) in already_reading:
            filtered += 1
            continue

        book_abs_id = hc_id_to_abs.get(str(hc_book_id))

        item, created = database_service.add_tbr_item(
            title=book.get("title", ""),
            author=book.get("author"),
            cover_url=book.get("cached_image"),
            source="hardcover_wtr",
            hardcover_book_id=hc_book_id,
            hardcover_slug=book.get("slug"),
            book_abs_id=book_abs_id,
            page_count=book.get("pages"),
            rating=book.get("rating"),
            release_year=book.get("release_year"),
        )
        if created:
            imported += 1
        else:
            skipped += 1

    return jsonify({"success": True, "imported": imported, "skipped": skipped, "filtered": filtered})


@tbr_bp.route("/api/reading/tbr/hardcover-lists", methods=["GET"])
def get_hardcover_lists():
    """Fetch the user's Hardcover custom lists for the import picker."""
    try:
        hc_client = get_container().hardcover_client()
        if not hc_client.is_configured():
            return jsonify([])
    except Exception:
        return jsonify([])

    lists = hc_client.get_user_lists()
    return jsonify(
        [
            {
                "id": lst["id"],
                "name": lst["name"],
                "description": lst.get("description", ""),
                "books_count": lst.get("books_count", 0),
            }
            for lst in lists
        ]
    )


@tbr_bp.route("/api/reading/tbr/import-hardcover-list", methods=["POST"])
def import_hardcover_list():
    """Import books from a specific Hardcover custom list into TBR."""
    data = request.json or {}
    list_id = data.get("list_id")
    if not list_id:
        return json_error("list_id is required", 400)

    try:
        list_id = int(list_id)
    except (TypeError, ValueError):
        return json_error("Invalid list_id", 400)

    try:
        hc_client = get_container().hardcover_client()
        if not hc_client.is_configured():
            return json_error("Hardcover not configured", 400)
    except Exception:
        return json_error("Hardcover not available", 400)

    database_service = get_database_service()
    list_data = hc_client.get_list_books(list_id)
    if not list_data:
        return json_error("List not found", 404)

    list_name = list_data.get("name", "")

    # Pre-fetch HC details for auto-linking
    all_hc_details = database_service.get_all_hardcover_details()
    hc_id_to_abs = {}
    for hc in all_hc_details:
        if hc.hardcover_book_id:
            hc_id_to_abs[str(hc.hardcover_book_id)] = hc.abs_id

    # Skip books already being read or finished in the local library
    library_books = {b.abs_id: b for b in database_service.get_all_books()}
    already_reading = {
        hc_id
        for hc_id, abs_id in hc_id_to_abs.items()
        if abs_id in library_books and library_books[abs_id].status in ("active", "completed", "paused", "dnf")
    }

    imported = 0
    skipped = 0
    filtered = 0
    for book in list_data.get("books", []):
        hc_book_id = book.get("book_id")
        if not hc_book_id:
            continue

        if str(hc_book_id) in already_reading:
            filtered += 1
            continue

        book_abs_id = hc_id_to_abs.get(str(hc_book_id))

        item, created = database_service.add_tbr_item(
            title=book.get("title", ""),
            author=book.get("author"),
            cover_url=book.get("cached_image"),
            source="hardcover_list",
            hardcover_book_id=hc_book_id,
            hardcover_slug=book.get("slug"),
            hardcover_list_id=list_id,
            hardcover_list_name=list_name,
            book_abs_id=book_abs_id,
            page_count=book.get("pages"),
            rating=book.get("rating"),
            release_year=book.get("release_year"),
        )
        if created:
            imported += 1
        else:
            skipped += 1

    return jsonify(
        {
            "success": True,
            "imported": imported,
            "skipped": skipped,
            "filtered": filtered,
            "list_name": list_name,
        }
    )


@tbr_bp.route("/api/reading/library-search", methods=["GET"])
def search_library_books():
    """Search library books by title for linking to TBR items."""
    database_service = get_database_service()
    q = (request.args.get("q") or "").strip()
    if not q or len(q) < 2:
        return jsonify([])

    books = database_service.search_books(q, limit=10)
    return jsonify(
        [
            {
                "id": b.id,
                "abs_id": b.abs_id,
                "title": b.title,
                "author": getattr(b, "author", None) or "",
                "status": b.status,
            }
            for b in books
        ]
    )


@tbr_bp.route("/api/reading/tbr/<int:item_id>/link", methods=["POST"])
def link_tbr_to_library(item_id):
    """Link a TBR item to a library book."""
    database_service = get_database_service()
    data = request.json or {}
    book_ref = (data.get("abs_id") or data.get("book_ref") or "").strip()
    if not book_ref:
        return json_error("Book reference is required", 400)

    book = database_service.get_book_by_ref(book_ref)
    if not book:
        return json_error("Book not found in library", 404)

    updated = database_service.link_tbr_to_book(item_id, book.id)
    if not updated:
        return json_error("TBR item not found", 404)

    return jsonify({"success": True})


@tbr_bp.route("/api/reading/tbr/<int:item_id>/link", methods=["DELETE"])
def unlink_tbr_from_library(item_id):
    """Unlink a TBR item from its library book."""
    database_service = get_database_service()
    updated = database_service.link_tbr_to_book(item_id, None)
    if not updated:
        return json_error("TBR item not found", 404)

    return jsonify({"success": True})
