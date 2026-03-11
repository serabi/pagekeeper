"""Books blueprint — delete, clear_progress, sync_now, mark_complete, pause, dnf, resume, update_hash."""

import logging
import threading

from flask import Blueprint, flash, jsonify, redirect, request, url_for

from src.blueprints.helpers import (
    cleanup_mapping_resources,
    find_in_booklore,
    get_container,
    get_database_service,
    get_kosync_id_for_ebook,
    get_manager,
)
from src.services.reading_service import ReadingService
from src.utils.logging_utils import sanitize_log_data


logger = logging.getLogger(__name__)


def _get_reading_service():
    return ReadingService(get_database_service())

books_bp = Blueprint('books', __name__)


@books_bp.route('/delete/<abs_id>', methods=['POST'])
def delete_mapping(abs_id):
    database_service = get_database_service()
    book = database_service.get_book(abs_id)
    if book:
        cleanup_mapping_resources(book)

    database_service.delete_book(abs_id)
    return redirect(url_for('dashboard.index'))


@books_bp.route('/clear-progress/<abs_id>', methods=['POST'])
def clear_progress(abs_id):
    """Clear progress for a mapping by setting all systems to 0%"""
    manager = get_manager()
    database_service = get_database_service()
    book = database_service.get_book(abs_id)

    if not book:
        logger.warning(f"Cannot clear progress: book not found for '{abs_id}'")
        return redirect(url_for('dashboard.index'))

    title = sanitize_log_data(book.abs_title or abs_id)

    def _run():
        try:
            logger.info(f"Clearing progress for {title}")
            manager.clear_progress(abs_id)
            logger.info(f"Progress cleared successfully for {title}")
        except Exception as e:
            logger.error(f"Failed to clear progress for '{abs_id}': {e}")

    threading.Thread(target=_run, daemon=True).start()
    return redirect(url_for('dashboard.index'))


@books_bp.route('/api/retry-transcription/<abs_id>', methods=['POST'])
def retry_transcription(abs_id):
    database_service = get_database_service()
    book = database_service.get_book(abs_id)
    if not book:
        return jsonify({"success": False, "error": "Book not found"}), 404

    if book.status not in ('failed_retry_later', 'failed_permanent'):
        return jsonify({"success": False, "error": "Book is not in a failed state"}), 400

    logger.info(f"Retrying transcription for '{sanitize_log_data(book.abs_title or abs_id)}'")
    database_service.delete_jobs_for_book(abs_id)
    book.status = 'pending'
    database_service.save_book(book)
    return jsonify({"success": True})


@books_bp.route('/api/sync-now/<abs_id>', methods=['POST'])
def sync_now(abs_id):
    manager = get_manager()
    database_service = get_database_service()
    book = database_service.get_book(abs_id)
    if not book:
        return jsonify({"success": False, "error": "Book not found"}), 404

    if book.status == 'completed':
        from src.services.reading_date_service import push_completion_to_clients
        container = get_container()
        push_completion_to_clients(book, container, database_service)
        return jsonify({"success": True, "reload": True})
    else:
        threading.Thread(target=manager.sync_cycle, kwargs={'target_abs_id': abs_id}, daemon=True).start()
        return jsonify({"success": True})


@books_bp.route('/api/mark-complete/<abs_id>', methods=['POST'])
def mark_complete(abs_id):
    perform_delete = request.json.get('delete', False) if request.json else False
    container = get_container()
    result = _get_reading_service().mark_complete_with_sync(
        abs_id, container, perform_delete=perform_delete
    )
    if not result['success']:
        return jsonify(result), 404
    return jsonify(result)


@books_bp.route('/api/pause/<abs_id>', methods=['POST'])
def pause_book(abs_id):
    container = get_container()
    result = _get_reading_service().update_status(
        abs_id, 'paused', container, allowed_from=('active', 'not_started')
    )
    if not result['success']:
        status_code = 404 if result['error'] == 'Book not found' else 400
        return jsonify(result), status_code
    return jsonify(result)


@books_bp.route('/api/dnf/<abs_id>', methods=['POST'])
def dnf_book(abs_id):
    container = get_container()
    result = _get_reading_service().update_status(
        abs_id, 'dnf', container, allowed_from=('active', 'paused', 'not_started')
    )
    if not result['success']:
        status_code = 404 if result['error'] == 'Book not found' else 400
        return jsonify(result), status_code
    return jsonify(result)


@books_bp.route('/api/resume/<abs_id>', methods=['POST'])
def resume_book(abs_id):
    container = get_container()
    result = _get_reading_service().update_status(
        abs_id, 'active', container, allowed_from=('paused', 'dnf', 'not_started')
    )
    if not result['success']:
        status_code = 404 if result['error'] == 'Book not found' else 400
        return jsonify(result), status_code
    return jsonify(result)


@books_bp.route('/update-hash/<abs_id>', methods=['POST'])
def update_hash(abs_id):
    manager = get_manager()
    database_service = get_database_service()

    new_hash = request.form.get('new_hash', '').strip()
    book = database_service.get_book(abs_id)

    if not book:
        flash("Book not found", "error")
        return redirect(url_for('dashboard.index'))

    old_hash = book.kosync_doc_id

    if new_hash:
        book.kosync_doc_id = new_hash
        database_service.save_book(book)
        logger.info(f"Updated KoSync hash for '{sanitize_log_data(book.abs_title)}' to manual input: '{new_hash}'")
        updated = True
    else:
        target_filename = book.original_ebook_filename or book.ebook_filename

        booklore_id = None
        bl_book, matched_bl_client = find_in_booklore(target_filename)
        if bl_book:
            booklore_id = bl_book.get('id')

        recalc_hash = get_kosync_id_for_ebook(target_filename, booklore_id, original_filename=book.ebook_filename, bl_client=matched_bl_client)

        if recalc_hash:
            book.kosync_doc_id = recalc_hash
            database_service.save_book(book)
            logger.info(f"Auto-regenerated KoSync hash for '{sanitize_log_data(book.abs_title)}': '{recalc_hash}'")
            updated = True
        else:
            flash("Could not recalculate hash (file not found?)", "error")
            return redirect(url_for('dashboard.index'))

    if updated and book.kosync_doc_id != old_hash:
        logger.info(f"Hash changed for '{sanitize_log_data(book.abs_title)}' -- triggering instant sync to reconcile progress")
        threading.Thread(target=manager.sync_cycle, kwargs={'target_abs_id': abs_id}, daemon=True).start()

    flash(f"Updated KoSync Hash for {book.abs_title}", "success")
    return redirect(url_for('dashboard.index'))
