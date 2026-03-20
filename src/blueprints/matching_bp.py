"""Matching blueprint — suggestions, single match, batch match."""

import json
import logging
import os
import threading
from pathlib import Path

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from src.blueprints.helpers import (
    any_booklore_configured,
    attempt_hardcover_automatch,
    audiobook_matches_search,
    find_in_booklore,
    get_abs_service,
    get_audiobook_author,
    get_audiobooks_conditionally,
    get_container,
    get_database_service,
    get_ebook_dir,
    get_kosync_id_for_ebook,
    get_manager,
    get_searchable_ebooks,
    serialize_suggestion,
)
from src.db.models import Book, StorytellerSubmission
from src.utils.logging_utils import sanitize_log_data
from src.utils.path_utils import sanitize_filename

logger = logging.getLogger(__name__)

matching_bp = Blueprint("matching", __name__)


def _create_storyteller_reservation(database_service, abs_id):
    """Create a submission record synchronously so the job scheduler knows to defer.

    This prevents a race condition where the background job picks up the book
    and starts Whisper transcription before the async submission thread has
    finished copying files and creating its own record.
    """
    book = database_service.get_book_by_ref(abs_id)
    storyteller_uuid = book.storyteller_uuid if book else None
    submission = StorytellerSubmission(abs_id=abs_id, book_id=book.id if book else None, status="queued", storyteller_uuid=storyteller_uuid)
    database_service.save_storyteller_submission(submission)
    return submission


def _submit_to_storyteller_async(container, abs_id, book_title, ebook_filename, books_dir, epub_cache_dir):
    """Submit a book to Storyteller in a background thread so the response isn't blocked."""

    def _do_submit():
        try:
            st_sub_svc = container.storyteller_submission_service()
            if not st_sub_svc.is_available():
                logger.warning(f"Storyteller submission skipped for '{book_title}': service not available")
                return
            from src.utils.epub_resolver import get_local_epub

            epub_path = get_local_epub(ebook_filename, books_dir, epub_cache_dir, container.booklore_client())
            audio_files = container.abs_client().get_audio_files(abs_id)
            if epub_path and audio_files:
                result = st_sub_svc.submit_book(abs_id, book_title, Path(epub_path), audio_files)
                if not result.success:
                    logger.warning(f"Storyteller submission failed for '{book_title}': {result.error}")
            else:
                logger.warning(
                    f"Storyteller submission skipped for '{book_title}': "
                    f"epub={'found' if epub_path else 'missing'}, audio={len(audio_files or [])} files"
                )
        except Exception as e:
            logger.warning(f"Storyteller submission error for '{book_title}': {e}")
            try:
                db_svc = container.database_service()
                book = db_svc.get_book_by_abs_id(abs_id)
                submission = db_svc.get_active_storyteller_submission_by_book_id(book.id) if book else None
                if submission:
                    db_svc.update_storyteller_submission_status(submission.id, "failed")
            except Exception:
                pass

    threading.Thread(target=_do_submit, daemon=True).start()


def _copy_book_merge_metadata(existing_book, overrides=None):
    metadata = {
        "storyteller_uuid": existing_book.storyteller_uuid,
        "original_ebook_filename": existing_book.original_ebook_filename,
        "abs_ebook_item_id": existing_book.abs_ebook_item_id,
        "ebook_item_id": existing_book.ebook_item_id or existing_book.abs_ebook_item_id,
        "custom_cover_url": existing_book.custom_cover_url,
        "started_at": existing_book.started_at,
        "finished_at": existing_book.finished_at,
        "rating": existing_book.rating,
        "read_count": existing_book.read_count or 1,
    }
    if overrides:
        metadata.update({key: value for key, value in overrides.items() if value is not None})
    return metadata


def _create_book_mapping(container, abs_id, title, ebook_filename, duration,
                         storyteller_uuid=None, storyteller_submit=False,
                         author=None, subtitle=None):
    """Create a book mapping with full pipeline: Booklore, KOSync, merge, Hardcover, etc.

    Returns (book, error_message). On success error_message is None.
    On failure book is None and error_message describes the problem.
    """
    database_service = get_database_service()
    abs_service = get_abs_service()

    # Booklore lookup
    booklore_id = None
    bl_match, bl_match_client = find_in_booklore(ebook_filename)
    if bl_match:
        booklore_id = bl_match.get("id")

    # KOSync ID
    kosync_doc_id = get_kosync_id_for_ebook(ebook_filename, booklore_id, bl_client=bl_match_client)
    if not kosync_doc_id:
        logger.warning(f"Cannot compute KOSync ID for '{sanitize_log_data(ebook_filename)}'")
        return None, "Could not compute KOSync ID for ebook"

    # Hash preservation
    current_book_entry = database_service.get_book_by_ref(abs_id)
    if current_book_entry and current_book_entry.kosync_doc_id:
        logger.info(f"Preserving existing hash '{current_book_entry.kosync_doc_id}' for '{abs_id}'")
        kosync_doc_id = current_book_entry.kosync_doc_id

    # Duplicate merge detection
    existing_book = database_service.get_book_by_kosync_id(kosync_doc_id)
    migration_source_id = None
    original_ebook_filename = None

    if existing_book and existing_book.abs_id != abs_id:
        logger.info(f"Merging existing '{existing_book.abs_id}' into '{abs_id}'")
        migration_source_id = existing_book.abs_id
        ebook_item_id = existing_book.ebook_item_id or existing_book.abs_ebook_item_id or existing_book.abs_id
        original_ebook_filename = existing_book.original_ebook_filename or existing_book.ebook_filename
        merge_metadata = _copy_book_merge_metadata(
            existing_book,
            {
                "abs_ebook_item_id": ebook_item_id,
                "ebook_item_id": ebook_item_id,
                "original_ebook_filename": original_ebook_filename,
                "storyteller_uuid": storyteller_uuid or existing_book.storyteller_uuid,
            },
        )
    else:
        merge_metadata = {
            "storyteller_uuid": storyteller_uuid,
            "original_ebook_filename": None,
            "abs_ebook_item_id": None,
            "ebook_item_id": None,
        }

    # Create book
    book = Book(
        abs_id=abs_id,
        title=title,
        ebook_filename=ebook_filename,
        kosync_doc_id=kosync_doc_id,
        transcript_file=None,
        status="pending",
        duration=duration,
        author=author,
        subtitle=subtitle,
        **merge_metadata,
    )
    database_service.save_book(book, is_new=True)

    # Storyteller reservation (before HTTP calls to prevent race)
    if storyteller_submit:
        _create_storyteller_reservation(database_service, abs_id)

    # Duplicate merge migration
    if migration_source_id:
        try:
            database_service.migrate_book_data(migration_source_id, abs_id)
            database_service.delete_book(existing_book.id)
            abs_service.add_to_collection(abs_id, current_app.config["ABS_COLLECTION_NAME"])
            logger.info(f"Successfully merged {migration_source_id} into {abs_id}")
        except Exception as e:
            logger.error(f"Failed to merge book data: {e}")
            raise

    # Hardcover automatch
    attempt_hardcover_automatch(container, book)

    # ABS collection add
    if not migration_source_id:
        abs_service.add_to_collection(abs_id, current_app.config["ABS_COLLECTION_NAME"])

    # Booklore shelf add
    if bl_match_client:
        shelf_filename = original_ebook_filename or ebook_filename
        try:
            bl_match_client.add_to_shelf(shelf_filename)
        except Exception as e:
            logger.warning(f"Booklore add_to_shelf failed for '{sanitize_log_data(shelf_filename)}': {e}")

    # Storyteller submission (background thread)
    if storyteller_submit:
        _submit_to_storyteller_async(
            container, abs_id, title, ebook_filename,
            current_app.config.get("BOOKS_DIR", ""),
            current_app.config.get("EPUB_CACHE_DIR", ""),
        )

    # Resolve suggestions
    database_service.resolve_suggestion(abs_id)
    database_service.resolve_suggestion(kosync_doc_id)
    try:
        device_doc = database_service.get_kosync_doc_by_filename(ebook_filename)
        if device_doc and device_doc.document_hash != kosync_doc_id:
            database_service.resolve_suggestion(device_doc.document_hash)
    except Exception as e:
        logger.warning(f"Failed to check/resolve device hash: {e}")

    return book, None


def _build_batch_queue_item(item):
    """Annotate queue entries with display-oriented fields without mutating session data."""
    ebook_label = item.get("ebook_display_name") or item.get("ebook_filename") or "Not selected"
    storyteller_selected = bool(item.get("storyteller_uuid"))
    storyteller_label = "Selected" if storyteller_selected else "None / Skip"

    if item.get("audio_only"):
        status_label = "Audio Only"
        status_kind = "audio-only"
    elif item.get("ebook_only"):
        status_label = "Ebook Only"
        status_kind = "ebook-only"
    elif item.get("abs_id") and item.get("ebook_filename"):
        status_label = "Ready"
        status_kind = "ready"
    else:
        status_label = "Incomplete"
        status_kind = "incomplete"

    return {
        **item,
        "ebook_label": ebook_label,
        "storyteller_label": storyteller_label,
        "storyteller_selected": storyteller_selected,
        "status_label": status_label,
        "status_kind": status_kind,
    }


def _build_batch_queue_view(queue):
    queue_items = [_build_batch_queue_item(item) for item in queue]
    return {
        "items": queue_items,
        "total_count": len(queue_items),
        "ready_count": sum(1 for item in queue_items if item["status_kind"] in {"ready", "audio-only", "ebook-only"}),
        "audio_only_count": sum(1 for item in queue_items if item["status_kind"] == "audio-only"),
        "ebook_only_count": sum(1 for item in queue_items if item["status_kind"] == "ebook-only"),
        "incomplete_count": sum(1 for item in queue_items if item["status_kind"] == "incomplete"),
    }


@matching_bp.route("/suggestions")
def suggestions():
    """Dedicated page for browsing and acting on pairing suggestions."""
    container = get_container()
    database_service = get_database_service()
    raw_suggestions = database_service.get_all_actionable_suggestions()
    suggestions_list = [serialize_suggestion(s) for s in raw_suggestions if s.matches]
    visible_count = sum(1 for s in suggestions_list if not s.get("hidden"))
    hidden_count = sum(1 for s in suggestions_list if s.get("hidden"))
    suggestions_enabled = current_app.config.get("SUGGESTIONS_ENABLED", False)
    bookfusion_enabled = container.bookfusion_client().is_configured()
    bookfusion_catalog_count = len(database_service.get_bookfusion_books()) if bookfusion_enabled else 0
    initial_search = request.args.get("search", "").strip()
    selected_source_id = request.args.get("source_id", "").strip()
    return render_template(
        "suggestions.html",
        suggestions=suggestions_list,
        visible_count=visible_count,
        hidden_count=hidden_count,
        suggestions_enabled=suggestions_enabled,
        bookfusion_enabled=bookfusion_enabled,
        bookfusion_catalog_count=bookfusion_catalog_count,
        suggestions_json=json.dumps(suggestions_list),
        initial_search=initial_search,
        selected_source_id=selected_source_id,
    )


@matching_bp.route("/match", methods=["GET", "POST"])
def match():
    container = get_container()
    manager = get_manager()
    database_service = get_database_service()

    if request.method == "POST":
        action = request.form.get("action", "")

        # --- Audio-only import (no ebook required) ---
        if action == "audio_only":
            abs_service = get_abs_service()
            if not abs_service.is_available():
                return "ABS is not configured", 400
            abs_id = request.form.get("audiobook_id")
            audiobooks = abs_service.get_audiobooks()
            selected_ab = next((ab for ab in audiobooks if ab["id"] == abs_id), None)
            if not selected_ab:
                return "Audiobook not found", 404
            book = Book(
                abs_id=abs_id,
                title=manager.get_audiobook_title(selected_ab),
                ebook_filename=None,
                kosync_doc_id=None,
                status="not_started",
                duration=manager.get_duration(selected_ab),
                sync_mode="audiobook",
                author=get_audiobook_author(selected_ab),
                subtitle=selected_ab.get("media", {}).get("metadata", {}).get("subtitle") or None,
            )
            database_service.save_book(book, is_new=True)
            abs_service.add_to_collection(abs_id, current_app.config["ABS_COLLECTION_NAME"])
            attempt_hardcover_automatch(container, book)
            database_service.resolve_suggestion(abs_id)
            return redirect(url_for("dashboard.index"))

        # --- Ebook-only import (no audiobook required) ---
        if action == "ebook_only":
            ebook_filename = sanitize_filename(request.form.get("ebook_filename"))
            ebook_display_name = request.form.get("ebook_display_name", "")
            storyteller_uuid = request.form.get("storyteller_uuid") or None
            storyteller_title = request.form.get("storyteller_title", "")

            if not ebook_filename and not storyteller_uuid:
                return "An ebook or Storyteller selection is required", 400

            if ebook_filename:
                # Ebook present (possibly with Storyteller too)
                booklore_id = None
                matched_bl_client = None
                bl_book, matched_bl_client = find_in_booklore(ebook_filename)
                if bl_book:
                    booklore_id = bl_book.get("id")
                kosync_doc_id = get_kosync_id_for_ebook(ebook_filename, booklore_id, bl_client=matched_bl_client)
                if not kosync_doc_id:
                    return "Could not compute KOSync ID for ebook", 404
                title = ebook_display_name or (bl_book.get("title") if bl_book else None) or Path(ebook_filename).stem
            else:
                # Storyteller-only (no ebook file)
                title = storyteller_title or ebook_display_name or "Storyteller Book"
                ebook_filename = None
                kosync_doc_id = None

            book = Book(
                abs_id=None,
                title=title,
                ebook_filename=ebook_filename,
                kosync_doc_id=kosync_doc_id,
                status="not_started",
                sync_mode="ebook_only",
                storyteller_uuid=storyteller_uuid,
            )
            database_service.save_book(book, is_new=True)
            if kosync_doc_id:
                database_service.resolve_suggestion(kosync_doc_id)
            return redirect(url_for("dashboard.index"))

        # --- Attach ebook to audio-only book ---
        if action == "attach_ebook":
            attach_abs_id = request.form.get("attach_abs_id")
            ebook_filename = sanitize_filename(request.form.get("ebook_filename"))
            if not attach_abs_id or not ebook_filename:
                return "Missing book ID or ebook filename", 400
            book = database_service.get_book_by_ref(attach_abs_id)
            if not book:
                return "Book not found", 404
            booklore_id = None
            bl_book, bl_client = find_in_booklore(ebook_filename)
            if bl_book:
                booklore_id = bl_book.get("id")
            kosync_doc_id = get_kosync_id_for_ebook(ebook_filename, booklore_id, bl_client=bl_client)
            if not kosync_doc_id:
                return "Could not compute KOSync ID for ebook", 404
            book.ebook_filename = ebook_filename
            book.kosync_doc_id = kosync_doc_id
            book.status = "pending"
            database_service.save_book(book)
            if bl_client:
                try:
                    bl_client.add_to_shelf(ebook_filename)
                except Exception as e:
                    logger.warning(f"Booklore add_to_shelf failed for '{sanitize_log_data(ebook_filename)}': {e}")
            database_service.resolve_suggestion(kosync_doc_id)
            return redirect(url_for("dashboard.index"))

        # --- Attach audiobook to ebook-only book ---
        if action == "attach_audiobook":
            abs_service = get_abs_service()
            if not abs_service.is_available():
                return "ABS is not configured", 400
            link_book_id = request.form.get("link_book_id")
            abs_id = request.form.get("audiobook_id")
            if not link_book_id or not abs_id:
                return "Missing book ID or audiobook ID", 400
            book = database_service.get_book_by_ref(link_book_id)
            if not book:
                return "Book not found", 404
            audiobooks = abs_service.get_audiobooks()
            selected_ab = next((ab for ab in audiobooks if ab["id"] == abs_id), None)
            if not selected_ab:
                return "Audiobook not found", 404
            new_book = Book(
                abs_id=abs_id,
                title=manager.get_audiobook_title(selected_ab),
                ebook_filename=book.ebook_filename,
                kosync_doc_id=book.kosync_doc_id,
                status=book.status or "not_started",
                duration=manager.get_duration(selected_ab),
                sync_mode="audiobook",
                author=get_audiobook_author(selected_ab),
                subtitle=selected_ab.get("media", {}).get("metadata", {}).get("subtitle") or None,
                **_copy_book_merge_metadata(
                    book,
                    {
                        "storyteller_uuid": book.storyteller_uuid,
                        "original_ebook_filename": book.original_ebook_filename,
                    },
                ),
            )
            database_service.save_book(new_book)
            try:
                database_service.migrate_book_data(link_book_id, abs_id)
                database_service.delete_book(book.id)
                abs_service.add_to_collection(abs_id, current_app.config["ABS_COLLECTION_NAME"])
                logger.info(f"Successfully merged {link_book_id} into {abs_id}")
            except Exception as e:
                logger.error(f"Failed to merge book data: {e}")
                raise
            attempt_hardcover_automatch(container, new_book)
            database_service.resolve_suggestion(abs_id)
            if new_book.kosync_doc_id:
                database_service.resolve_suggestion(new_book.kosync_doc_id)
            return redirect(url_for("dashboard.index"))

        # --- Standard flow (requires audiobook) ---
        abs_service = get_abs_service()
        abs_id = request.form.get("audiobook_id")
        ebook_filename = sanitize_filename(request.form.get("ebook_filename"))
        storyteller_uuid = request.form.get("storyteller_uuid")
        storyteller_submit = request.form.get("storyteller_submit")

        audiobooks = abs_service.get_audiobooks()
        selected_ab = next((ab for ab in audiobooks if ab["id"] == abs_id), None)
        if not selected_ab:
            return "Audiobook not found", 404

        _ab_meta = selected_ab.get("media", {}).get("metadata", {})
        book, error = _create_book_mapping(
            container, abs_id,
            title=manager.get_audiobook_title(selected_ab),
            ebook_filename=ebook_filename,
            duration=manager.get_duration(selected_ab),
            storyteller_uuid=storyteller_uuid,
            storyteller_submit=bool(storyteller_submit),
            author=get_audiobook_author(selected_ab),
            subtitle=_ab_meta.get("subtitle") or None,
        )
        if error:
            return error, 404

        return redirect(url_for("dashboard.index"))

    # GET request
    search = request.args.get("search", "").strip().lower()
    attach_to = request.args.get("attach_to", "").strip()
    link_to = request.args.get("link_to", "").strip()
    preselect_abs_id = request.args.get("abs_id", "").strip()
    attach_title = ""
    link_title = ""

    if attach_to:
        attach_book = database_service.get_book_by_ref(attach_to)
        if attach_book:
            attach_title = attach_book.title or attach_to

    if link_to:
        link_book = database_service.get_book_by_ref(link_to)
        if link_book:
            link_title = link_book.title or link_to

    abs_service = get_abs_service()
    audiobooks, ebooks, storyteller_books = [], [], []
    if search:
        if not attach_to:
            audiobooks = get_audiobooks_conditionally()
            audiobooks = [ab for ab in audiobooks if audiobook_matches_search(ab, search)]
            for ab in audiobooks:
                ab["cover_url"] = abs_service.get_cover_proxy_url(ab["id"])

        if not link_to:
            ebooks = get_searchable_ebooks(search)

            if container.storyteller_client().is_configured():
                try:
                    storyteller_books = container.storyteller_client().search_books(search)
                except Exception as e:
                    logger.warning(f"Storyteller search failed in match route: {e}")

    # If abs_id provided (e.g. from suggestions) but not in search results, fetch it directly
    preselected_audiobook = None
    if preselect_abs_id and not attach_to:
        already_listed = any(ab["id"] == preselect_abs_id for ab in audiobooks)
        if not already_listed:
            all_audiobooks = get_audiobooks_conditionally()
            preselected_audiobook = next((ab for ab in all_audiobooks if ab["id"] == preselect_abs_id), None)
            if preselected_audiobook:
                preselected_audiobook["cover_url"] = abs_service.get_cover_proxy_url(preselect_abs_id)
                audiobooks.insert(0, preselected_audiobook)

    storyteller_submit_available = False
    try:
        st_sub_svc = container.storyteller_submission_service()
        storyteller_submit_available = st_sub_svc.is_available()
    except Exception:
        pass

    storyteller_force_mode = os.environ.get("STORYTELLER_FORCE_MODE", "false").lower() == "true"
    storyteller_configured = container.storyteller_client().is_configured()

    # Detect available services for smart mode defaults
    abs_configured = abs_service.is_available()
    has_ebook_sources = (
        any_booklore_configured()
        or container.cwa_client().is_configured()
        or abs_service.has_ebook_libraries()
        or get_ebook_dir().exists()
    )

    # Build sets of IDs already in the library for "In Library" badges
    library_abs_ids = set()
    library_ebook_filenames = set()
    if search:
        all_books = database_service.get_all_books()
        library_abs_ids = {b.abs_id for b in all_books}
        library_ebook_filenames = {b.ebook_filename for b in all_books if b.ebook_filename}
        library_ebook_filenames |= {b.original_ebook_filename for b in all_books if b.original_ebook_filename}

    return render_template(
        "match.html",
        audiobooks=audiobooks,
        ebooks=ebooks,
        storyteller_books=storyteller_books,
        search=search,
        get_title=manager.get_audiobook_title,
        attach_to=attach_to,
        attach_title=attach_title,
        link_to=link_to,
        link_title=link_title,
        preselect_abs_id=preselect_abs_id,
        storyteller_submit_available=storyteller_submit_available,
        storyteller_force_mode=storyteller_force_mode,
        storyteller_configured=storyteller_configured,
        library_abs_ids=library_abs_ids,
        library_ebook_filenames=library_ebook_filenames,
        abs_configured=abs_configured,
        has_ebook_sources=has_ebook_sources,
    )


@matching_bp.route("/batch-match", methods=["GET", "POST"])
def batch_match():
    container = get_container()
    manager = get_manager()
    database_service = get_database_service()

    abs_service = get_abs_service()

    if request.method == "POST":
        action = request.form.get("action")
        if action == "add_to_queue":
            session.setdefault("queue", [])
            abs_id = request.form.get("audiobook_id") or ""
            ebook_filename = sanitize_filename(request.form.get("ebook_filename", "")) or ""
            ebook_display_name = request.form.get("ebook_display_name", ebook_filename)
            storyteller_uuid = request.form.get("storyteller_uuid", "")

            if not abs_id and not ebook_filename and not storyteller_uuid:
                return redirect(url_for("matching.batch_match", search=request.form.get("search", "")))

            # Resolve audiobook metadata if present
            selected_ab = None
            if abs_id:
                audiobooks = abs_service.get_audiobooks()
                selected_ab = next((ab for ab in audiobooks if ab["id"] == abs_id), None)
                if not selected_ab:
                    return redirect(url_for("matching.batch_match", search=request.form.get("search", "")))

            # Dedup key: abs_id if present, otherwise ebook_filename
            queue_key = abs_id or ebook_filename
            if not any(item.get("queue_key") == queue_key for item in session["queue"]):
                is_ebook_only = not abs_id and (ebook_filename or storyteller_uuid)
                is_audio_only = abs_id and not ebook_filename and not storyteller_uuid
                title = (
                    manager.get_audiobook_title(selected_ab) if selected_ab
                    else ebook_display_name or Path(ebook_filename).stem if ebook_filename
                    else "Storyteller Book"
                )
                _ab_meta = (selected_ab or {}).get("media", {}).get("metadata", {})
                session["queue"].append(
                    {
                        "queue_key": queue_key,
                        "abs_id": abs_id,
                        "title": title,
                        "ebook_filename": ebook_filename,
                        "ebook_display_name": ebook_display_name,
                        "storyteller_uuid": storyteller_uuid,
                        "storyteller_submit": bool(request.form.get("storyteller_submit")),
                        "duration": manager.get_duration(selected_ab) if selected_ab else 0,
                        "cover_url": abs_service.get_cover_proxy_url(abs_id) if abs_id else None,
                        "audio_only": is_audio_only,
                        "ebook_only": is_ebook_only,
                        "author": get_audiobook_author(selected_ab) if selected_ab else None,
                        "subtitle": _ab_meta.get("subtitle") or None,
                    }
                )
                session.modified = True
            return redirect(url_for("matching.batch_match", search=request.form.get("search", "")))
        elif action == "remove_from_queue":
            remove_key = request.form.get("queue_key") or request.form.get("abs_id")
            session["queue"] = [item for item in session.get("queue", []) if item.get("queue_key", item.get("abs_id")) != remove_key]
            session.modified = True
            return redirect(url_for("matching.batch_match"))
        elif action == "clear_queue":
            session["queue"] = []
            session.modified = True
            return redirect(url_for("matching.batch_match"))
        elif action == "process_queue":
            failed_items = []
            for item in session.get("queue", []):
                item_label = item.get("ebook_display_name") or item.get("ebook_filename") or item.get("abs_id")
                try:
                    # Handle audio-only queue items
                    if item.get("audio_only"):
                        book = Book(
                            abs_id=item["abs_id"],
                            title=item["title"],
                            ebook_filename=None,
                            kosync_doc_id=None,
                            status="not_started",
                            duration=item["duration"],
                            sync_mode="audiobook",
                            author=item.get("author"),
                            subtitle=item.get("subtitle"),
                        )
                        database_service.save_book(book, is_new=True)
                        abs_service.add_to_collection(item["abs_id"], current_app.config["ABS_COLLECTION_NAME"])
                        attempt_hardcover_automatch(container, book)
                        database_service.resolve_suggestion(item["abs_id"])
                        continue

                    # Handle ebook-only queue items
                    if item.get("ebook_only"):
                        ebook_filename = item["ebook_filename"]
                        storyteller_uuid = item.get("storyteller_uuid") or None

                        if ebook_filename:
                            bl_book, bl_client = find_in_booklore(ebook_filename)
                            booklore_id = bl_book.get("id") if bl_book else None
                            kosync_doc_id = get_kosync_id_for_ebook(ebook_filename, booklore_id, bl_client=bl_client)
                            if not kosync_doc_id:
                                failed_items.append(item.get("ebook_display_name") or ebook_filename)
                                continue
                            title = item.get("ebook_display_name") or (bl_book.get("title") if bl_book else None) or Path(ebook_filename).stem
                        else:
                            title = item.get("title", "Storyteller Book")
                            ebook_filename = None
                            kosync_doc_id = None

                        book = Book(
                            abs_id=None,
                            title=title,
                            ebook_filename=ebook_filename,
                            kosync_doc_id=kosync_doc_id,
                            status="not_started",
                            sync_mode="ebook_only",
                            storyteller_uuid=storyteller_uuid,
                        )
                        database_service.save_book(book, is_new=True)
                        if kosync_doc_id:
                            database_service.resolve_suggestion(kosync_doc_id)
                        continue

                    book, error = _create_book_mapping(
                        container,
                        abs_id=item["abs_id"],
                        title=item["title"],
                        ebook_filename=item["ebook_filename"],
                        duration=item["duration"],
                        storyteller_uuid=item.get("storyteller_uuid", ""),
                        storyteller_submit=bool(item.get("storyteller_submit")),
                        author=item.get("author"),
                        subtitle=item.get("subtitle"),
                    )
                    if error:
                        failed_items.append(item.get("ebook_display_name") or item["ebook_filename"])

                except Exception as e:
                    logger.error(f"Failed to process queue item '{sanitize_log_data(item_label)}': {e}")
                    failed_items.append(item_label)

            if failed_items:
                names = ", ".join(failed_items)
                flash(f"Could not compute KOSync ID for: {names}", "warning")
            session["queue"] = []
            session.modified = True
            return redirect(url_for("dashboard.index"))

    # GET request
    search = request.args.get("search", "").strip().lower()
    audiobooks, ebooks, storyteller_books = [], [], []
    if search:
        audiobooks = get_audiobooks_conditionally()
        audiobooks = [ab for ab in audiobooks if audiobook_matches_search(ab, search)]
        for ab in audiobooks:
            ab["cover_url"] = abs_service.get_cover_proxy_url(ab["id"])

        ebooks = get_searchable_ebooks(search)
        ebooks.sort(key=lambda x: x.name.lower())

        if container.storyteller_client().is_configured():
            try:
                storyteller_books = container.storyteller_client().search_books(search)
            except Exception as e:
                logger.warning(f"Storyteller search failed in batch_match route: {e}")

    storyteller_submit_available = False
    try:
        st_sub_svc = container.storyteller_submission_service()
        storyteller_submit_available = st_sub_svc.is_available()
    except Exception:
        pass

    storyteller_force_mode = os.environ.get("STORYTELLER_FORCE_MODE", "false").lower() == "true"
    storyteller_configured = container.storyteller_client().is_configured()

    abs_configured = abs_service.is_available()
    has_ebook_sources = (
        any_booklore_configured()
        or container.cwa_client().is_configured()
        or abs_service.has_ebook_libraries()
        or get_ebook_dir().exists()
    )

    queue_view = _build_batch_queue_view(session.get("queue", []))
    return render_template(
        "batch_match.html",
        audiobooks=audiobooks,
        ebooks=ebooks,
        storyteller_books=storyteller_books,
        queue=queue_view["items"],
        queue_summary=queue_view,
        search=search,
        get_title=manager.get_audiobook_title,
        storyteller_submit_available=storyteller_submit_available,
        storyteller_force_mode=storyteller_force_mode,
        storyteller_configured=storyteller_configured,
        abs_configured=abs_configured,
        has_ebook_sources=has_ebook_sources,
    )
