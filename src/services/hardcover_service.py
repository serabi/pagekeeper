"""Hardcover business logic — everything that isn't part of the SyncClient interface.

Handles status push, rating push, automatch, and manual matching.
Called by blueprints and StatusMachine (not by the sync loop).
"""

import logging

from src.api.hardcover_client import HardcoverClient
from src.db.models import Book, HardcoverDetails, State
from src.services.hardcover_log_service import log_hardcover_action
from src.services.write_tracker import record_write
from src.utils.logging_utils import sanitize_log_data

logger = logging.getLogger(__name__)

# Hardcover status IDs (shared with sync client)
HC_WANT_TO_READ = 1
HC_CURRENTLY_READING = 2
HC_READ = 3
HC_PAUSED = 4
HC_DNF = 5
HC_IGNORED = 6

PROGRESS_START_THRESHOLD = 0.02
PROGRESS_COMPLETE_THRESHOLD = 0.99

LOCAL_TO_HC_STATUS = {
    'not_started': HC_WANT_TO_READ,
    'active': HC_CURRENTLY_READING,
    'completed': HC_READ,
    'paused': HC_PAUSED,
    'dnf': HC_DNF,
}


class HardcoverService:
    """Non-sync Hardcover operations: status push, ratings, matching."""

    def __init__(self, hardcover_client: HardcoverClient, database_service,
                 abs_client=None):
        self.hardcover_client = hardcover_client
        self.database_service = database_service
        self.abs_client = abs_client

    def is_configured(self) -> bool:
        return self.hardcover_client.is_configured()

    def _require_hardcover_details(self, book):
        """Fetch HardcoverDetails and return them only if a book_id is linked.

        Returns HardcoverDetails or None. Used as a guard in internal methods.
        Accepts a Book object and looks up by book.id (integer PK).
        """
        details = self.database_service.get_hardcover_details(book.id)
        if not details or not details.hardcover_book_id:
            return None
        return details

    # ── Edition Selection ─────────────────────────────────────────────

    def select_edition_id(self, book, hardcover_details):
        """Select the appropriate edition based on sync source."""
        sync_source = getattr(book, 'sync_source', None)
        if sync_source == 'audiobook' and hardcover_details.hardcover_audio_edition_id:
            return hardcover_details.hardcover_audio_edition_id
        return hardcover_details.hardcover_edition_id

    def resolve_editions(self, hardcover_details):
        """Fetch and cache edition info (pages, audio_seconds, edition IDs).

        Called at match time to ensure update_progress has the data it needs.
        If pages are already cached (>0 or -1), this is a no-op.
        Returns True if editions were resolved, False otherwise.
        """
        current_pages = hardcover_details.hardcover_pages or 0
        if current_pages != 0:
            return True  # Already resolved

        book_id = hardcover_details.hardcover_book_id
        if not book_id:
            return False

        editions = self.hardcover_client.get_all_editions(int(book_id))
        page_ed = editions.get('ebook') or editions.get('physical')
        audio_ed = editions.get('audio')

        if page_ed and page_ed.get('pages') and page_ed['pages'] > 0:
            hardcover_details.hardcover_pages = page_ed['pages']
            hardcover_details.hardcover_edition_id = page_ed['id']
            if audio_ed and audio_ed.get('audio_seconds') and audio_ed['audio_seconds'] > 0:
                hardcover_details.hardcover_audio_edition_id = str(audio_ed['id'])
                hardcover_details.hardcover_audio_seconds = audio_ed['audio_seconds']
            self.database_service.save_hardcover_details(hardcover_details)
            return True
        elif audio_ed and audio_ed.get('audio_seconds') and audio_ed['audio_seconds'] > 0:
            hardcover_details.hardcover_audio_seconds = audio_ed['audio_seconds']
            hardcover_details.hardcover_audio_edition_id = str(audio_ed['id'])
            hardcover_details.hardcover_edition_id = audio_ed['id']
            hardcover_details.hardcover_pages = -1  # Audio-only
            self.database_service.save_hardcover_details(hardcover_details)
            return True
        else:
            hardcover_details.hardcover_pages = -1  # No usable editions
            self.database_service.save_hardcover_details(hardcover_details)
            return False

    # ── Status Push ───────────────────────────────────────────────────

    def push_local_status(self, book, status_label):
        """Push a local status change to Hardcover."""
        if not self.is_configured():
            return

        hc_status_id = LOCAL_TO_HC_STATUS.get(status_label)
        if not hc_status_id:
            return

        hardcover_details = self._require_hardcover_details(book)
        if not hardcover_details:
            return

        try:
            edition_id = self.select_edition_id(book, hardcover_details)
            self.hardcover_client.update_status(
                int(hardcover_details.hardcover_book_id),
                hc_status_id,
                int(edition_id) if edition_id else None,
            )
            hardcover_details.hardcover_status_id = hc_status_id
            self.database_service.save_hardcover_details(hardcover_details)
            record_write('Hardcover', book.id, {'status': hc_status_id})

            log_hardcover_action(
                self.database_service, abs_id=book.abs_id,
                book_title=sanitize_log_data(book.title),
                direction='push', action='status_update',
                detail={'status_label': status_label, 'hc_status_id': hc_status_id},
            )
            logger.info(
                f"Local → Hardcover status: '{sanitize_log_data(book.title)}' "
                f"set to {status_label} (HC status {hc_status_id})"
            )
        except Exception as e:
            log_hardcover_action(
                self.database_service, abs_id=book.abs_id,
                book_title=sanitize_log_data(book.title),
                direction='push', action='status_update',
                success=False, error_message=str(e),
                detail={'status_label': status_label, 'hc_status_id': hc_status_id},
            )
            logger.warning(f"Failed to push status to Hardcover: {e}")

    # ── Rating Push ───────────────────────────────────────────────────

    def push_local_rating(self, book, rating):
        """Mirror a local rating change to Hardcover when a link exists."""
        if not self.is_configured():
            return {'hardcover_synced': False, 'hardcover_error': 'Hardcover not configured'}

        hardcover_details = self._require_hardcover_details(book)
        if not hardcover_details:
            return {'hardcover_synced': False, 'hardcover_error': 'Book is not linked to Hardcover'}

        try:
            ub = self._get_or_create_user_book(book, hardcover_details)
            if not ub or not ub.get('id'):
                return {'hardcover_synced': False, 'hardcover_error': 'Could not resolve Hardcover user_book'}

            result = self.hardcover_client.update_user_book(
                int(ub['id']),
                {'rating': float(rating) if rating is not None else None},
            )
            if not result:
                return {'hardcover_synced': False, 'hardcover_error': 'Hardcover rejected rating update'}

            record_write('Hardcover', book.id, {'rating': rating})
            log_hardcover_action(
                self.database_service, abs_id=book.abs_id,
                book_title=sanitize_log_data(book.title),
                direction='push', action='rating',
                detail={'rating': rating},
            )
            return {'hardcover_synced': True, 'hardcover_error': None}
        except Exception as e:
            log_hardcover_action(
                self.database_service, abs_id=book.abs_id,
                book_title=sanitize_log_data(book.title),
                direction='push', action='rating',
                success=False, error_message=str(e),
                detail={'rating': rating},
            )
            logger.warning(f"Failed to push rating to Hardcover: {e}")
            return {'hardcover_synced': False, 'hardcover_error': str(e)}

    # ── User Book Lifecycle ───────────────────────────────────────────

    def _get_or_create_user_book(self, book, hardcover_details, edition_id=None):
        """Get an existing user_book from cache/API, or create one on Hardcover.

        Returns the user_book dict (with 'id' and 'status_id') or None on failure.
        Always saves updated IDs to hardcover_details.
        """
        # 1. Return cached IDs if available
        if hardcover_details.hardcover_user_book_id and hardcover_details.hardcover_status_id:
            return {
                'id': hardcover_details.hardcover_user_book_id,
                'status_id': hardcover_details.hardcover_status_id,
            }

        # 2. Try to adopt an existing user_book from HC
        ub = self.hardcover_client.get_user_book(hardcover_details.hardcover_book_id)
        if ub:
            hardcover_details.hardcover_user_book_id = ub['id']
            hardcover_details.hardcover_status_id = ub.get('status_id')
            self.database_service.save_hardcover_details(hardcover_details)
            log_hardcover_action(
                self.database_service, abs_id=book.abs_id,
                book_title=sanitize_log_data(book.title),
                direction='pull', action='adopt_user_book',
                detail={'user_book_id': ub['id'], 'status_id': ub.get('status_id')},
            )
            logger.info(
                f"Hardcover: adopted existing user_book {ub['id']} "
                f"(status {ub.get('status_id')}) for '{sanitize_log_data(book.title)}'"
            )
            return ub

        # 3. Create a new user_book on HC
        # Note: if the API call below succeeds but save_hardcover_details fails,
        # the next call will find the orphaned user_book via get_user_book (step 2)
        # and adopt it — self-healing by design.
        hc_status_id = LOCAL_TO_HC_STATUS.get(book.status, HC_WANT_TO_READ)
        if not edition_id:
            edition_id = self.select_edition_id(book, hardcover_details)
        result = self.hardcover_client.update_status(
            int(hardcover_details.hardcover_book_id),
            hc_status_id,
            int(edition_id) if edition_id else None,
        )
        if not result or not result.get('id'):
            return None

        hardcover_details.hardcover_user_book_id = result['id']
        hardcover_details.hardcover_status_id = result.get('status_id', hc_status_id)
        self.database_service.save_hardcover_details(hardcover_details)
        record_write('Hardcover', book.id, {'status': hardcover_details.hardcover_status_id})
        log_hardcover_action(
            self.database_service, abs_id=book.abs_id,
            book_title=sanitize_log_data(book.title),
            direction='push', action='create_user_book',
            detail={'user_book_id': result['id'], 'status_id': hardcover_details.hardcover_status_id},
        )
        return result

    def _pull_dates_at_match(self, book):
        """Pull reading dates from Hardcover at match time (one-time fill).

        Accepts a Book object (must be persisted with a valid .id).
        """
        try:
            if not book:
                return
            hc_details = self._require_hardcover_details(book)
            if not hc_details:
                return
            user_book = self.hardcover_client.find_user_book(int(hc_details.hardcover_book_id))
            if not user_book:
                return
            reads = user_book.get("user_book_reads", [])
            if not reads:
                return
            read = reads[0]
            updates = {}
            if not book.started_at and read.get("started_at"):
                updates['started_at'] = read["started_at"]
            if not book.finished_at and read.get("finished_at"):
                updates['finished_at'] = read["finished_at"]
            if updates:
                self.database_service.update_book_reading_fields(book.id, **updates)
                logger.info(f"Pulled dates at match time for '{book.abs_id}': {updates}")
        except Exception as e:
            logger.debug(f"Could not pull dates at match time for '{book.abs_id}': {e}")

    # ── Initial Progress Push ─────────────────────────────────────────

    def push_initial_progress(self, book, hardcover_sync_client):
        """Push current local progress to Hardcover after initial linking."""
        states = self.database_service.get_states_for_book(book.id)
        percentages = [s.percentage for s in states if s.percentage is not None]
        max_pct = max(percentages) if percentages else 0.0
        if max_pct <= 0:
            return  # Nothing to push

        try:
            from src.sync_clients.sync_client_interface import LocatorResult, UpdateProgressRequest
            locator = LocatorResult(percentage=max_pct)
            request = UpdateProgressRequest(locator_result=locator, txt="Initial sync", previous_location=None)
            result = hardcover_sync_client.update_progress(book, request)
            if result and result.success:
                import time
                pct = result.updated_state.get('pct', max_pct) if result.updated_state else max_pct
                state = State(
                    abs_id=book.abs_id,
                    book_id=book.id,
                    client_name='hardcover',
                    last_updated=time.time(),
                    percentage=pct,
                )
                self.database_service.save_state(state)
            logger.info(
                f"Hardcover: pushed initial progress {max_pct:.1%} for "
                f"'{sanitize_log_data(book.title)}'"
            )
        except Exception as e:
            logger.warning(f"Failed to push initial progress to Hardcover: {e}")

    # ── Backfill ───────────────────────────────────────────────────────

    def backfill_hardcover_states(self):
        """Create Hardcover State records for linked books that have progress but no HC state.

        Runs once at startup to fix books that were linked before push_initial_progress
        saved state locally.
        """
        import time

        all_details = self.database_service.get_all_hardcover_details()
        backfilled = 0

        for details in all_details:
            if not details.hardcover_book_id:
                continue

            # Check if a Hardcover state already exists
            states = self.database_service.get_states_for_book(details.book_id)
            hc_states = [s for s in states if s.client_name == 'hardcover']
            if hc_states:
                continue

            # Use max progress from other services
            other_pcts = [s.percentage for s in states if s.percentage is not None]
            max_pct = max(other_pcts) if other_pcts else 0.0
            if max_pct <= 0:
                continue

            state = State(
                abs_id=details.abs_id,
                book_id=details.book_id,
                client_name='hardcover',
                last_updated=time.time(),
                percentage=max_pct,
            )
            self.database_service.save_state(state)
            backfilled += 1

        if backfilled:
            logger.info(f"Hardcover: backfilled {backfilled} state record(s)")

    # ── Automatch ─────────────────────────────────────────────────────

    def _try_match_with_strategy(self, search_func, strategy_name, book_title):
        """Try a single search strategy and validate it has pages or audio_seconds."""
        match = search_func()
        if not match:
            return None, None

        pages = match.get('pages')
        if not pages or pages <= 0:
            logger.info(f"'{book_title}' could not find valid page count using '{strategy_name}' match")
            return None, match

        return match, None

    def automatch_hardcover(self, book, hardcover_sync_client=None):
        """Match a book with Hardcover using various search strategies."""
        if not self.hardcover_client.is_configured():
            return

        existing_details = self.database_service.get_hardcover_details(book.id)
        if existing_details:
            return

        if not self.abs_client or not self.abs_client.is_configured():
            logger.debug(f"Skipping Hardcover automatch for '{sanitize_log_data(book.title)}': ABS not available")
            return

        item = self.abs_client.get_item_details(book.abs_id)
        if not item:
            return

        meta = item.get('media', {}).get('metadata', {})
        isbn = meta.get('isbn')
        asin = meta.get('asin')
        title = meta.get('title')
        author = meta.get('authorName')

        match = None
        matched_by = None
        first_rejected = None
        first_rejected_by = None

        search_strategies = [
            (lambda: self.hardcover_client.search_by_isbn(isbn) if isbn else None, 'isbn', isbn),
            (lambda: self.hardcover_client.search_by_isbn(asin) if asin else None, 'asin', asin),
            (lambda: self.hardcover_client.search_by_title_author(title, author) if (title and author) else None, 'title_author', title and author),
            (lambda: self.hardcover_client.search_by_title_author(title, "") if title else None, 'title', title),
        ]

        for search_func, strategy_name, condition in search_strategies:
            if not match and condition:
                valid_match, rejected_match = self._try_match_with_strategy(search_func, strategy_name, book.title)
                if valid_match:
                    match = valid_match
                    matched_by = strategy_name
                    break
                elif rejected_match and not first_rejected:
                    first_rejected = rejected_match
                    first_rejected_by = strategy_name

        # Fallback: check if first rejected match has an audiobook edition
        audio_seconds = None
        audio_edition_id = None
        if not match and first_rejected:
            book_id = first_rejected.get('book_id')
            if book_id:
                editions = self.hardcover_client.get_all_editions(book_id)
                audio_ed = editions.get('audio')
                if audio_ed and audio_ed.get('audio_seconds') and audio_ed['audio_seconds'] > 0:
                    match = first_rejected
                    matched_by = first_rejected_by
                    audio_seconds = audio_ed['audio_seconds']
                    audio_edition_id = str(audio_ed['id'])
                    page_ed = editions.get('ebook') or editions.get('physical')
                    if page_ed and page_ed.get('pages') and page_ed['pages'] > 0:
                        match['edition_id'] = page_ed['id']
                        match['pages'] = page_ed['pages']
                    else:
                        match['edition_id'] = audio_ed['id']
                        match['pages'] = -1
                    logger.info(f"Hardcover: '{sanitize_log_data(meta.get('title'))}' matched as audiobook ({audio_seconds}s)")

        if match:
            hardcover_details = HardcoverDetails(
                abs_id=book.abs_id,
                book_id=book.id,
                hardcover_book_id=match.get('book_id'),
                hardcover_slug=match.get('slug'),
                hardcover_edition_id=match.get('edition_id'),
                hardcover_pages=match.get('pages'),
                hardcover_audio_seconds=audio_seconds,
                isbn=isbn,
                asin=asin,
                matched_by=matched_by,
                hardcover_audio_edition_id=audio_edition_id,
            )

            self.database_service.save_hardcover_details(hardcover_details)
            self.resolve_editions(hardcover_details)
            self._get_or_create_user_book(book, hardcover_details, match.get('edition_id'))
            self._pull_dates_at_match(book)
            log_hardcover_action(
                self.database_service, abs_id=book.abs_id,
                book_title=sanitize_log_data(meta.get('title')),
                direction='push', action='automatch',
                detail={'matched_by': matched_by, 'hardcover_book_id': match.get('book_id'),
                        'slug': match.get('slug')},
            )
            logger.info(f"Hardcover: '{sanitize_log_data(meta.get('title'))}' matched (matched by {matched_by})")
            if hardcover_sync_client:
                self.push_initial_progress(book, hardcover_sync_client)
        else:
            logger.warning(f"Hardcover: No match found for '{sanitize_log_data(meta.get('title'))}'")

    def set_manual_match(self, book_abs_id: str, input_str: str) -> bool:
        """Manually match an ABS book to a Hardcover book via URL, ID, or Slug."""
        if not self.hardcover_client.is_configured():
            logger.error("Hardcover client not configured")
            return False

        match = self.hardcover_client.resolve_book_from_input(input_str)
        if not match:
            logger.error(f"Could not resolve Hardcover book from '{input_str}'")
            return False

        isbn = None
        asin = None

        if self.abs_client:
            try:
                item = self.abs_client.get_item_details(book_abs_id)
                if item:
                    meta = item.get('media', {}).get('metadata', {})
                    isbn = meta.get('isbn')
                    asin = meta.get('asin')
            except Exception as e:
                logger.warning(f"Failed to fetch ABS details during manual match: {e}")

        book = self.database_service.get_book_by_ref(book_abs_id)
        book_id = book.id if book else None
        details = HardcoverDetails(
            abs_id=book_abs_id,
            book_id=book_id,
            hardcover_book_id=match['book_id'],
            hardcover_slug=match.get('slug'),
            hardcover_edition_id=match.get('edition_id'),
            hardcover_pages=match.get('pages'),
            hardcover_audio_seconds=match.get('audio_seconds'),
            isbn=isbn,
            asin=asin,
            matched_by='manual',
        )

        self.database_service.save_hardcover_details(details)
        self.resolve_editions(details)
        log_hardcover_action(
            self.database_service, abs_id=book_abs_id, book_id=book_id,
            book_title=sanitize_log_data(match.get('title', '')),
            direction='push', action='manual_match',
            detail={'hardcover_book_id': match['book_id'], 'slug': match.get('slug'),
                    'input': input_str},
        )
        logger.info(f"Manually matched ABS {book_abs_id} to Hardcover {match['book_id']} ({match.get('title')})")

        if not book:
            book = Book(abs_id=book_abs_id, title='', status='')
        self._get_or_create_user_book(book, details, match.get('edition_id'))
        self._pull_dates_at_match(book)
        return True
