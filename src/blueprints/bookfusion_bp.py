"""BookFusion blueprint — upload books and sync highlights."""

import logging
from datetime import datetime

from flask import Blueprint, jsonify, request

from src.blueprints.helpers import get_container, get_database_service
from src.db.models import BookfusionBook

logger = logging.getLogger(__name__)

bookfusion_bp = Blueprint("bookfusion", __name__)


@bookfusion_bp.route("/api/bookfusion/upload", methods=["POST"])
def upload_book():
    """Upload a book from PageKeeper to BookFusion."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    abs_id = data.get("abs_id")
    if not abs_id:
        return jsonify({"error": "abs_id required"}), 400

    container = get_container()
    bf_client = container.bookfusion_client()

    if not bf_client.upload_api_key:
        return jsonify({"error": "BookFusion upload API key not configured"}), 400

    db_service = get_database_service()
    book = db_service.get_book_by_ref(abs_id)
    if not book:
        return jsonify({"error": "Book not found"}), 404

    if not book.ebook_filename and not book.original_ebook_filename:
        return jsonify({"error": "No ebook file associated with this book"}), 400

    ebook_filename = book.original_ebook_filename or book.ebook_filename

    from src.utils.epub_resolver import get_local_epub

    books_dir = container.config.get("BOOKS_DIR") or "/books"
    epub_cache_dir = container.config.get("EPUB_CACHE_DIR") or "/tmp/epub_cache"
    grimmory_client = container.grimmory_client() if hasattr(container, "grimmory_client") else None

    file_path = get_local_epub(ebook_filename, books_dir, epub_cache_dir, grimmory_client)
    if not file_path:
        return jsonify({"error": "Could not locate ebook file"}), 500

    try:
        with open(file_path, "rb") as f:
            file_bytes = f.read()
    except Exception as e:
        logger.error(f"Failed to read ebook file: {e}")
        return jsonify({"error": "Failed to read ebook file"}), 500

    title = book.title or ""
    authors = book.author or ""

    logger.info(f"BookFusion upload request: title='{title}', authors='{authors}', filename='{ebook_filename}'")
    result = bf_client.upload_book(ebook_filename, file_bytes, title, authors)
    if not result:
        return jsonify({"error": "Upload to BookFusion failed"}), 500

    bf_book_id = result.get("id")
    if bf_book_id:
        db_service.save_bookfusion_book(
            BookfusionBook(
                bookfusion_id=bf_book_id,
                title=title,
                authors=authors,
                filename=ebook_filename,
                matched_book_id=book.id,
                matched_abs_id=abs_id,
            )
        )

    return jsonify({"success": True, "result": result})


@bookfusion_bp.route("/api/bookfusion/sync-highlights", methods=["POST"])
def sync_highlights():
    """Trigger highlight sync from BookFusion for a specific book or all books."""
    container = get_container()
    bf_client = container.bookfusion_client()
    db_service = get_database_service()

    if not bf_client.highlights_api_key:
        return jsonify({"error": "BookFusion highlights API key not configured"}), 400

    data = request.get_json(silent=True) or {}

    if data.get("full_resync"):
        db_service.set_bookfusion_sync_cursor(None)

    try:
        result = bf_client.sync_all_highlights(db_service)
        return jsonify(
            {
                "success": True,
                "new_highlights": result["new_highlights"],
                "books_saved": result["books_saved"],
                "new_ids": result.get("new_ids", []),
            }
        )
    except Exception:
        logger.exception("BookFusion highlight sync failed")
        return jsonify({"error": "BookFusion highlight sync failed"}), 500


@bookfusion_bp.route("/api/bookfusion/sync-book", methods=["POST"])
def sync_book_highlights():
    """Sync highlights for a specific book from BookFusion."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    abs_id = data.get("abs_id")
    if not abs_id:
        return jsonify({"error": "abs_id required"}), 400

    db_service = get_database_service()
    book = db_service.get_book_by_ref(abs_id)
    if not book:
        return jsonify({"error": "Book not found"}), 404

    container = get_container()
    bf_client = container.bookfusion_client()

    if not bf_client.highlights_api_key:
        return jsonify({"error": "BookFusion highlights API key not configured"}), 400

    bf_books = db_service.get_bookfusion_books_by_book_id(book.id)
    if not bf_books:
        return jsonify({"error": "BookFusion link not found for this book"}), 404

    bf_book_ids = [b.bookfusion_id for b in bf_books]

    try:
        result = bf_client.sync_all_highlights(db_service)

        linked_count = 0
        for bf_id in bf_book_ids:
            db_service.link_bookfusion_highlights_by_book_id(bf_id, book.id)
            linked_count += 1

        return jsonify(
            {
                "success": True,
                "new_highlights": result["new_highlights"],
                "books_saved": result["books_saved"],
                "linked_books": linked_count,
            }
        )
    except Exception:
        logger.exception("BookFusion highlight sync failed for book")
        return jsonify({"error": "BookFusion highlight sync failed"}), 500


@bookfusion_bp.route("/api/bookfusion/save-journal", methods=["POST"])
def save_highlight_to_journal():
    """Save BookFusion highlights as reading journal entries for a book."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    abs_id = data.get("abs_id")
    highlights = data.get("highlights", [])

    if not abs_id:
        return jsonify({"error": "abs_id required"}), 400

    db_service = get_database_service()
    book = db_service.get_book_by_ref(abs_id)
    if not book:
        return jsonify({"error": "Book not found"}), 404

    if not highlights:
        bf_highlights = db_service.get_bookfusion_highlights_for_book_by_book_id(book.id)
        if not bf_highlights:
            return jsonify({"error": "No highlights found for this book"}), 400
        highlights = []
        for hl in bf_highlights:
            highlights.append(
                {
                    "quote": hl.quote_text or hl.content,
                    "chapter": hl.chapter_heading or "",
                    "highlighted_at": hl.highlighted_at.strftime("%Y-%m-%d %H:%M:%S") if hl.highlighted_at else "",
                }
            )

    existing_entries = db_service.get_reading_journal_entries_for_book(book.id, "highlight")
    existing_keys = set()
    for entry in existing_entries:
        if entry.entry:
            existing_keys.add(entry.entry.strip())

    cleanup_stats = db_service.cleanup_bookfusion_import_notes(abs_id)
    saved = 0
    skipped = 0

    for hl in highlights:
        quote = hl.get("quote", "").strip()
        chapter = hl.get("chapter", "").strip()
        highlighted_at_raw = (hl.get("highlighted_at") or "").strip()

        if not quote:
            continue

        entry_text = quote
        if chapter:
            entry_text += f"\n— {chapter}"

        if entry_text in existing_keys:
            skipped += 1
            continue

        created_at = None
        if highlighted_at_raw:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%b %d, %Y"):
                try:
                    created_at = datetime.strptime(highlighted_at_raw, fmt)
                    break
                except ValueError:
                    continue
            if not created_at:
                logger.debug("Could not parse BookFusion highlight timestamp '%s'", highlighted_at_raw)

        try:
            db_service.add_reading_journal(
                book.id, "highlight", entry=entry_text, created_at=created_at, abs_id=book.abs_id
            )
            saved += 1
        except Exception as e:
            logger.warning(f"Failed to save journal entry: {e}")

    return jsonify({"success": True, "saved": saved, "skipped": skipped, "cleanup": cleanup_stats})
