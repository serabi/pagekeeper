"""Service for reading status transitions, progress updates, and related side effects."""

import logging
import time
from datetime import date

from src.db.models import State
from src.services.reading_date_service import (
    pull_reading_dates,
    push_booklore_read_status,
    push_completion_to_clients,
)
from src.utils.logging_utils import sanitize_log_data

logger = logging.getLogger(__name__)


class ReadingService:

    def __init__(self, database_service):
        self.database_service = database_service

    @staticmethod
    def max_progress(states, as_percent=False):
        """Return the highest percentage from a list of State objects.

        If as_percent=True, returns 0-100 (rounded to 1 decimal).
        Otherwise returns 0.0-1.0.
        """
        raw = 0.0
        for state in states:
            pct = getattr(state, 'percentage', None)
            if pct is not None:
                raw = max(raw, float(pct))
        if as_percent:
            return min(round(raw * 100, 1), 100.0)
        return raw

    def pull_started_at(self, abs_id, container):
        """Pull started_at from Hardcover/ABS before falling back to today."""
        try:
            dates = pull_reading_dates(abs_id, container, self.database_service)
            return dates.get('started_at', date.today().isoformat())
        except Exception:
            return date.today().isoformat()

    def update_status(self, abs_id, new_status, container, *, allowed_from=None):
        """Consolidate status transition logic.

        Parameters:
            abs_id: Book identifier.
            new_status: Target status ('active', 'completed', 'paused', 'dnf', 'not_started').
            container: DI container for accessing sync clients.
            allowed_from: If set, a set/tuple of statuses the book must currently be in.
                          Returns an error result if the current status is not in this set.

        Returns a dict with 'success', 'status', 'previous_status', and optionally 'error'.
        """
        valid_statuses = {'active', 'completed', 'paused', 'dnf', 'not_started'}
        if new_status not in valid_statuses:
            return {'success': False, 'error': f"Invalid status. Must be one of: {', '.join(sorted(valid_statuses))}"}

        book = self.database_service.get_book(abs_id)
        if not book:
            return {'success': False, 'error': 'Book not found'}

        old_status = book.status

        if allowed_from is not None and old_status not in allowed_from:
            return {'success': False, 'error': f"Cannot change to '{new_status}' from status '{old_status}'"}

        if old_status == new_status:
            return {'success': True, 'status': new_status, 'previous_status': old_status}

        # Apply status change
        book.status = new_status
        if new_status == 'active':
            book.activity_flag = False
        self.database_service.save_book(book)

        # Auto-create journal entries for transitions
        event_map = {
            'completed': 'finished',
            'paused': 'paused',
            'dnf': 'dnf',
        }
        event = event_map.get(new_status)
        if event:
            pct = 1.0 if event == 'finished' else None
            self.database_service.add_reading_journal(abs_id, event=event, percentage=pct)

        # Auto-set dates
        today = date.today().isoformat()
        if new_status == 'active':
            if not book.started_at:
                self.database_service.update_book_reading_fields(
                    abs_id, started_at=self.pull_started_at(abs_id, container)
                )
                self.database_service.add_reading_journal(abs_id, event='started')
            else:
                self.database_service.add_reading_journal(abs_id, event='resumed')
        elif new_status == 'completed' and not book.finished_at:
            updates = {'finished_at': today}
            if not book.started_at:
                updates['started_at'] = self.pull_started_at(abs_id, container)
            self.database_service.update_book_reading_fields(abs_id, **updates)

        if new_status in ('active', 'paused', 'dnf', 'completed'):
            logger.info(f"Book status changed to '{new_status}': "
                        f"'{sanitize_log_data(book.abs_title or abs_id)}'")

        # Push status to Hardcover
        try:
            hc_sync = container.hardcover_sync_client()
            if hc_sync.is_configured():
                hc_sync.push_local_status(book, new_status)
        except Exception as e:
            logger.debug(f"Could not push status to Hardcover: {e}")

        # Push Booklore read status for active/completed transitions
        if book.ebook_filename:
            if new_status == 'active' and old_status in ('dnf', 'paused', 'not_started', 'completed'):
                push_booklore_read_status(book, container, 'READING')
            elif new_status == 'completed':
                push_booklore_read_status(book, container, 'COMPLETED')

        return {'success': True, 'status': new_status, 'previous_status': old_status}

    def mark_complete_with_sync(self, abs_id, container, *, perform_delete=False):
        """Full completion flow: push 100% to all clients, record locally, optionally delete.

        This is the books.py mark_complete path which pushes progress to all sync clients
        and handles the delete-after-completion flow.
        """
        from src.blueprints.helpers import cleanup_mapping_resources
        from src.sync_clients.sync_client_interface import LocatorResult, UpdateProgressRequest

        book = self.database_service.get_book(abs_id)
        if not book:
            return {'success': False, 'error': 'Book not found'}

        locator = LocatorResult(percentage=1.0)
        update_req = UpdateProgressRequest(locator_result=locator, txt="Book finished", previous_location=None)

        for client_name, client in container.sync_clients().items():
            if client.is_configured():
                try:
                    if client_name.lower() == 'abs':
                        client.abs_client.mark_finished(abs_id)
                    else:
                        client.update_progress(book, update_req)
                except Exception as e:
                    logger.warning(f"Completion sync to {client_name} failed: {e}")

                state = State(
                    abs_id=abs_id,
                    client_name=client_name.lower(),
                    percentage=1.0,
                    timestamp=int(time.time()),
                    last_updated=int(time.time())
                )
                self.database_service.save_state(state)

        # Record completion locally (skip if already completed — idempotent)
        if book.status != 'completed':
            today = date.today().isoformat()
            reading_updates = {'finished_at': today}
            if not book.started_at:
                reading_updates['started_at'] = self.pull_started_at(abs_id, container)
            if book.finished_at:
                reading_updates['read_count'] = (book.read_count or 1) + 1

            book.status = 'completed'
            self.database_service.save_book(book)
            self.database_service.update_book_reading_fields(abs_id, **reading_updates)
            self.database_service.add_reading_journal(abs_id, event='finished', percentage=1.0)

        # Push READ status to Booklore instances
        if book.ebook_filename:
            push_booklore_read_status(book, container, 'READ')

        if perform_delete:
            cleanup_mapping_resources(book)
            self.database_service.delete_book(abs_id)

        return {'success': True}

    def set_progress(self, abs_id, percentage, container):
        """Save manual progress and propagate to sync clients.

        Returns a dict with 'success' and 'percentage', or 'success' and 'error'.
        """
        book = self.database_service.get_book(abs_id)
        if not book:
            return {'success': False, 'error': 'Book not found'}

        # Mark book as active if it hasn't been started yet
        if percentage > 0 and book.status not in ('active', 'paused', 'dnf', 'completed'):
            book.status = 'active'
            if not book.started_at:
                book.started_at = self.pull_started_at(abs_id, container)
            self.database_service.save_book(book)

        state = State(
            abs_id=abs_id,
            client_name='manual',
            percentage=percentage,
            last_updated=time.time(),
            timestamp=time.time(),
        )
        self.database_service.save_state(state)

        # Trigger sync to propagate progress to other linked services
        try:
            from src.sync_clients.sync_client_interface import LocatorResult, UpdateProgressRequest
            sync_clients = container.sync_clients()
            locator = LocatorResult(percentage=percentage)
            req = UpdateProgressRequest(locator_result=locator)
            for client_name, client in sync_clients.items():
                if client.is_configured():
                    try:
                        client.update_progress(book, req)
                    except Exception as e:
                        logger.debug(f"Progress sync to {client_name} failed: {e}")
        except Exception as e:
            logger.debug(f"Could not propagate progress: {e}")

        return {'success': True, 'percentage': percentage}
