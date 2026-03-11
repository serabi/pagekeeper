"""Reading Tab blueprint — reading tracker pages and API endpoints."""

import json as _json
import logging
import math
from datetime import date, datetime
from pathlib import Path

from flask import Blueprint, abort, jsonify, render_template, request

from src.blueprints.helpers import (
    get_abs_service,
    get_container,
    get_database_service,
)
from src.services.book_metadata_service import build_book_metadata, build_service_info
from src.services.reading_service import ReadingService
from src.services.reading_stats_service import ReadingStatsService
from src.utils.cover_resolver import resolve_book_covers

logger = logging.getLogger(__name__)

reading_bp = Blueprint('reading', __name__)


def _get_reading_service():
    return ReadingService(get_database_service())


def _get_reading_stats_service():
    return ReadingStatsService(get_database_service())


def _safe_privacy(val):
    """Parse the journal privacy setting to int, defaulting to 3 (me-only)."""
    try:
        return int(val)
    except (TypeError, ValueError):
        return 3


def _resolve_journal_sync(hardcover_details, database_service):
    """Resolve whether journal sync is enabled for a book (per-book or global)."""
    if not hardcover_details or not hardcover_details.hardcover_book_id:
        return False
    try:
        container = get_container()
        hc_sync = container.hardcover_sync_client()
        return hc_sync.is_journal_push_enabled(hardcover_details)
    except Exception:
        return False


def _synthetic_journal(abs_id, event, date_str, percentage=None):
    """Create a lightweight object mimicking ReadingJournal for timeline display."""
    class _SyntheticJournal:
        def __init__(self):
            self.id = None
            self.abs_id = abs_id
            self.event = event
            self.entry = None
            self.percentage = percentage
            self.created_at = datetime.strptime(date_str, '%Y-%m-%d') if date_str else None
    return _SyntheticJournal()


def _build_book_reading_data(book, database_service, abs_service, states_by_book,
                             booklore_by_filename=None, abs_metadata_by_id=None):
    """Build a reading-focused data dict for a single book."""
    sync_mode = getattr(book, 'sync_mode', 'audiobook')
    if sync_mode == 'ebook_only':
        book_type = 'ebook-only'
    elif not book.ebook_filename:
        book_type = 'audio-only'
    else:
        book_type = 'linked'

    # Get unified progress from states
    states = states_by_book.get(book.abs_id, [])
    max_progress = ReadingService.max_progress(states, as_percent=True)

    # Enrich title/author from Booklore or ABS metadata when available
    display_title = book.abs_title or ''
    display_author = ''
    bl_meta = None
    if booklore_by_filename:
        for fn in (book.ebook_filename, getattr(book, 'original_ebook_filename', None)):
            if fn:
                candidates = booklore_by_filename.get(fn.lower(), [])
                bl_meta = next((b for b in candidates if b.title), candidates[0] if candidates else None)
                if bl_meta and bl_meta.title:
                    stems = set()
                    for check_fn in (book.ebook_filename, getattr(book, 'original_ebook_filename', None)):
                        if check_fn:
                            stems.add(Path(check_fn).stem)
                    if display_title in stems or display_title == book.ebook_filename:
                        display_title = bl_meta.title
                    break

    if bl_meta and bl_meta.authors:
        display_author = bl_meta.authors

    if not display_author and book_type != 'ebook-only':
        abs_meta = (abs_metadata_by_id or {}).get(book.abs_id, {})
        display_author = abs_meta.get('author') or ''

    if not display_author and getattr(book, 'author', None):
        display_author = book.author

    if not display_author:
        display_author = book.ebook_filename or ''

    covers = resolve_book_covers(book, abs_service, database_service, book_type,
                                 booklore_meta=bl_meta)

    return {
        'abs_id': book.abs_id,
        'abs_title': display_title,
        'abs_author': display_author,
        'ebook_filename': book.ebook_filename,
        'kosync_doc_id': book.kosync_doc_id,
        'status': book.status,
        'book_type': book_type,
        'unified_progress': max_progress,
        'cover_url': covers['cover_url'],
        'custom_cover_url': covers['custom_cover_url'],
        'abs_cover_url': covers['abs_cover_url'],
        'fallback_cover_url': covers['fallback_cover_url'],
        'started_at': book.started_at,
        'finished_at': book.finished_at,
        'rating': book.rating,
        'read_count': book.read_count or 1,
    }


def _is_genuinely_reading(book_data):
    """Determine if a book is genuinely being read vs just synced.

    A book with status='active' might just be synced and not truly being read.
    We only count it as "currently reading" if it has meaningful progress (>1%).
    We don't trust started_at alone because ABS/Hardcover auto-set it on first sync.
    """
    if book_data['status'] == 'not_started':
        return False
    if book_data['status'] != 'active':
        return True  # paused/completed/dnf are explicit user actions
    return book_data['unified_progress'] > 1.0


@reading_bp.route('/reading')
@reading_bp.route('/reading/tbr')
@reading_bp.route('/reading/stats')
def reading_index():
    """Render the main reading tab page."""
    database_service = get_database_service()
    abs_service = get_abs_service()

    books = database_service.get_all_books()

    # Only include books with reading-relevant statuses
    reading_statuses = {'active', 'completed', 'paused', 'dnf', 'not_started'}
    books = [b for b in books if b.status in reading_statuses]

    abs_metadata_by_id = {}
    try:
        all_abs_books = abs_service.get_audiobooks()
        for ab in all_abs_books:
            ab_id = ab.get('id')
            if not ab_id:
                continue
            metadata = ab.get('media', {}).get('metadata', {})
            abs_metadata_by_id[ab_id] = {
                'author': metadata.get('authorName') or '',
            }
    except Exception as e:
        logger.warning(f"Could not fetch ABS metadata for reading log enrichment: {e}")

    # Fetch all states at once to avoid N+1
    all_states = database_service.get_all_states()
    states_by_book = {}
    for state in all_states:
        states_by_book.setdefault(state.abs_id, []).append(state)

    # Fetch Booklore metadata for title enrichment
    all_booklore_books = database_service.get_all_booklore_books()
    booklore_by_filename = {}
    for bl_book in all_booklore_books:
        if bl_book.filename:
            booklore_by_filename.setdefault(bl_book.filename.lower(), []).append(bl_book)

    all_book_data = [
        _build_book_reading_data(
            b,
            database_service,
            abs_service,
            states_by_book,
            booklore_by_filename,
            abs_metadata_by_id,
        )
        for b in books
    ]

    # Classify books and assign display_status
    currently_reading = []
    finished = []
    paused = []
    dnf = []
    not_started = []

    for bd in all_book_data:
        if bd['status'] == 'completed':
            bd['display_status'] = 'finished'
            finished.append(bd)
        elif bd['status'] == 'paused':
            bd['display_status'] = 'paused'
            paused.append(bd)
        elif bd['status'] == 'dnf':
            bd['display_status'] = 'dnf'
            dnf.append(bd)
        elif bd['status'] == 'not_started':
            bd['display_status'] = 'not_started'
            not_started.append(bd)
        elif _is_genuinely_reading(bd):
            bd['display_status'] = 'reading'
            currently_reading.append(bd)
        else:
            bd['display_status'] = 'not_started'
            not_started.append(bd)

    # Sort each section for default ordering
    currently_reading.sort(key=lambda b: b['unified_progress'], reverse=True)
    finished.sort(key=lambda b: b['finished_at'] or '', reverse=True)
    paused.sort(key=lambda b: (b['abs_title'] or '').lower())
    dnf.sort(key=lambda b: (b['abs_title'] or '').lower())
    not_started.sort(key=lambda b: (b['abs_title'] or '').lower())

    section_counts = {
        'reading': len(currently_reading),
        'finished': len(finished),
        'paused': len(paused),
        'dnf': len(dnf),
        'not_started': len(not_started),
    }

    # Collect unique years from finished books for year dividers
    finished_years = sorted(
        {bd['finished_at'][:4] for bd in finished if bd.get('finished_at')},
        reverse=True,
    )

    current_year = date.today().year
    stats = _get_reading_stats_service().get_year_stats(current_year)
    goal = database_service.get_reading_goal(current_year)
    reading_sections = [
        {
            'id': 'continue',
            'title': 'Continue Reading',
            'description': 'Books with active progress and quick resume context.',
            'books': currently_reading,
        },
        {
            'id': 'finished',
            'title': 'Recently Finished',
            'description': 'Completed books grouped by finish year.',
            'books': finished,
            'group_by_year': True,
        },
        {
            'id': 'stalled',
            'title': 'Paused and DNF',
            'description': 'Books you may revisit or archive.',
            'books': paused + dnf,
        },
        {
            'id': 'backlog',
            'title': 'Backlog',
            'description': 'Tracked books that have not started yet.',
            'books': not_started,
        },
    ]

    # TBR count for tab badge
    tbr_count = database_service.get_tbr_count()

    # Check if Hardcover is configured (for TBR import/search toggle)
    try:
        hc_configured = get_container().hardcover_client().is_configured()
    except Exception:
        hc_configured = False

    # Determine active tab from route
    path_to_tab = {'/reading/tbr': 'tbr', '/reading/stats': 'stats'}
    active_tab = path_to_tab.get(request.path, 'log')

    return render_template(
        'reading.html',
        all_books=currently_reading + finished + paused + dnf + not_started,
        reading_sections=reading_sections,
        section_counts=section_counts,
        finished_years=finished_years,
        stats=stats,
        goal=goal,
        current_year=current_year,
        total_books=len(all_book_data),
        tbr_count=tbr_count,
        hc_configured=hc_configured,
        active_tab=active_tab,
    )


@reading_bp.route('/reading/book/<abs_id>')
def reading_detail(abs_id):
    """Render the book detail view with journal."""
    database_service = get_database_service()
    abs_service = get_abs_service()

    book = database_service.get_book(abs_id)
    if not book:
        abort(404)

    all_states = database_service.get_all_states()
    states_by_book = {}
    for state in all_states:
        states_by_book.setdefault(state.abs_id, []).append(state)

    # Booklore enrichment
    all_booklore_books = database_service.get_all_booklore_books()
    booklore_by_filename = {}
    for bl_book in all_booklore_books:
        if bl_book.filename:
            booklore_by_filename.setdefault(bl_book.filename.lower(), []).append(bl_book)

    book_data = _build_book_reading_data(book, database_service, abs_service, states_by_book,
                                         booklore_by_filename)
    journals = database_service.get_reading_journals(abs_id)

    # Synthesize started/finished timeline entries from book dates if missing
    existing_events = {j.event for j in journals}
    synthetic = []
    if book.started_at and 'started' not in existing_events:
        synthetic.append(_synthetic_journal(abs_id, 'started', book.started_at))
    if book.finished_at and 'finished' not in existing_events:
        synthetic.append(_synthetic_journal(abs_id, 'finished', book.finished_at, percentage=1.0))
    if synthetic:
        journals = list(journals) + synthetic
        journals.sort(key=lambda j: j.created_at or datetime.min, reverse=True)

    # BookFusion highlights matched to this book
    bf_highlights = database_service.get_bookfusion_highlights_for_book(abs_id)

    has_bookfusion_link = (
        abs_id.startswith('bf-')
        or len(bf_highlights) > 0
        or database_service.is_bookfusion_linked(abs_id)
    )

    container = get_container()
    metadata = build_book_metadata(book, container, database_service, abs_service)
    hardcover = metadata.get('_hardcover')

    service_states, integrations, services_enabled = build_service_info(
        book, states_by_book, container, abs_service, metadata, has_bookfusion_link,
    )

    return render_template(
        'reading_detail.html',
        book=book_data,
        journals=journals,
        bf_highlights=bf_highlights,
        has_bookfusion_link=has_bookfusion_link,
        metadata=metadata,
        services_enabled=services_enabled,
        service_states=service_states,
        integrations=integrations,
        hardcover_rating_sync_available=services_enabled['hardcover'] and bool(
            hardcover and hardcover.hardcover_book_id
        ),
        hardcover_linked=bool(hardcover and hardcover.hardcover_book_id),
        journal_sync_enabled=_resolve_journal_sync(hardcover, database_service),
        journal_privacy_default=_safe_privacy(database_service.get_setting('HARDCOVER_JOURNAL_PRIVACY')),
    )


@reading_bp.route('/reading/tbr/<int:item_id>')
def tbr_detail(item_id):
    """Render the TBR book detail page."""
    database_service = get_database_service()

    item = database_service.get_tbr_item(item_id)
    if not item:
        abort(404)

    # Deserialize genres
    genres = _json.loads(item.genres) if item.genres else []

    # Resolve linked library book
    linked_book = None
    if item.book_abs_id:
        linked_book = database_service.get_book(item.book_abs_id)

    # Check HC configuration
    try:
        hc_configured = get_container().hardcover_client().is_configured()
    except Exception:
        hc_configured = False

    return render_template(
        'tbr_detail.html',
        item=item,
        genres=genres,
        linked_book=linked_book,
        hc_configured=hc_configured,
    )


# ─── API Endpoints ───────────────────────────────────────────────────


@reading_bp.route('/api/reading/book/<abs_id>/rating', methods=['POST'])
def update_rating(abs_id):
    """Set or update the rating for a book."""
    database_service = get_database_service()
    data = request.json or {}
    rating = data.get('rating')

    if rating is not None:
        try:
            rating = float(rating)
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "Invalid rating value"}), 400
        if not math.isfinite(rating) or rating < 0 or rating > 5:
            return jsonify({"success": False, "error": "Rating must be between 0 and 5"}), 400
        if abs((rating * 2) - round(rating * 2)) > 1e-9:
            return jsonify({"success": False, "error": "Rating must be in 0.5 increments"}), 400

    book = database_service.update_book_reading_fields(abs_id, rating=rating)
    if not book:
        return jsonify({"success": False, "error": "Book not found"}), 404

    hardcover_synced = False
    hardcover_error = None
    try:
        container = get_container()
        hc_sync = container.hardcover_sync_client()
        if hc_sync.is_configured():
            sync_result = hc_sync.push_local_rating(book, rating)
            hardcover_synced = bool(sync_result.get('hardcover_synced'))
            hardcover_error = sync_result.get('hardcover_error')
    except Exception as e:
        hardcover_error = str(e)

    return jsonify({
        "success": True,
        "rating": book.rating,
        "hardcover_synced": hardcover_synced,
        "hardcover_error": hardcover_error,
    })


@reading_bp.route('/api/reading/book/<abs_id>/progress', methods=['POST'])
def update_progress(abs_id):
    """Manually set reading progress for a book (e.g. BookFusion books without auto-sync)."""
    data = request.json or {}
    percentage = data.get('percentage')

    if percentage is None:
        return jsonify({"success": False, "error": "percentage is required"}), 400

    try:
        percentage = float(percentage)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid percentage value"}), 400

    if not math.isfinite(percentage) or percentage < 0 or percentage > 1:
        return jsonify({"success": False, "error": "percentage must be between 0 and 1"}), 400

    container = get_container()
    result = _get_reading_service().set_progress(abs_id, percentage, container)
    if not result['success']:
        return jsonify(result), 404

    return jsonify({"success": True, "percentage": percentage})


@reading_bp.route('/api/reading/book/<abs_id>/dates', methods=['POST'])
def update_dates(abs_id):
    """Update started_at and/or finished_at dates."""
    database_service = get_database_service()
    data = request.json or {}
    updates = {}

    for field in ('started_at', 'finished_at'):
        if field in data:
            val = data[field]
            if val:
                try:
                    datetime.strptime(val, '%Y-%m-%d')
                except ValueError:
                    return jsonify({"success": False, "error": f"Invalid date format for {field}"}), 400
            updates[field] = val or None

    if not updates:
        return jsonify({"success": False, "error": "No date fields provided"}), 400

    # Cross-validate against existing DB values when only one date is provided
    book = database_service.get_book(abs_id)
    if not book:
        return jsonify({"success": False, "error": "Book not found"}), 404

    effective_started = updates.get('started_at') or (book.started_at if 'started_at' not in updates else None)
    effective_finished = updates.get('finished_at') or (book.finished_at if 'finished_at' not in updates else None)
    if effective_started and effective_finished and effective_started > effective_finished:
        return jsonify({"success": False, "error": "started_at cannot be after finished_at"}), 400

    book = database_service.update_book_reading_fields(abs_id, **updates)
    if not book:
        return jsonify({"success": False, "error": "Book not found"}), 404

    # Sync corresponding journal entry timestamps to match the edited dates
    event_map = {'started_at': 'started', 'finished_at': 'finished'}
    for field, event in event_map.items():
        if field in updates:
            journal = database_service.find_journal_by_event(abs_id, event)
            if journal:
                new_date = updates[field]
                if new_date:
                    new_dt = datetime.strptime(new_date, '%Y-%m-%d')
                    database_service.update_reading_journal(journal.id, created_at=new_dt)

    return jsonify({
        "success": True,
        "started_at": book.started_at,
        "finished_at": book.finished_at,
    })


@reading_bp.route('/api/reading/book/<abs_id>/dates/sync-hardcover', methods=['POST'])
def sync_dates_to_hardcover(abs_id):
    """Push local started_at/finished_at to Hardcover, overwriting HC dates."""
    database_service = get_database_service()
    book = database_service.get_book(abs_id)
    if not book:
        return jsonify({"success": False, "error": "Book not found"}), 404

    try:
        from src.services.reading_date_service import push_dates_to_hardcover
        container = get_container()
        synced = push_dates_to_hardcover(abs_id, container, database_service, force=True)
        if synced:
            return jsonify({"success": True, "message": "Dates synced to Hardcover"})
        return jsonify({"success": False, "error": "Nothing to sync — dates already match or Hardcover not linked"}), 400
    except Exception as e:
        logger.debug(f"Could not push dates to Hardcover: {e}")
        return jsonify({"success": False, "error": "Failed to sync dates to Hardcover"}), 500


@reading_bp.route('/api/reading/book/<abs_id>/journal', methods=['POST'])
def add_journal(abs_id):
    """Add a journal note for a book."""
    database_service = get_database_service()
    data = request.json or {}
    entry = (data.get('entry') or '').strip()

    if not entry:
        return jsonify({"success": False, "error": "Entry text is required"}), 400

    book = database_service.get_book(abs_id)
    if not book:
        return jsonify({"success": False, "error": "Book not found"}), 404

    # Get current progress for the journal entry
    book_states = database_service.get_states_for_book(abs_id)
    max_pct = ReadingService.max_progress(book_states)

    journal = database_service.add_reading_journal(
        abs_id, event='note', entry=entry, percentage=max_pct if max_pct > 0 else None
    )

    # Fire-and-forget push to Hardcover
    try:
        container = get_container()
        hc_sync = container.hardcover_sync_client()
        if hc_sync.is_configured():
            hc_sync.push_journal_note(book, entry)
    except Exception as e:
        logger.debug(f"Hardcover journal push failed for {abs_id}: {e}")

    return jsonify({
        "success": True,
        "journal": {
            "id": journal.id,
            "event": journal.event,
            "entry": journal.entry,
            "percentage": journal.percentage,
            "created_at": journal.created_at.isoformat() if journal.created_at else None,
        }
    })


@reading_bp.route('/api/reading/journal/<int:journal_id>', methods=['DELETE'])
def delete_journal(journal_id):
    """Delete a journal entry."""
    database_service = get_database_service()
    deleted = database_service.delete_reading_journal(journal_id)
    if not deleted:
        return jsonify({"success": False, "error": "Journal entry not found"}), 404
    return jsonify({"success": True})


@reading_bp.route('/api/reading/journal/<int:journal_id>', methods=['PATCH'])
def update_journal(journal_id):
    """Update a journal note entry."""
    database_service = get_database_service()
    data = request.json or {}
    entry = (data.get('entry') or '').strip()
    if not entry:
        return jsonify({"success": False, "error": "entry is required"}), 400

    existing = database_service.get_reading_journal(journal_id)
    if not existing:
        return jsonify({"success": False, "error": "Journal entry not found"}), 404
    if existing.event != 'note':
        return jsonify({"success": False, "error": "Only notes can be edited"}), 400
    journal = database_service.update_reading_journal(journal_id, entry=entry)

    return jsonify({
        "success": True,
        "journal": {
            "id": journal.id,
            "event": journal.event,
            "entry": journal.entry,
            "percentage": journal.percentage,
            "created_at": journal.created_at.isoformat() if journal.created_at else None,
        }
    })


@reading_bp.route('/api/reading/book/<abs_id>/journal-sync', methods=['POST'])
def set_journal_sync(abs_id):
    """Set per-book journal sync preference for Hardcover."""
    database_service = get_database_service()
    data = request.json or {}
    journal_sync = data.get('journal_sync')

    if journal_sync not in ('on', 'off', None):
        return jsonify({"success": False, "error": "journal_sync must be 'on', 'off', or null"}), 400

    hardcover_details = database_service.get_hardcover_details(abs_id)
    if not hardcover_details:
        return jsonify({"success": False, "error": "Book not linked to Hardcover"}), 404

    hardcover_details.journal_sync = journal_sync
    database_service.save_hardcover_details(hardcover_details)
    return jsonify({"success": True, "journal_sync": journal_sync})


@reading_bp.route('/api/reading/journal/<int:journal_id>/push-hardcover', methods=['POST'])
def push_journal_to_hardcover(journal_id):
    """Push an existing journal entry (note or highlight) to Hardcover on demand."""
    database_service = get_database_service()

    journal = database_service.get_reading_journal(journal_id)
    if not journal:
        return jsonify({"success": False, "error": "Journal entry not found"}), 404

    if not journal.entry:
        return jsonify({"success": False, "error": "Journal entry has no text to push"}), 400

    hardcover_details = database_service.get_hardcover_details(journal.abs_id)
    if not hardcover_details or not hardcover_details.hardcover_book_id:
        return jsonify({"success": False, "error": "Book not linked to Hardcover"}), 400

    try:
        container = get_container()
        hc_client = container.hardcover_client()
        if not hc_client or not hc_client.is_configured():
            return jsonify({"success": False, "error": "Hardcover not configured"}), 400

        data = request.json or {}
        try:
            privacy_override = int(data['privacy'])
        except (KeyError, TypeError, ValueError):
            privacy_override = None

        hc_sync = container.hardcover_sync_client()
        edition_id = hc_sync.select_edition_id(
            database_service.get_book(journal.abs_id), hardcover_details
        )
        privacy = privacy_override if privacy_override in (1, 2, 3) else hc_sync.get_journal_privacy()

        book = database_service.get_book(journal.abs_id)
        book_title = book.abs_title if book else None

        success = hc_client.create_reading_journal(
            int(hardcover_details.hardcover_book_id),
            int(edition_id) if edition_id else None,
            'note',
            action_at=journal.created_at.isoformat() if journal.created_at else None,
            entry=journal.entry,
            privacy_setting_id=privacy,
        )
        if success:
            from src.services.hardcover_log_service import log_hardcover_action
            from src.utils.logging_utils import sanitize_log_data
            preview = journal.entry[:80] + ('...' if len(journal.entry) > 80 else '')
            log_hardcover_action(
                database_service, abs_id=journal.abs_id,
                book_title=sanitize_log_data(book_title) if book_title else None,
                direction='push', action='journal_note',
                detail={'entry_preview': preview, 'privacy': privacy, 'source': journal.event},
            )
            return jsonify({"success": True})
        return jsonify({"success": False, "error": "Hardcover rejected the journal entry"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@reading_bp.route('/api/reading/goal/<int:year>', methods=['GET'])
def get_goal(year):
    """Get the reading goal for a given year."""
    database_service = get_database_service()
    stats = _get_reading_stats_service().get_year_stats(year)
    goal = database_service.get_reading_goal(year)

    return jsonify({
        "year": year,
        "target": goal.target_books if goal else None,
        "completed": stats['books_finished'],
    })


@reading_bp.route('/api/reading/goal/<int:year>', methods=['POST'])
def set_goal(year):
    """Set or update the yearly reading goal."""
    database_service = get_database_service()
    data = request.json or {}
    target = data.get('target_books')

    if target is None:
        return jsonify({"success": False, "error": "target_books is required"}), 400

    try:
        target = int(target)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "target_books must be an integer"}), 400

    if target < 1:
        return jsonify({"success": False, "error": "target_books must be at least 1"}), 400

    goal = database_service.save_reading_goal(year, target)
    return jsonify({"success": True, "year": goal.year, "target_books": goal.target_books})


# ─── New API Endpoints (Step 1 — Issue #16 leftovers) ────────────────


@reading_bp.route('/api/reading/books', methods=['GET'])
def get_reading_books():
    """Return all books with reading data (status, progress, dates, rating)."""
    database_service = get_database_service()

    books = database_service.get_all_books()
    reading_statuses = {'active', 'completed', 'paused', 'dnf', 'not_started'}
    books = [b for b in books if b.status in reading_statuses]

    all_states = database_service.get_all_states()
    states_by_book = {}
    for state in all_states:
        states_by_book.setdefault(state.abs_id, []).append(state)

    result = []
    for book in books:
        states = states_by_book.get(book.abs_id, [])
        max_progress = ReadingService.max_progress(states, as_percent=True)

        result.append({
            'abs_id': book.abs_id,
            'abs_title': book.abs_title,
            'status': book.status,
            'unified_progress': min(max_progress, 100.0),
            'started_at': book.started_at,
            'finished_at': book.finished_at,
            'rating': book.rating,
            'read_count': book.read_count or 1,
        })

    return jsonify(result)


@reading_bp.route('/api/reading/book/<abs_id>', methods=['GET'])
def get_reading_book(abs_id):
    """Single book detail with journals."""
    database_service = get_database_service()

    book = database_service.get_book(abs_id)
    if not book:
        return jsonify({"error": "Book not found"}), 404

    states = database_service.get_states_for_book(abs_id)
    max_progress = ReadingService.max_progress(states, as_percent=True)

    journals = database_service.get_reading_journals(abs_id)
    journal_list = [{
        'id': j.id,
        'event': j.event,
        'entry': j.entry,
        'percentage': j.percentage,
        'created_at': j.created_at.isoformat() if j.created_at else None,
    } for j in journals]

    return jsonify({
        'abs_id': book.abs_id,
        'abs_title': book.abs_title,
        'status': book.status,
        'unified_progress': min(max_progress, 100.0),
        'started_at': book.started_at,
        'finished_at': book.finished_at,
        'rating': book.rating,
        'read_count': book.read_count or 1,
        'journals': journal_list,
    })


@reading_bp.route('/api/reading/book/<abs_id>/status', methods=['POST'])
def update_status(abs_id):
    """Update reading status for a book (with journal auto-creation).

    Accepts: {"status": "active"|"completed"|"paused"|"dnf"|"not_started"}
    """
    data = request.json or {}
    new_status = data.get('status')

    container = get_container()
    result = _get_reading_service().update_status(abs_id, new_status, container)
    if not result['success']:
        code = 404 if result.get('error') == 'Book not found' else 400
        return jsonify(result), code

    return jsonify(result)


@reading_bp.route('/api/reading/stats/<int:year>', methods=['GET'])
def get_stats(year):
    """Reading stats for a given year."""
    stats = _get_reading_stats_service().get_year_stats(year)
    return jsonify(stats)
