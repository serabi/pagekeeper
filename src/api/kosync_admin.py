# KoSync Admin — Dashboard management routes for KoSync documents
# These routes are LAN-only (port 4477), never exposed to the internet sync port.
import logging
import os

from flask import Blueprint, current_app, jsonify, render_template, request

from src.api.kosync_auth import admin_or_local_required
from src.utils.path_utils import is_safe_path_within

logger = logging.getLogger(__name__)

kosync_admin_bp = Blueprint("kosync_admin", __name__)


def _get_db():
    return current_app.config["database_service"]


def _get_svc():
    return current_app.config["kosync_service"]


def _get_container():
    return current_app.config["container"]


def _serialize_document(doc):
    """Convert a KosyncDocument to a JSON-safe dict."""
    db = _get_db()
    linked_book = None
    if doc.linked_book_id:
        linked_book = db.get_book_by_id(doc.linked_book_id)
    return {
        "document_hash": doc.document_hash,
        "progress": doc.progress,
        "percentage": float(doc.percentage) if doc.percentage else 0,
        "device": doc.device,
        "device_id": doc.device_id,
        "timestamp": doc.timestamp.isoformat() if doc.timestamp else None,
        "first_seen": doc.first_seen.isoformat() if doc.first_seen else None,
        "last_updated": doc.last_updated.isoformat() if doc.last_updated else None,
        "linked_book_id": doc.linked_book_id,
        "linked_abs_id": doc.linked_abs_id,
        "linked_book_title": linked_book.title if linked_book else None,
    }


def _cleanup_cache_for_hash(doc_hash):
    """Delete cached EPUB file for a document."""
    try:
        db = _get_db()
        doc = db.get_kosync_document(doc_hash)
        filename = doc.filename if doc else None

        if not filename and doc and doc.linked_abs_id:
            book = db.get_book_by_abs_id(doc.linked_abs_id)
            if book:
                filename = book.original_ebook_filename or book.ebook_filename

        if filename:
            container = _get_container()
            if container:
                cache_dir = container.data_dir() / "epub_cache"
                file_path = cache_dir / filename
                if not is_safe_path_within(file_path, cache_dir):
                    logger.warning(f"Blocked cache deletion — path escapes cache dir: '{filename}'")
                elif file_path.exists():
                    try:
                        os.remove(file_path)
                        logger.info(f"Deleted cached EPUB: {filename}")
                    except Exception as e:
                        logger.warning(f"Failed to delete cached file '{filename}': {e}")

    except Exception as e:
        logger.error(f"Error cleaning up cache for '{doc_hash}': {e}")


# ---------------- KOSync Document Management API ----------------


@kosync_admin_bp.route("/api/kosync-documents", methods=["GET"])
@admin_or_local_required
def api_get_kosync_documents():
    """Get all KOSync documents with their link status."""
    docs = _get_db().get_all_kosync_documents()
    result = [_serialize_document(doc) for doc in docs]
    return jsonify(
        {
            "documents": result,
            "total": len(result),
            "linked": sum(1 for d in result if d["linked_book_id"]),
            "unlinked": sum(1 for d in result if not d["linked_book_id"]),
        }
    )


@kosync_admin_bp.route("/api/kosync-documents/<doc_hash>/link", methods=["POST"])
@admin_or_local_required
def api_link_kosync_document(doc_hash):
    """Link a KOSync document to a book (by abs_id or book_id)."""
    db = _get_db()
    data = request.json
    if not data:
        return jsonify({"error": "Missing request body"}), 400

    book = None
    if data.get("abs_id"):
        book = db.get_book_by_abs_id(data["abs_id"])
    elif data.get("book_id"):
        book = db.get_book_by_id(data["book_id"])
    else:
        return jsonify({"error": "Missing abs_id or book_id"}), 400

    if not book:
        return jsonify({"error": "Book not found"}), 404

    doc = db.get_kosync_document(doc_hash)
    if not doc:
        return jsonify({"error": "KOSync document not found"}), 404

    success = db.link_kosync_document(doc_hash, book.id, book.abs_id)
    if success:
        current_id = book.kosync_doc_id
        if current_id != doc_hash:
            logger.info(f"Updating Book {book.title} KOSync ID: {current_id} -> {doc_hash}")
            book.kosync_doc_id = doc_hash
            db.save_book(book)

        db.resolve_suggestion(doc_hash)
        return jsonify({"success": True, "message": f"Linked to {book.title}"})

    return jsonify({"error": "Failed to link document"}), 500


@kosync_admin_bp.route("/api/kosync-documents/<doc_hash>/unlink", methods=["POST"])
@admin_or_local_required
def api_unlink_kosync_document(doc_hash):
    """Remove the ABS book link from a KOSync document."""
    db = _get_db()
    doc = db.get_kosync_document(doc_hash)
    if doc and doc.linked_book_id:
        book = db.get_book_by_id(doc.linked_book_id)
        if book and book.kosync_doc_id == doc_hash:
            book.kosync_doc_id = None
            db.save_book(book)

    success = db.unlink_kosync_document(doc_hash)
    if success:
        _cleanup_cache_for_hash(doc_hash)
        return jsonify({"success": True, "message": "Document unlinked"})
    return jsonify({"error": "Document not found"}), 404


@kosync_admin_bp.route("/api/kosync-documents/<doc_hash>", methods=["DELETE"])
@admin_or_local_required
def api_delete_kosync_document(doc_hash):
    """Delete a KOSync document."""
    db = _get_db()
    doc = db.get_kosync_document(doc_hash)
    if doc and doc.linked_book_id:
        book = db.get_book_by_id(doc.linked_book_id)
        if book and book.kosync_doc_id == doc_hash:
            book.kosync_doc_id = None
            db.save_book(book)

    _cleanup_cache_for_hash(doc_hash)
    success = db.delete_kosync_document(doc_hash)
    if success:
        return jsonify({"success": True, "message": "Document deleted"})
    return jsonify({"error": "Document not found"}), 404


# ---------------- KOSync Document Management Page ----------------


@kosync_admin_bp.route("/kosync-documents")
@admin_or_local_required
def kosync_documents_page():
    """Render the KoSync Document Management page."""
    db = _get_db()
    svc = _get_svc()
    docs = db.get_all_kosync_documents()
    documents = [_serialize_document(doc) for doc in docs]

    orphaned = svc.get_orphaned_kosync_books()
    orphaned_books = [
        {
            "book_id": b.id,
            "abs_id": b.abs_id,
            "title": b.title,
            "kosync_doc_id": b.kosync_doc_id,
            "status": b.status,
            "sync_mode": b.sync_mode,
        }
        for b in orphaned
    ]

    return render_template("kosync_documents.html", documents=documents, orphaned_books=orphaned_books)


@kosync_admin_bp.route("/api/kosync-documents/orphaned", methods=["GET"])
@admin_or_local_required
def api_get_orphaned_kosync_books():
    """Get books with kosync_doc_id set but no matching KosyncDocument."""
    orphaned = _get_svc().get_orphaned_kosync_books()
    return jsonify(
        [
            {
                "book_id": b.id,
                "abs_id": b.abs_id,
                "title": b.title,
                "kosync_doc_id": b.kosync_doc_id,
                "status": b.status,
                "sync_mode": b.sync_mode,
            }
            for b in orphaned
        ]
    )


@kosync_admin_bp.route("/api/kosync-documents/clear-orphan/<int:book_id>", methods=["POST"])
@admin_or_local_required
def api_clear_orphaned_hash(book_id):
    """Clear kosync_doc_id from a book to stop 502 cycle."""
    book = _get_svc().clear_orphaned_hash(book_id)
    if book:
        return jsonify({"success": True, "message": f"Cleared hash from {book.title}"})
    return jsonify({"error": "Book not found"}), 404


@kosync_admin_bp.route("/api/kosync-documents/resolve-orphan/<int:book_id>", methods=["POST"])
@admin_or_local_required
def api_resolve_orphaned_hash(book_id):
    """Create a KosyncDocument for an orphaned hash and link it to a book."""
    db = _get_db()
    svc = _get_svc()

    source_book = db.get_book_by_id(book_id)
    if not source_book or not source_book.kosync_doc_id:
        return jsonify({"error": "Book not found or has no hash"}), 404

    doc_hash = source_book.kosync_doc_id
    data = request.json or {}
    target_book_id = data.get("target_book_id")

    if target_book_id:
        target_book = db.get_book_by_id(target_book_id)
        if not target_book:
            return jsonify({"error": "Target book not found"}), 404
        svc.register_hash_for_book(doc_hash, target_book)
        source_book.kosync_doc_id = None
        target_book.kosync_doc_id = doc_hash
        db.save_book(source_book)
        db.save_book(target_book)
        return jsonify({"success": True, "message": f"Linked hash to {target_book.title}"})

    svc.register_hash_for_book(doc_hash, source_book)
    return jsonify({"success": True, "message": f"Linked hash to {source_book.title}"})


@kosync_admin_bp.route("/api/kosync-documents/<doc_hash>/create-book", methods=["POST"])
@admin_or_local_required
def api_create_book_from_hash(doc_hash):
    """Create an ebook-only book from an unlinked KoSync document."""
    db = _get_db()
    svc = _get_svc()
    data = request.json
    if not data or not data.get("title", "").strip():
        return jsonify({"error": "Title is required"}), 400

    doc = db.get_kosync_document(doc_hash)
    if not doc:
        return jsonify({"error": "KoSync document not found"}), 404

    title = data["title"].strip()
    book = svc.create_ebook_only_book(doc_hash, title, doc.filename)
    return jsonify(
        {
            "success": True,
            "message": f'Created book "{book.title}"',
            "book_id": book.id,
        }
    )
