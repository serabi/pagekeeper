"""Service for reading status transitions, progress updates, and related side effects."""

import logging
import os
import time
from datetime import date

from src.db.models import State
from src.services.mapping_cleanup_service import cleanup_mapping_resources
from src.services.status_machine import StatusMachine

logger = logging.getLogger(__name__)


class ReadingService:
    def __init__(self, database_service):
        self.database_service = database_service
        self.status_machine = StatusMachine(database_service)

    @staticmethod
    def max_progress(states, as_percent=False):
        """Return the highest percentage from a list of State objects.

        If as_percent=True, returns 0-100 (rounded to 1 decimal).
        Otherwise returns 0.0-1.0.
        """
        raw = 0.0
        for state in states:
            pct = getattr(state, "percentage", None)
            if pct is not None:
                raw = max(raw, float(pct))
        if as_percent:
            return min(round(raw * 100, 1), 100.0)
        return raw

    def pull_started_at(self, book_id, container):
        """Pull started_at from Hardcover/ABS before falling back to today."""
        try:
            dates = container.reading_date_service().pull_reading_dates(book_id)
            return dates.get("started_at", date.today().isoformat())
        except Exception as e:
            logger.warning("Could not pull started_at for book_id=%s, defaulting to today: %s", book_id, e)
            return date.today().isoformat()

    def update_status(self, book_id, new_status, container, *, allowed_from=None):
        """Transition a book's status with all appropriate side effects.

        Delegates to StatusMachine for the actual transition logic.

        Returns a dict with 'success', 'status', 'previous_status', and optionally 'error'.
        """
        book = self.database_service.get_book_by_id(book_id)
        if not book:
            return {"success": False, "error": "Book not found"}

        return self.status_machine.transition(
            book,
            new_status,
            "local",
            container=container,
            allowed_from=allowed_from,
        )

    def mark_complete_with_sync(self, book_id, container, *, perform_delete=False):
        """Full completion flow: push 100% to all clients, record locally, optionally delete.

        This is the books.py mark_complete path which pushes progress to all sync clients
        and handles the delete-after-completion flow.
        """
        from src.sync_clients.sync_client_interface import LocatorResult, UpdateProgressRequest

        book = self.database_service.get_book_by_ref(book_id)
        if not book:
            return {"success": False, "error": "Book not found"}

        locator = LocatorResult(percentage=1.0)
        update_req = UpdateProgressRequest(locator_result=locator, txt="Book finished", previous_location=None)

        for client_name, client in container.sync_clients().items():
            if client.is_configured():
                try:
                    if client_name.lower() == "abs":
                        client.abs_client.mark_finished(book.abs_id)
                    else:
                        client.update_progress(book, update_req)
                except Exception as e:
                    logger.warning(f"Completion sync to {client_name} failed: {e}")

                state = State(
                    abs_id=book.abs_id,
                    book_id=book.id,
                    client_name=client_name.lower(),
                    percentage=1.0,
                    timestamp=int(time.time()),
                    last_updated=int(time.time()),
                )
                self.database_service.save_state(state)

        if book.status != "completed":
            result = self.status_machine.transition(book, "completed", "completion_sync", container=container)
            if not result["success"]:
                return result

        if perform_delete:
            cleanup_mapping_resources(
                book,
                container=container,
                manager=container.sync_manager(),
                database_service=self.database_service,
                abs_service=container.abs_service(),
                grimmory_client=container.grimmory_client_group(),
                collection_name=os.environ.get("ABS_COLLECTION_NAME", "Synced with KOReader"),
            )
            self.database_service.delete_book(book.id)

        return {"success": True}

    def set_progress(self, book_id, percentage, container):
        """Save manual progress and propagate to sync clients.

        Returns a dict with 'success' and 'percentage', or 'success' and 'error'.
        """
        book = self.database_service.get_book_by_id(book_id)
        if not book:
            return {"success": False, "error": "Book not found"}

        if percentage > 0 and book.status not in ("active", "paused", "dnf", "completed"):
            result = self.status_machine.transition(book, "active", "manual_progress", container=container)
            if not result["success"]:
                return result

        state = State(
            abs_id=book.abs_id,
            book_id=book.id,
            client_name="manual",
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

        return {"success": True, "percentage": percentage}
