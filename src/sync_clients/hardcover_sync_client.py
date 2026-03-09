import logging
import os

from src.api.hardcover_client import HardcoverClient
from src.db.models import Book, HardcoverDetails, State
from src.services.hardcover_log_service import log_hardcover_action
from src.services.write_tracker import is_own_write, record_write
from src.sync_clients.sync_client_interface import ServiceState, SyncClient, SyncResult, UpdateProgressRequest
from src.utils.ebook_utils import EbookParser
from src.utils.logging_utils import sanitize_log_data

logger = logging.getLogger(__name__)

# Hardcover status IDs
HC_WANT_TO_READ = 1
HC_CURRENTLY_READING = 2
HC_READ = 3
HC_PAUSED = 4
HC_DNF = 5

# Local status → Hardcover status mapping
LOCAL_TO_HC_STATUS = {
    'active': HC_CURRENTLY_READING,
    'completed': HC_READ,
    'paused': HC_PAUSED,
    'dnf': HC_DNF,
}

# Hardcover status → local status mapping
HC_TO_LOCAL_STATUS = {
    HC_CURRENTLY_READING: 'active',
    HC_READ: 'completed',
    HC_PAUSED: 'paused',
    HC_DNF: 'dnf',
}

# Hardcover status → journal event mapping
HC_TRANSITION_EVENTS = {
    HC_CURRENTLY_READING: 'resumed',
    HC_PAUSED: 'paused',
    HC_DNF: 'dnf',
    HC_READ: 'finished',
}


class HardcoverSyncClient(SyncClient):
    """
    Hardcover sync client — bidirectional sync with caching, rate limiting,
    status sync, and optional journal mirroring.
    """

    def __init__(self, hardcover_client: HardcoverClient, ebook_parser: EbookParser,
                 abs_client=None, database_service=None):
        super().__init__(ebook_parser)
        self.hardcover_client = hardcover_client
        self.abs_client = abs_client
        self.database_service = database_service

    def is_configured(self) -> bool:
        return self.hardcover_client.is_configured()

    def check_connection(self):
        return self.hardcover_client.check_connection()

    def can_be_leader(self) -> bool:
        """Hardcover cannot lead — it doesn't provide text content."""
        return False

    def get_supported_sync_types(self) -> set:
        return {'audiobook', 'ebook'}

    # ── Bulk State Fetching (Step 8) ──────────────────────────────────

    def fetch_bulk_state(self) -> dict | None:
        """Pre-fetch all active user_books from Hardcover in one API call.

        Returns dict keyed by hardcover_book_id for quick lookup by get_service_state().
        """
        if not self.is_configured():
            return None
        try:
            return self.hardcover_client.get_currently_reading()
        except Exception as e:
            logger.debug(f"Hardcover bulk fetch failed: {e}")
            return None

    # ── get_service_state (Step 9) ────────────────────────────────────

    def get_service_state(self, book: Book, prev_state: State | None,
                          title_snip: str = "", bulk_context: dict = None) -> ServiceState | None:
        """Read Hardcover progress for delta detection.

        Uses bulk_context if available (from fetch_bulk_state), otherwise
        falls back to a per-book API call. Checks write-suppression to
        avoid echo loops.
        """
        if not self.is_configured() or not self.database_service:
            return None

        hardcover_details = self.database_service.get_hardcover_details(book.abs_id)
        if not hardcover_details or not hardcover_details.hardcover_book_id:
            return None

        hc_book_id = int(hardcover_details.hardcover_book_id)

        # Look up from bulk context or fetch individually
        ub = None
        if bulk_context is not None:
            ub = bulk_context.get(hc_book_id)
        if ub is None:
            ub = self.hardcover_client.find_user_book(hc_book_id)
        if not ub:
            return None

        # Cache user_book_id and status_id
        if ub.get('id') and hardcover_details.hardcover_user_book_id != ub['id']:
            hardcover_details.hardcover_user_book_id = ub['id']
            self.database_service.save_hardcover_details(hardcover_details)
        if ub.get('status_id') and hardcover_details.hardcover_status_id != ub.get('status_id'):
            # Status pull from Hardcover (Step 12)
            self._sync_status_from_hardcover(book, hardcover_details, ub['status_id'])

        # Calculate percentage from progress
        reads = ub.get('user_book_reads', [])
        if not reads:
            return None

        read = reads[0]
        # Cache read ID
        if read.get('id') and hardcover_details.hardcover_user_book_read_id != read['id']:
            hardcover_details.hardcover_user_book_read_id = read['id']
            self.database_service.save_hardcover_details(hardcover_details)

        percentage = self._calculate_percentage(hardcover_details, read)
        if percentage is None:
            return None

        current_state = {'pct': percentage, 'status': ub.get('status_id')}

        # Write suppression
        if is_own_write('Hardcover', book.abs_id, state=current_state):
            return None

        previous_pct = prev_state.percentage if prev_state else 0.0
        delta = percentage - previous_pct

        return ServiceState(
            current=current_state,
            previous_pct=previous_pct,
            delta=delta,
            threshold=0.01,
            is_configured=True,
            display=('Hardcover', f"{percentage:.0%}"),
            value_formatter=lambda v: f"{v:.0%}",
        )

    def _calculate_percentage(self, hardcover_details, read) -> float | None:
        """Calculate progress percentage from a user_book_read entry."""
        progress_pages = read.get('progress_pages')
        progress_seconds = read.get('progress_seconds')
        total_pages = hardcover_details.hardcover_pages or 0
        total_seconds = hardcover_details.hardcover_audio_seconds or 0

        if progress_seconds and total_seconds > 0:
            return min(progress_seconds / total_seconds, 1.0)
        elif progress_pages and total_pages > 0:
            return min(progress_pages / total_pages, 1.0)
        return None

    # ── Status Sync: Hardcover → Local (Step 12) ─────────────────────

    def _sync_status_from_hardcover(self, book, hardcover_details, hc_status_id):
        """Pull status changes from Hardcover and apply locally."""
        cached_status = hardcover_details.hardcover_status_id
        if cached_status == hc_status_id:
            return  # No change

        # Don't act on our own writes
        if is_own_write('Hardcover', book.abs_id):
            hardcover_details.hardcover_status_id = hc_status_id
            self.database_service.save_hardcover_details(hardcover_details)
            return

        local_status = HC_TO_LOCAL_STATUS.get(hc_status_id)
        if not local_status or book.status == local_status:
            hardcover_details.hardcover_status_id = hc_status_id
            self.database_service.save_hardcover_details(hardcover_details)
            return

        # Apply the status change
        old_status = book.status
        book.status = local_status
        self.database_service.save_book(book)

        # Create journal entry for the transition
        event = HC_TRANSITION_EVENTS.get(hc_status_id)
        if event:
            self.database_service.add_reading_journal(book.abs_id, event=event)

        hardcover_details.hardcover_status_id = hc_status_id
        self.database_service.save_hardcover_details(hardcover_details)
        log_hardcover_action(
            self.database_service, abs_id=book.abs_id,
            book_title=sanitize_log_data(book.abs_title),
            direction='pull', action='status_pull',
            detail={'old_status': old_status, 'new_status': local_status, 'hc_status_id': hc_status_id},
        )
        logger.info(
            f"Hardcover → local status: '{sanitize_log_data(book.abs_title)}' "
            f"{old_status} → {local_status} (HC status {hc_status_id})"
        )

    # ── Status Sync: Local → Hardcover (Step 11) ─────────────────────

    def push_local_status(self, book, status_label):
        """Push a local status change to Hardcover.

        Args:
            book: Book model instance
            status_label: one of 'active', 'completed', 'paused', 'dnf'
        """
        if not self.is_configured() or not self.database_service:
            return

        hc_status_id = LOCAL_TO_HC_STATUS.get(status_label)
        if not hc_status_id:
            return

        hardcover_details = self.database_service.get_hardcover_details(book.abs_id)
        if not hardcover_details or not hardcover_details.hardcover_book_id:
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
            record_write('Hardcover', book.abs_id, {'status': hc_status_id})

            # Optional journal mirroring (Step 14)
            self._mirror_journal_if_enabled(book, hardcover_details, hc_status_id)

            log_hardcover_action(
                self.database_service, abs_id=book.abs_id,
                book_title=sanitize_log_data(book.abs_title),
                direction='push', action='status_update',
                detail={'status_label': status_label, 'hc_status_id': hc_status_id},
            )
            logger.info(
                f"Local → Hardcover status: '{sanitize_log_data(book.abs_title)}' "
                f"set to {status_label} (HC status {hc_status_id})"
            )
        except Exception as e:
            log_hardcover_action(
                self.database_service, abs_id=book.abs_id,
                book_title=sanitize_log_data(book.abs_title),
                direction='push', action='status_update',
                success=False, error_message=str(e),
                detail={'status_label': status_label, 'hc_status_id': hc_status_id},
            )
            logger.warning(f"Failed to push status to Hardcover: {e}")

    # ── Cached ID Helpers (Step 4) ────────────────────────────────────

    def _ensure_user_book(self, book, hardcover_details):
        """Return cached user_book dict or fetch and cache IDs.

        Reduces steady-state API calls from 2-3 → 0 per book.
        """
        if hardcover_details.hardcover_user_book_id and hardcover_details.hardcover_status_id:
            return {
                'id': hardcover_details.hardcover_user_book_id,
                'status_id': hardcover_details.hardcover_status_id,
            }

        ub = self.hardcover_client.get_user_book(hardcover_details.hardcover_book_id)
        if not ub:
            return None

        hardcover_details.hardcover_user_book_id = ub['id']
        hardcover_details.hardcover_status_id = ub.get('status_id')
        self.database_service.save_hardcover_details(hardcover_details)
        return ub

    def _ensure_writable_user_book(self, book, hardcover_details):
        """Ensure a user_book exists so metadata like rating can be updated."""
        ub = self._ensure_user_book(book, hardcover_details)
        if ub:
            return ub

        hc_status_id = LOCAL_TO_HC_STATUS.get(book.status, HC_WANT_TO_READ)
        edition_id = self.select_edition_id(book, hardcover_details)
        created = self.hardcover_client.update_status(
            int(hardcover_details.hardcover_book_id),
            hc_status_id,
            int(edition_id) if edition_id else None,
        )
        if not created:
            return None

        hardcover_details.hardcover_user_book_id = created.get('id')
        hardcover_details.hardcover_status_id = created.get('status_id', hc_status_id)
        self.database_service.save_hardcover_details(hardcover_details)
        record_write('Hardcover', book.abs_id, {'status': hardcover_details.hardcover_status_id})
        log_hardcover_action(
            self.database_service, abs_id=book.abs_id,
            book_title=sanitize_log_data(book.abs_title),
            direction='push', action='create_user_book',
            detail={'user_book_id': created.get('id'), 'status_id': hardcover_details.hardcover_status_id},
        )
        return created

    def _create_or_adopt_user_book(self, book, hardcover_details, edition_id=None):
        """Check if a user_book already exists on Hardcover before creating one.

        If the user already has this book on Hardcover (e.g. marked as Read),
        adopt the existing status instead of overwriting it with Want to Read.
        Only creates a new user_book (with a locally-mapped status) when none exists.
        """
        ub = self.hardcover_client.get_user_book(hardcover_details.hardcover_book_id)
        if ub:
            # Adopt existing — do NOT overwrite the user's Hardcover status
            hardcover_details.hardcover_user_book_id = ub['id']
            hardcover_details.hardcover_status_id = ub.get('status_id')
            self.database_service.save_hardcover_details(hardcover_details)
            log_hardcover_action(
                self.database_service, abs_id=book.abs_id,
                book_title=sanitize_log_data(book.abs_title),
                direction='pull', action='adopt_user_book',
                detail={'user_book_id': ub['id'], 'status_id': ub.get('status_id')},
            )
            logger.info(
                f"Hardcover: adopted existing user_book {ub['id']} "
                f"(status {ub.get('status_id')}) for '{sanitize_log_data(book.abs_title)}'"
            )
            return

        # No existing user_book — create one with a status mapped from local
        hc_status_id = LOCAL_TO_HC_STATUS.get(book.status, HC_WANT_TO_READ)
        result = self.hardcover_client.update_status(
            int(hardcover_details.hardcover_book_id),
            hc_status_id,
            int(edition_id) if edition_id else None,
        )
        if result and result.get('id'):
            hardcover_details.hardcover_user_book_id = result['id']
            hardcover_details.hardcover_status_id = result.get('status_id', hc_status_id)
            self.database_service.save_hardcover_details(hardcover_details)
            record_write('Hardcover', book.abs_id, {'status': hc_status_id})
            log_hardcover_action(
                self.database_service, abs_id=book.abs_id,
                book_title=sanitize_log_data(book.abs_title),
                direction='push', action='create_user_book',
                detail={'status_id': hc_status_id, 'user_book_id': result['id']},
            )

    def _ensure_read_id(self, user_book_id, hardcover_details):
        """Return cached read ID or fetch and cache it.

        Detects re-reads: if the latest read has finished_at set, a new read
        will be created by the update_progress mutation.
        """
        if hardcover_details.hardcover_user_book_read_id:
            return hardcover_details.hardcover_user_book_read_id

        # The read ID will be populated when update_progress creates/updates a read
        return None

    # ── Edition Selection (Step 7) ────────────────────────────────────

    def select_edition_id(self, book, hardcover_details):
        """Select the appropriate edition based on sync source."""
        sync_source = getattr(book, 'sync_source', None)
        if sync_source == 'audiobook' and hardcover_details.hardcover_audio_edition_id:
            return hardcover_details.hardcover_audio_edition_id
        return hardcover_details.hardcover_edition_id

    # ── Optional Journal Mirroring (Step 14) ──────────────────────────

    def get_journal_privacy(self) -> int:
        """Read the user's journal privacy setting (default: 3 = private)."""
        if not self.database_service:
            return 3
        val = self.database_service.get_setting('HARDCOVER_JOURNAL_PRIVACY')
        if val and val.isdigit() and int(val) in (1, 2, 3):
            return int(val)
        return 3

    def _mirror_journal_if_enabled(self, book, hardcover_details, hc_status_id):
        """Create a Hardcover reading journal entry if the user has enabled mirroring."""
        event_map = {
            HC_CURRENTLY_READING: ('started_reading', 'HARDCOVER_JOURNAL_ON_START'),
            HC_READ: ('finished_reading', 'HARDCOVER_JOURNAL_ON_FINISH'),
        }
        entry = event_map.get(hc_status_id)
        if not entry:
            return

        event_name, env_key = entry
        if os.environ.get(env_key, '').lower() != 'true':
            return

        try:
            edition_id = self.select_edition_id(book, hardcover_details)
            privacy = self.get_journal_privacy()
            success = self.hardcover_client.create_reading_journal(
                int(hardcover_details.hardcover_book_id),
                int(edition_id) if edition_id else None,
                event_name,
                privacy_setting_id=privacy,
            )
            if success:
                logger.info(f"Hardcover journal mirrored: '{event_name}' for '{sanitize_log_data(book.abs_title)}'")
            else:
                logger.warning(f"Hardcover journal mirror rejected: '{event_name}' for book {hardcover_details.hardcover_book_id} (edition {edition_id})")
        except Exception as e:
            logger.debug(f"Could not mirror journal to Hardcover: {e}")

    def is_journal_push_enabled(self, hardcover_details) -> bool:
        """Check if journal note pushing is enabled for this book.

        Per-book override (journal_sync) takes precedence over global default.
        """
        per_book = hardcover_details.journal_sync
        if per_book == 'on':
            return True
        if per_book == 'off':
            return False
        # None → fall back to global setting
        if not self.database_service:
            return False
        val = self.database_service.get_setting('HARDCOVER_JOURNAL_PUSH_NOTES')
        return val and val.lower() == 'true'

    def push_journal_note(self, book, entry: str):
        """Push a journal note to Hardcover (fire-and-forget on creation)."""
        if not self.is_configured() or not self.database_service:
            return

        hardcover_details = self.database_service.get_hardcover_details(book.abs_id)
        if not hardcover_details or not hardcover_details.hardcover_book_id:
            return

        if not self.is_journal_push_enabled(hardcover_details):
            return

        try:
            edition_id = self.select_edition_id(book, hardcover_details)
            privacy = self.get_journal_privacy()
            success = self.hardcover_client.create_reading_journal(
                int(hardcover_details.hardcover_book_id),
                int(edition_id) if edition_id else None,
                'note',
                entry=entry,
                privacy_setting_id=privacy,
            )
            if success:
                log_hardcover_action(
                    self.database_service, abs_id=book.abs_id,
                    book_title=sanitize_log_data(book.abs_title),
                    direction='push', action='journal_note',
                    detail={'entry_preview': entry[:80] + ('...' if len(entry) > 80 else ''), 'privacy': privacy},
                )
                logger.info(f"Hardcover journal note pushed for '{sanitize_log_data(book.abs_title)}'")
            else:
                logger.warning(f"Hardcover journal note rejected for book {hardcover_details.hardcover_book_id} (edition {edition_id})")
        except Exception as e:
            log_hardcover_action(
                self.database_service, abs_id=book.abs_id,
                book_title=sanitize_log_data(book.abs_title),
                direction='push', action='journal_note',
                success=False, error_message=str(e),
            )
            logger.debug(f"Could not push journal note to Hardcover: {e}")

    def push_local_rating(self, book, rating):
        """Mirror a local rating change to Hardcover when a link exists."""
        if not self.is_configured() or not self.database_service:
            return {'hardcover_synced': False, 'hardcover_error': 'Hardcover not configured'}

        hardcover_details = self.database_service.get_hardcover_details(book.abs_id)
        if not hardcover_details or not hardcover_details.hardcover_book_id:
            return {'hardcover_synced': False, 'hardcover_error': 'Book is not linked to Hardcover'}

        try:
            ub = self._ensure_writable_user_book(book, hardcover_details)
            if not ub or not ub.get('id'):
                return {'hardcover_synced': False, 'hardcover_error': 'Could not resolve Hardcover user_book'}

            result = self.hardcover_client.update_user_book(
                int(ub['id']),
                {'rating': float(rating) if rating is not None else None},
            )
            if not result:
                return {'hardcover_synced': False, 'hardcover_error': 'Hardcover rejected rating update'}

            record_write('Hardcover', book.abs_id, {'rating': rating})
            log_hardcover_action(
                self.database_service, abs_id=book.abs_id,
                book_title=sanitize_log_data(book.abs_title),
                direction='push', action='rating',
                detail={'rating': rating},
            )
            return {'hardcover_synced': True, 'hardcover_error': None}
        except Exception as e:
            log_hardcover_action(
                self.database_service, abs_id=book.abs_id,
                book_title=sanitize_log_data(book.abs_title),
                direction='push', action='rating',
                success=False, error_message=str(e),
                detail={'rating': rating},
            )
            logger.warning(f"Failed to push rating to Hardcover: {e}")
            return {'hardcover_synced': False, 'hardcover_error': str(e)}

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

    def automatch_hardcover(self, book):
        """Match a book with Hardcover using various search strategies."""
        if not self.hardcover_client.is_configured():
            return

        existing_details = self.database_service.get_hardcover_details(book.abs_id)
        if existing_details:
            return

        if not self.abs_client or not self.abs_client.is_configured():
            logger.debug(f"Skipping Hardcover automatch for '{sanitize_log_data(book.abs_title)}': ABS not available")
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
                valid_match, rejected_match = self._try_match_with_strategy(search_func, strategy_name, book.abs_title)
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
                    # Use ebook/physical edition for pages if available
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
            self._create_or_adopt_user_book(book, hardcover_details, match.get('edition_id'))
            log_hardcover_action(
                self.database_service, abs_id=book.abs_id,
                book_title=sanitize_log_data(meta.get('title')),
                direction='push', action='automatch',
                detail={'matched_by': matched_by, 'hardcover_book_id': match.get('book_id'),
                        'slug': match.get('slug')},
            )
            logger.info(f"Hardcover: '{sanitize_log_data(meta.get('title'))}' matched (matched by {matched_by})")
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

        details = HardcoverDetails(
            abs_id=book_abs_id,
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
        log_hardcover_action(
            self.database_service, abs_id=book_abs_id,
            book_title=sanitize_log_data(match.get('title', '')),
            direction='push', action='manual_match',
            detail={'hardcover_book_id': match['book_id'], 'slug': match.get('slug'),
                    'input': input_str},
        )
        logger.info(f"Manually matched ABS {book_abs_id} to Hardcover {match['book_id']} ({match.get('title')})")

        book = self.database_service.get_book(book_abs_id)
        if not book:
            # Fallback: create a minimal Book for status mapping
            book = Book(abs_id=book_abs_id, abs_title='', status='')
        self._create_or_adopt_user_book(book, details, match.get('edition_id'))
        return True

    def get_text_from_current_state(self, book: Book, state: ServiceState) -> str | None:
        return None

    # ── Smarter Status Transitions (Step 6) ───────────────────────────

    def _handle_status_transition(self, book, hardcover_details, current_status, percentage, is_finished):
        """Handle status transitions based on progress.

        Transition table:
        | Current         | Condition        | New Status          |
        |-----------------|------------------|---------------------|
        | Want to Read(1) | progress > 2%    | Currently Reading(2)|
        | Reading(2)      | finished(>99%)   | Read(3)             |
        | Paused(4)       | progress > 2%    | Currently Reading(2)|
        | DNF(5)          | any              | no change           |
        | Read(3)         | non-re-read      | no change           |
        """
        new_status = current_status

        if is_finished and current_status not in (HC_READ, HC_DNF):
            new_status = HC_READ
        elif percentage > 0.02 and current_status in (HC_WANT_TO_READ, HC_PAUSED):
            new_status = HC_CURRENTLY_READING

        if new_status != current_status:
            edition_id = self.select_edition_id(book, hardcover_details)
            try:
                self.hardcover_client.update_status(
                    int(hardcover_details.hardcover_book_id),
                    new_status,
                    int(edition_id) if edition_id else None,
                )
            except Exception as e:
                log_hardcover_action(
                    self.database_service, abs_id=book.abs_id,
                    book_title=sanitize_log_data(book.abs_title),
                    direction='push', action='status_transition',
                    success=False, error_message=str(e),
                    detail={'from': current_status, 'to': new_status},
                )
                logger.error(f"Failed to update Hardcover status: {e}")
                return current_status
            hardcover_details.hardcover_status_id = new_status
            self.database_service.save_hardcover_details(hardcover_details)
            record_write('Hardcover', book.abs_id, {'status': new_status})

            status_names = {1: 'Want to Read', 2: 'Currently Reading', 3: 'Read', 4: 'Paused', 5: 'DNF'}
            log_hardcover_action(
                self.database_service, abs_id=book.abs_id,
                book_title=sanitize_log_data(book.abs_title),
                direction='push', action='status_transition',
                detail={'from': current_status, 'to': new_status,
                        'label': status_names.get(new_status, str(new_status))},
            )
            logger.info(f"Hardcover: '{sanitize_log_data(book.abs_title)}' status → {status_names.get(new_status, new_status)}")

            # Mirror journal if enabled
            self._mirror_journal_if_enabled(book, hardcover_details, new_status)

        return new_status

    # ── Progress Updates ──────────────────────────────────────────────

    def update_progress(self, book: Book, request: UpdateProgressRequest) -> SyncResult:
        """Update progress in Hardcover. Uses cached IDs to minimize API calls."""
        if not self.is_configured() or not self.database_service:
            return SyncResult(None, False)

        self.automatch_hardcover(book)

        percentage = request.locator_result.percentage

        hardcover_details = self.database_service.get_hardcover_details(book.abs_id)
        if not hardcover_details or not hardcover_details.hardcover_book_id:
            return SyncResult(None, False)

        # Use cached user_book or fetch (Step 4)
        ub = self._ensure_user_book(book, hardcover_details)
        if not ub:
            return SyncResult(None, False)

        audio_seconds = hardcover_details.hardcover_audio_seconds or 0
        is_audiobook = getattr(book, 'sync_source', None) == 'audiobook'

        # Edition-aware: select correct edition based on sync source (Step 7)
        edition_id = self.select_edition_id(book, hardcover_details)

        if is_audiobook and audio_seconds > 0:
            return self._update_audiobook_progress(book, hardcover_details, ub, percentage, audio_seconds, edition_id)

        # --- PAGE-BASED PATH ---
        total_pages = hardcover_details.hardcover_pages or 0

        if total_pages <= 0:
            if total_pages == -1:
                return SyncResult(None, False)

            logger.info(f"Hardcover: Pages are 0 for {sanitize_log_data(book.abs_title)}, refreshing...")
            editions = self.hardcover_client.get_all_editions(int(hardcover_details.hardcover_book_id))

            page_ed = editions.get('ebook') or editions.get('physical')
            audio_ed = editions.get('audio')

            if page_ed and page_ed.get('pages') and page_ed['pages'] > 0:
                total_pages = page_ed['pages']
                hardcover_details.hardcover_pages = total_pages
                hardcover_details.hardcover_edition_id = page_ed['id']
                if audio_ed and audio_ed.get('audio_seconds') and audio_ed['audio_seconds'] > 0:
                    hardcover_details.hardcover_audio_edition_id = str(audio_ed['id'])
                    hardcover_details.hardcover_audio_seconds = audio_ed['audio_seconds']
                self.database_service.save_hardcover_details(hardcover_details)
                edition_id = self.select_edition_id(book, hardcover_details)
            elif audio_ed and audio_ed.get('audio_seconds') and audio_ed['audio_seconds'] > 0:
                audio_seconds = audio_ed['audio_seconds']
                hardcover_details.hardcover_audio_seconds = audio_seconds
                hardcover_details.hardcover_audio_edition_id = str(audio_ed['id'])
                hardcover_details.hardcover_edition_id = audio_ed['id']
                hardcover_details.hardcover_pages = -1
                self.database_service.save_hardcover_details(hardcover_details)
                edition_id = self.select_edition_id(book, hardcover_details)
                return self._update_audiobook_progress(book, hardcover_details, ub, percentage, audio_seconds, edition_id)
            else:
                hardcover_details.hardcover_pages = -1
                self.database_service.save_hardcover_details(hardcover_details)
                return SyncResult(None, False)

        if total_pages <= 0:
            page_num = 0
        elif percentage <= 0:
            page_num = 0
        else:
            page_num = max(1, min(int(total_pages * percentage), total_pages))

        is_finished = percentage > 0.99 or (total_pages > 0 and page_num == total_pages)
        if is_finished and total_pages > 0:
            page_num = total_pages
        current_status = ub.get('status_id') or hardcover_details.hardcover_status_id or HC_WANT_TO_READ

        current_status = self._handle_status_transition(book, hardcover_details, current_status, percentage, is_finished)

        try:
            self.hardcover_client.update_progress(
                ub['id'],
                page_num,
                edition_id=edition_id,
                is_finished=is_finished,
                current_percentage=percentage,
                started_at=book.started_at,
                finished_at=book.finished_at,
            )

            actual_pct = 1.0 if is_finished and total_pages > 0 else (
                min(page_num / total_pages, 1.0) if total_pages > 0 else percentage
            )

            updated_state = {
                'pct': actual_pct,
                'pages': page_num,
                'total_pages': total_pages,
                'status': current_status,
            }

            record_write('Hardcover', book.abs_id, updated_state)
            return SyncResult(actual_pct, True, updated_state)

        except Exception as e:
            logger.error(f"Failed to update Hardcover progress: {e}")
            return SyncResult(None, False)

    def _update_audiobook_progress(self, book, hardcover_details, ub, percentage, audio_seconds, edition_id=None):
        """Update Hardcover progress using progress_seconds for audiobook editions."""
        is_finished = percentage > 0.99
        current_status = ub.get('status_id') or hardcover_details.hardcover_status_id or HC_WANT_TO_READ

        current_status = self._handle_status_transition(book, hardcover_details, current_status, percentage, is_finished)

        try:
            progress_seconds = int(audio_seconds * percentage)
            self.hardcover_client.update_progress(
                ub['id'],
                0,
                edition_id=edition_id or hardcover_details.hardcover_edition_id,
                is_finished=is_finished,
                current_percentage=percentage,
                audio_seconds=audio_seconds,
                started_at=book.started_at,
                finished_at=book.finished_at,
            )

            updated_state = {
                'pct': percentage,
                'progress_seconds': progress_seconds,
                'total_seconds': audio_seconds,
                'status': current_status,
            }

            record_write('Hardcover', book.abs_id, updated_state)
            return SyncResult(percentage, True, updated_state)

        except Exception as e:
            logger.error(f"Failed to update Hardcover audiobook progress: {e}")
            return SyncResult(None, False)
