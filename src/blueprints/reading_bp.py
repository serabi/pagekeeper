"""Reading Tab blueprint — reading tracker pages and API endpoints."""

import json as _json
import logging
import math
from datetime import date, datetime
from pathlib import Path

from flask import Blueprint, abort, jsonify, render_template, request

from src.blueprints.helpers import (
    find_booklore_metadata,
    get_abs_service,
    get_book_or_404,
    get_container,
    get_database_service,
    get_enabled_booklore_server_ids,
    get_hardcover_book_url,
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
                             booklore_by_filename=None, abs_metadata_by_id=None,
                             hardcover_details=None):
    """Build a reading-focused data dict for a single book."""
    sync_mode = book.sync_mode
    if sync_mode == 'ebook_only':
        book_type = 'ebook-only'
    elif not book.ebook_filename:
        book_type = 'audio-only'
    else:
        book_type = 'linked'

    # Get unified progress from states
    states = states_by_book.get(book.id, [])
    max_progress = ReadingService.max_progress(states, as_percent=True)

    # Enrich title/author from Booklore or ABS metadata when available
    display_title = book.title or ''
    display_author = ''
    bl_meta = find_booklore_metadata(book, booklore_by_filename) if booklore_by_filename else None
    if bl_meta and bl_meta.title:
        stems = set()
        for check_fn in (book.ebook_filename, book.original_ebook_filename):
            if check_fn:
                stems.add(Path(check_fn).stem)
        if display_title in stems or display_title == book.ebook_filename:
            display_title = bl_meta.title

    if bl_meta and bl_meta.authors:
        display_author = bl_meta.authors

    if not display_author and book_type != 'ebook-only':
        abs_meta = (abs_metadata_by_id or {}).get(book.abs_id, {})
        display_author = abs_meta.get('author') or ''

    if not display_author and book.author:
        display_author = book.author

    if not display_author:
        display_author = book.ebook_filename or ''

    covers = resolve_book_covers(book, abs_service, database_service, book_type,
                                 booklore_meta=bl_meta, hardcover_details=hardcover_details)

    return {
        'id': book.id,
        'abs_id': book.abs_id,
        'title': display_title,
        'abs_author': display_author,
        'ebook_filename': book.ebook_filename,
        'kosync_doc_id': book.kosync_doc_id,
        'status': book.status,
        'book_type': book_type,
        'unified_progress': max_progress,
        'cover_url': covers['cover_url'],
        'placeholder_logo': covers['placeholder_logo'],
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
    states_by_book = database_service.get_states_by_book()

    # Fetch Booklore metadata for title enrichment
    enabled_bl_ids = get_enabled_booklore_server_ids()
    booklore_by_filename = database_service.get_booklore_by_filename(enabled_server_ids=enabled_bl_ids)

    # Bulk-fetch Hardcover details to avoid N+1 in resolve_book_covers
    all_hardcover = database_service.get_all_hardcover_details()
    hardcover_by_book = {h.book_id: h for h in all_hardcover}

    all_book_data = [
        _build_book_reading_data(
            b,
            database_service,
            abs_service,
            states_by_book,
            booklore_by_filename,
            abs_metadata_by_id,
            hardcover_details=hardcover_by_book.get(b.id),
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
    paused.sort(key=lambda b: (b['title'] or '').lower())
    dnf.sort(key=lambda b: (b['title'] or '').lower())
    not_started.sort(key=lambda b: (b['title'] or '').lower())

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

    # Build TBR-linked abs_ids set for "Add to Want to Read" visibility
    tbr_linked_abs_ids = set()
    try:
        tbr_items = database_service.get_tbr_items()
        tbr_linked_abs_ids = {item.book_abs_id for item in tbr_items if item.book_abs_id}
    except Exception as e:
        logger.debug(f"Could not load TBR items: {e}")

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
        not_started_books=not_started,
        not_started_count=len(not_started),
        tbr_linked_abs_ids=tbr_linked_abs_ids,
        tbr_count=tbr_count,
        hc_configured=hc_configured,
        active_tab=active_tab,
    )


@reading_bp.route('/reading/book/<book_ref>')
def reading_detail(book_ref):
    """Render the book detail view with journal."""
    active_tab = request.args.get('tab', 'overview')

    database_service = get_database_service()
    abs_service = get_abs_service()

    book = get_book_or_404(book_ref)

    states_by_book = database_service.get_states_by_book()

    # Booklore enrichment
    enabled_bl_ids = get_enabled_booklore_server_ids()
    booklore_by_filename = database_service.get_booklore_by_filename(enabled_server_ids=enabled_bl_ids)

    hc_details = database_service.get_hardcover_details(book.id)
    book_data = _build_book_reading_data(book, database_service, abs_service, states_by_book,
                                         booklore_by_filename, hardcover_details=hc_details)
    journals = database_service.get_reading_journals(book.id)

    # Synthesize started/finished timeline entries from book dates if missing
    existing_events = {j.event for j in journals}
    synthetic = []
    if book.started_at and 'started' not in existing_events:
        synthetic.append(_synthetic_journal(book.abs_id, 'started', book.started_at))
    if book.finished_at and 'finished' not in existing_events:
        synthetic.append(_synthetic_journal(book.abs_id, 'finished', book.finished_at, percentage=1.0))
    if synthetic:
        journals = list(journals) + synthetic
        journals.sort(key=lambda j: j.created_at or datetime.min, reverse=True)

    # BookFusion highlights matched to this book
    bf_highlights = database_service.get_bookfusion_highlights_for_book_by_book_id(book.id)

    has_bookfusion_link = (
        (book.abs_id or '').startswith('bf-')
        or len(bf_highlights) > 0
        or database_service.is_bookfusion_linked_by_book_id(book.id)
    )

    container = get_container()
    metadata = build_book_metadata(book, container, database_service, abs_service)
    hardcover = metadata.get('_hardcover')

    service_states, integrations, services_enabled = build_service_info(
        book, states_by_book, container, abs_service, metadata, has_bookfusion_link,
    )

    # Check if this book already has a linked TBR item
    has_linked_tbr = database_service.find_tbr_by_book_id(book.id) is not None

    # Alignment tab data
    alignment_info = None
    show_alignment_tab = book.sync_mode != 'ebook_only'

    # Validate active_tab against actually available tabs
    valid_tabs = {'overview', 'journal'}
    if bf_highlights:
        valid_tabs.add('highlights')
    if show_alignment_tab:
        valid_tabs.add('alignment')
    if active_tab not in valid_tabs:
        active_tab = 'overview'
    if show_alignment_tab:
        try:
            alignment_service = container.alignment_service()
            alignment_info = alignment_service.get_alignment_info(book.id)
            if alignment_info:
                book_duration = book.duration
                max_ts = alignment_info['max_timestamp']
                if book_duration and book_duration > 0:
                    coverage = min(max_ts / book_duration, 1.0)
                    alignment_info['coverage'] = coverage
                    alignment_info['coverage_hours'] = max_ts / 3600
                    alignment_info['total_hours'] = book_duration / 3600
                    alignment_info['status'] = 'active' if coverage >= 0.9 else 'partial'
                else:
                    alignment_info['coverage'] = None
                    alignment_info['status'] = 'active'

                # Infer source for legacy data
                if not alignment_info['source'] and book.storyteller_uuid:
                    alignment_info['source'] = 'storyteller'
        except Exception as e:
            logger.debug(f"Failed to load alignment info for book {book.id}: {e}")

    return render_template(
        'reading_detail.html',
        book=book_data,
        journals=journals,
        bf_highlights=bf_highlights,
        has_bookfusion_link=has_bookfusion_link,
        has_linked_tbr=has_linked_tbr,
        metadata=metadata,
        services_enabled=services_enabled,
        service_states=service_states,
        integrations=integrations,
        hardcover_rating_sync_available=services_enabled['hardcover'] and bool(
            hardcover and hardcover.hardcover_book_id
        ),
        hardcover_linked=bool(hardcover and hardcover.hardcover_book_id),
        active_tab=active_tab,
        show_alignment_tab=show_alignment_tab,
        alignment_info=alignment_info,
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
    if item.book_id:
        linked_book = database_service.get_book_by_id(item.book_id)
    elif item.book_abs_id:
        linked_book = database_service.get_book_by_abs_id(item.book_abs_id)

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
        get_hardcover_book_url=get_hardcover_book_url,
    )


# ─── API Endpoints ───────────────────────────────────────────────────


@reading_bp.route('/api/reading/book/<book_ref>/rating', methods=['POST'])
def update_rating(book_ref):
    """Set or update the rating for a book."""
    database_service = get_database_service()
    book = get_book_or_404(book_ref)
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

    book = database_service.update_book_reading_fields(book.id, rating=rating)
    if not book:
        return jsonify({"success": False, "error": "Book not found"}), 404

    hardcover_synced = False
    hardcover_error = None
    try:
        container = get_container()
        hc_service = container.hardcover_service()
        if hc_service.is_configured():
            sync_result = hc_service.push_local_rating(book, rating)
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


@reading_bp.route('/api/reading/book/<book_ref>/progress', methods=['POST'])
def update_progress(book_ref):
    """Manually set reading progress for a book (e.g. BookFusion books without auto-sync)."""
    book = get_book_or_404(book_ref)
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
    result = _get_reading_service().set_progress(book.id, percentage, container)
    if not result['success']:
        return jsonify(result), 404

    return jsonify({"success": True, "percentage": percentage})


@reading_bp.route('/api/reading/book/<book_ref>/dates', methods=['POST'])
def update_dates(book_ref):
    """Update started_at and/or finished_at dates."""
    database_service = get_database_service()
    book = get_book_or_404(book_ref)
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

    effective_started = updates.get('started_at') or (book.started_at if 'started_at' not in updates else None)
    effective_finished = updates.get('finished_at') or (book.finished_at if 'finished_at' not in updates else None)
    if effective_started and effective_finished and effective_started > effective_finished:
        return jsonify({"success": False, "error": "started_at cannot be after finished_at"}), 400

    book = database_service.update_book_reading_fields(book.id, **updates)
    if not book:
        return jsonify({"success": False, "error": "Book not found"}), 404

    # Sync corresponding journal entry timestamps to match the edited dates
    event_map = {'started_at': 'started', 'finished_at': 'finished'}
    for field, event in event_map.items():
        if field in updates:
            journal = database_service.find_journal_by_event(book.id, event)
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


@reading_bp.route('/api/reading/book/<book_ref>/dates/sync-hardcover', methods=['POST'])
def sync_dates_to_hardcover(book_ref):
    """Push local started_at/finished_at to Hardcover, overwriting HC dates."""
    book = get_book_or_404(book_ref)
    container = get_container()
    synced, message = container.reading_date_service().push_dates_to_hardcover(book.id, force=True)
    if synced:
        return jsonify({"success": True, "message": message})
    return jsonify({"success": False, "error": message}), 400


@reading_bp.route('/api/reading/book/<book_ref>/dates/pull-hardcover', methods=['POST'])
def pull_dates_from_hardcover(book_ref):
    """Pull started_at/finished_at from Hardcover into local DB."""
    book = get_book_or_404(book_ref)
    container = get_container()
    success, message, dates = container.reading_date_service().pull_dates_from_hardcover(book.id)
    if success:
        return jsonify({"success": True, "message": message, "dates": dates})
    return jsonify({"success": False, "error": message}), 400


@reading_bp.route('/api/reading/book/<book_ref>/journal', methods=['POST'])
def add_journal(book_ref):
    """Add a journal note for a book."""
    database_service = get_database_service()
    book = get_book_or_404(book_ref)
    data = request.json or {}
    entry = (data.get('entry') or '').strip()

    if not entry:
        return jsonify({"success": False, "error": "Entry text is required"}), 400

    # Get current progress for the journal entry
    book_states = database_service.get_states_for_book(book.id)
    max_pct = ReadingService.max_progress(book_states)

    journal = database_service.add_reading_journal(
        book.id, event='note', entry=entry, percentage=max_pct if max_pct > 0 else None,
        abs_id=book.abs_id,
    )

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
    """Delete a journal entry (cascades to book dates for started/finished)."""
    database_service = get_database_service()

    # Look up the journal before deleting so we can cascade for started/finished
    journal = database_service.get_reading_journal(journal_id)
    if not journal:
        return jsonify({"success": False, "error": "Journal entry not found"}), 404

    book_id = journal.book_id
    event = journal.event

    deleted = database_service.delete_reading_journal(journal_id)
    if not deleted:
        return jsonify({"success": False, "error": "Journal entry not found"}), 404

    # If this was the last started/finished journal, clear the corresponding book field
    cleared_field = None
    if event in ('started', 'finished'):
        remaining = database_service.find_journal_by_event(book_id, event)
        if not remaining:
            cleared_field = 'started_at' if event == 'started' else 'finished_at'
            database_service.update_book_reading_fields(book_id, **{cleared_field: None})

    return jsonify({"success": True, "cleared_field": cleared_field})


@reading_bp.route('/api/reading/journal/<int:journal_id>', methods=['PATCH'])
def update_journal(journal_id):
    """Update a journal entry (notes: text; started/finished: date)."""
    database_service = get_database_service()
    data = request.json or {}
    entry = (data.get('entry') or '').strip()

    existing = database_service.get_reading_journal(journal_id)
    if not existing:
        return jsonify({"success": False, "error": "Journal entry not found"}), 404

    # Started/finished entries: only allow editing the date (created_at), not text
    if existing.event in ('started', 'finished'):
        date_str = (data.get('created_at') or '').strip()
        if not date_str:
            return jsonify({"success": False, "error": "created_at date is required for started/finished entries"}), 400
        try:
            new_dt = datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            return jsonify({"success": False, "error": "Invalid date format (expected YYYY-MM-DD)"}), 400
        journal = database_service.update_reading_journal(journal_id, created_at=new_dt)
        # Also update the corresponding book field
        field = 'started_at' if existing.event == 'started' else 'finished_at'
        database_service.update_book_reading_fields(existing.book_id, **{field: date_str})
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

    if existing.event != 'note':
        return jsonify({"success": False, "error": "Only notes can be edited"}), 400
    if not entry:
        return jsonify({"success": False, "error": "entry is required"}), 400
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
        states_by_book.setdefault(state.book_id, []).append(state)

    result = []
    for book in books:
        states = states_by_book.get(book.id, [])
        max_progress = ReadingService.max_progress(states, as_percent=True)

        result.append({
            'abs_id': book.abs_id,
            'title': book.title,
            'status': book.status,
            'unified_progress': min(max_progress, 100.0),
            'started_at': book.started_at,
            'finished_at': book.finished_at,
            'rating': book.rating,
            'read_count': book.read_count or 1,
        })

    return jsonify(result)


@reading_bp.route('/api/reading/book/<book_ref>', methods=['GET'])
def get_reading_book(book_ref):
    """Single book detail with journals."""
    database_service = get_database_service()

    book = get_book_or_404(book_ref)

    states = database_service.get_states_for_book(book.id)
    max_progress = ReadingService.max_progress(states, as_percent=True)

    journals = database_service.get_reading_journals(book.id)
    journal_list = [{
        'id': j.id,
        'event': j.event,
        'entry': j.entry,
        'percentage': j.percentage,
        'created_at': j.created_at.isoformat() if j.created_at else None,
    } for j in journals]

    return jsonify({
        'abs_id': book.abs_id,
        'title': book.title,
        'status': book.status,
        'unified_progress': min(max_progress, 100.0),
        'started_at': book.started_at,
        'finished_at': book.finished_at,
        'rating': book.rating,
        'read_count': book.read_count or 1,
        'journals': journal_list,
    })


@reading_bp.route('/api/reading/book/<book_ref>/status', methods=['POST'])
def update_status(book_ref):
    """Update reading status for a book (with journal auto-creation).

    Accepts: {"status": "active"|"completed"|"paused"|"dnf"|"not_started"}
    """
    book = get_book_or_404(book_ref)
    data = request.json or {}
    new_status = data.get('status')

    container = get_container()
    result = _get_reading_service().update_status(book.id, new_status, container)
    if not result['success']:
        code = 404 if result.get('error') == 'Book not found' else 400
        return jsonify(result), code

    return jsonify(result)


@reading_bp.route('/api/reading/stats/<int:year>', methods=['GET'])
def get_stats(year):
    """Reading stats for a given year."""
    stats = _get_reading_stats_service().get_year_stats(year)
    return jsonify(stats)
