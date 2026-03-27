"""Hardcover sync client — push-only SyncClient for Hardcover.

Hardcover is a mirror of PageKeeper state, not a source. This client only
handles progress pushes. Non-sync operations (status push, ratings, journals,
matching) live in src/services/hardcover_service.py.
"""

import logging

from src.api.hardcover_client import HardcoverClient
from src.db.models import Book, State
from src.services.hardcover_log_service import log_hardcover_action
from src.services.hardcover_service import (
    HC_CURRENTLY_READING,
    HC_DNF,
    HC_PAUSED,
    HC_READ,
    HC_WANT_TO_READ,
    PROGRESS_COMPLETE_THRESHOLD,
    PROGRESS_START_THRESHOLD,
)
from src.services.write_tracker import record_write
from src.sync_clients.sync_client_interface import ServiceState, SyncClient, SyncResult, UpdateProgressRequest
from src.utils.ebook_utils import EbookParser
from src.utils.logging_utils import sanitize_log_data

logger = logging.getLogger(__name__)

class HardcoverSyncClient(SyncClient):
    """Push-only SyncClient for Hardcover — receives progress, never sources it."""

    def __init__(self, hardcover_client: HardcoverClient, ebook_parser: EbookParser,
                 abs_client=None, database_service=None, hardcover_service=None):
        super().__init__(ebook_parser)
        self.hardcover_client = hardcover_client
        self.abs_client = abs_client
        self.database_service = database_service
        self.hardcover_service = hardcover_service

    def is_configured(self) -> bool:
        return self.hardcover_client.is_configured()

    def check_connection(self):
        return self.hardcover_client.check_connection()

    def can_be_leader(self) -> bool:
        return False

    def get_supported_sync_types(self) -> set:
        return {'audiobook', 'ebook'}

    def get_text_from_current_state(self, book: Book, state: ServiceState) -> str | None:
        return None

    # ── Bulk State Fetching ───────────────────────────────────────────

    def fetch_bulk_state(self) -> dict | None:
        """Push-only client — no bulk state needed."""
        return None

    # ── get_service_state ─────────────────────────────────────────────

    def get_service_state(self, book: Book, prev_state: State | None,
                          title_snip: str = "", bulk_context: dict = None) -> ServiceState | None:
        """Push-only client — never reports state (excluded from leader election)."""
        return None

    # ── Edition Selection ─────────────────────────────────────────────

    def select_edition_id(self, book, hardcover_details):
        """Select the appropriate edition based on sync source.

        Delegates to HardcoverService if available, otherwise uses local logic.
        """
        if self.hardcover_service:
            return self.hardcover_service.select_edition_id(book, hardcover_details)
        sync_source = getattr(book, 'sync_source', None)
        if sync_source == 'audiobook' and hardcover_details.hardcover_audio_edition_id:
            return hardcover_details.hardcover_audio_edition_id
        return hardcover_details.hardcover_edition_id

    # ── Cached ID Helpers ─────────────────────────────────────────────

    def _ensure_user_book(self, book, hardcover_details):
        """Return cached user_book dict or fetch and cache IDs."""
        if hardcover_details.hardcover_user_book_id:
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

    # ── HC Status Transitions During Progress Push ────────────────────

    def _handle_status_transition(self, book, hardcover_details, current_status, percentage, is_finished):
        """Handle Hardcover-side status transitions based on progress."""
        new_status = current_status

        if is_finished and current_status not in (HC_READ, HC_DNF):
            new_status = HC_READ
        elif percentage > PROGRESS_START_THRESHOLD and current_status in (HC_WANT_TO_READ, HC_PAUSED):
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
                    book_title=sanitize_log_data(book.title),
                    direction='push', action='status_transition',
                    success=False, error_message=str(e),
                    detail={'from': current_status, 'to': new_status},
                )
                logger.error(f"Failed to update Hardcover status: {e}")
                return current_status
            hardcover_details.hardcover_status_id = new_status
            self.database_service.save_hardcover_details(hardcover_details)
            record_write('Hardcover', book.id, {'status': new_status})

            status_names = {1: 'Want to Read', 2: 'Currently Reading', 3: 'Read', 4: 'Paused', 5: 'DNF'}
            log_hardcover_action(
                self.database_service, abs_id=book.abs_id,
                book_title=sanitize_log_data(book.title),
                direction='push', action='status_transition',
                detail={'from': current_status, 'to': new_status,
                        'label': status_names.get(new_status, str(new_status))},
            )
            logger.info(f"Hardcover: '{sanitize_log_data(book.title)}' status → {status_names.get(new_status, new_status)}")

        return new_status

    # ── Progress Updates ──────────────────────────────────────────────

    def update_progress(self, book: Book, request: UpdateProgressRequest) -> SyncResult:
        """Update progress in Hardcover. Uses cached IDs to minimize API calls."""
        if not self.is_configured() or not self.database_service:
            return SyncResult(None, False)

        percentage = request.locator_result.percentage

        hardcover_details = self.database_service.get_hardcover_details(book.id)
        if not hardcover_details or not hardcover_details.hardcover_book_id:
            return SyncResult(None, False)

        ub = self._ensure_user_book(book, hardcover_details)
        if not ub:
            return SyncResult(None, False)

        audio_seconds = hardcover_details.hardcover_audio_seconds or 0
        is_audiobook = getattr(book, 'sync_source', None) == 'audiobook'

        edition_id = self.select_edition_id(book, hardcover_details)

        if is_audiobook and audio_seconds > 0:
            return self._update_audiobook_progress(book, hardcover_details, ub, percentage, audio_seconds, edition_id)

        # --- PAGE-BASED PATH ---
        total_pages = hardcover_details.hardcover_pages or 0

        if total_pages <= 0:
            if total_pages == -1:
                return SyncResult(None, False)

            # Lazy fallback for books matched before edition resolution was added
            if self.hardcover_service:
                logger.info(f"Hardcover: Pages are 0 for {sanitize_log_data(book.title)}, resolving editions...")
                self.hardcover_service.resolve_editions(hardcover_details)
                total_pages = hardcover_details.hardcover_pages or 0
                audio_seconds = hardcover_details.hardcover_audio_seconds or 0
                edition_id = self.select_edition_id(book, hardcover_details)
                if total_pages == -1 and audio_seconds > 0:
                    return self._update_audiobook_progress(book, hardcover_details, ub, percentage, audio_seconds, edition_id)
                if total_pages <= 0:
                    return SyncResult(None, False)
            else:
                return SyncResult(None, False)

        if total_pages <= 0:
            page_num = 0
        elif percentage <= 0:
            page_num = 0
        else:
            page_num = max(1, min(int(total_pages * percentage), total_pages))

        is_finished = percentage > PROGRESS_COMPLETE_THRESHOLD or (total_pages > 0 and page_num == total_pages)
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

            record_write('Hardcover', book.id, updated_state)
            return SyncResult(actual_pct, True, updated_state)

        except Exception as e:
            logger.error(f"Failed to update Hardcover progress: {e}")
            return SyncResult(None, False)

    def _update_audiobook_progress(self, book, hardcover_details, ub, percentage, audio_seconds, edition_id=None):
        """Update Hardcover progress using progress_seconds for audiobook editions."""
        is_finished = percentage > PROGRESS_COMPLETE_THRESHOLD
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

            record_write('Hardcover', book.id, updated_state)
            return SyncResult(percentage, True, updated_state)

        except Exception as e:
            logger.error(f"Failed to update Hardcover audiobook progress: {e}")
            return SyncResult(None, False)
