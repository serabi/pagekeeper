import logging
import os
from pathlib import Path

from src.api.booklore_client import BookloreClient
from src.db.models import Book, State
from src.sync_clients.sync_client_interface import ServiceState, SyncClient, SyncResult, UpdateProgressRequest
from src.utils.ebook_utils import EbookParser

logger = logging.getLogger(__name__)

class BookloreSyncClient(SyncClient):
    def __init__(self, booklore_client: BookloreClient, ebook_parser: EbookParser, client_name: str = 'BookLore'):
        super().__init__(ebook_parser)
        self.booklore_client = booklore_client
        self.client_name = client_name
        self.delta_kosync_thresh = float(os.getenv("SYNC_DELTA_KOSYNC_PERCENT", 1)) / 100.0

    def is_configured(self) -> bool:
        return self.booklore_client.is_configured()

    def check_connection(self):
        return self.booklore_client.check_connection()

    def fetch_bulk_state(self) -> dict | None:
        if not self.is_configured():
            return None
        books = self.booklore_client.get_all_books()
        if not books:
            return None
        return {(b.get('fileName') or '').lower(): b for b in books if b.get('fileName')}

    def get_supported_sync_types(self) -> set:
        """Booklore participates in both audiobook and ebook sync modes."""
        return {'audiobook', 'ebook'}

    def get_service_state(self, book: Book, prev_state: State | None, title_snip: str = "", bulk_context: dict = None) -> ServiceState | None:
        # FIX: Use original filename if available (Tri-Link), otherwise standard filename
        epub = book.original_ebook_filename or book.ebook_filename

        if bulk_context is not None:
            lookup_key = Path(epub).name.lower() if epub else ''
            book_info = bulk_context.get(lookup_key)
            if book_info:
                bl_pct, _ = self.booklore_client.extract_progress(book_info)
            else:
                bl_pct = None
        else:
            bl_pct, _ = self.booklore_client.get_progress(epub)

        if bl_pct is None:
            logger.warning("BookLore percentage is None - returning None for service state")
            return None

        # Get previous BookLore state
        prev_booklore_pct = prev_state.percentage if prev_state else 0

        delta = abs(bl_pct - prev_booklore_pct)

        return ServiceState(
            current={"pct": bl_pct},
            previous_pct=prev_booklore_pct,
            delta=delta,
            threshold=self.delta_kosync_thresh,
            is_configured=self.booklore_client.is_configured(),
            display=(self.client_name, "{prev:.4%} -> {curr:.4%}"),
            value_formatter=lambda v: f"{v*100:.4f}%"
        )

    def get_text_from_current_state(self, book: Book, state: ServiceState) -> str | None:
        bl_pct = state.current.get('pct')
        epub = book.ebook_filename
        if bl_pct is not None and epub and self.ebook_parser:
            return self.ebook_parser.get_text_at_percentage(epub, bl_pct)
        return None

    def update_progress(self, book: Book, request: UpdateProgressRequest) -> SyncResult:
        # FIX: Use original filename for updates too
        epub = book.original_ebook_filename or book.ebook_filename
        pct = request.locator_result.percentage
        success = self.booklore_client.update_progress(epub, pct, request.locator_result)
        if success:
            try:
                from src.services.write_tracker import record_write
                record_write(self.client_name, book.abs_id)
            except ImportError:
                pass
        updated_state = {
            'pct': pct
        }
        return SyncResult(pct, success, updated_state)
