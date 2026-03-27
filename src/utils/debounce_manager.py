"""
Debounce manager for PageKeeper.

Debounces rapid KoSync PUT events before triggering sync cycles.
Records (book_id, title) events. A background polling thread fires
the sync callback once no new events arrive within the debounce window.
"""

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)


class DebounceManager:
    def __init__(self, database_service, manager, rate_limiter=None, poll_interval=10, stale_seconds=300):
        self._db = database_service
        self._manager = manager
        self._rate_limiter = rate_limiter
        self._poll_interval = poll_interval
        self._stale_seconds = stale_seconds

        self._entries: dict[int, dict] = {}
        self._lock = threading.Lock()
        self._thread_started = False

    def record_event(self, book_id: int, title: str) -> None:
        """Record a PUT event for debounced sync triggering."""
        with self._lock:
            self._entries[book_id] = {
                "last_event": time.time(),
                "title": title,
                "synced": False,
            }
            if not self._thread_started:
                self._thread_started = True
                threading.Thread(target=self._poll_loop, daemon=True).start()

    def _poll_loop(self) -> None:
        """Check periodically for books that stopped receiving PUTs."""
        while True:
            time.sleep(self._poll_interval)
            debounce_seconds = int(os.environ.get("ABS_SOCKET_DEBOUNCE_SECONDS", "30"))
            now = time.time()
            to_sync = []

            with self._lock:
                for book_id, info in self._entries.items():
                    if not info["synced"] and (now - info["last_event"]) > debounce_seconds:
                        info["synced"] = True
                        to_sync.append((book_id, info["title"]))

            for book_id, title in to_sync:
                self._trigger_sync(book_id, title)

            # Clean up stale entries
            with self._lock:
                stale = [k for k, v in self._entries.items() if now - v["last_event"] > self._stale_seconds]
                for k in stale:
                    del self._entries[k]

            # Prune stale rate-limit buckets
            if self._rate_limiter:
                self._rate_limiter.prune()

    def _trigger_sync(self, book_id: int, title: str) -> None:
        """Trigger sync for a debounced book."""
        if not self._manager:
            return
        book = self._db.get_book_by_id(book_id) if self._db else None
        if not book:
            logger.warning(f"KOSync PUT: No book found for id={book_id} — skipping sync")
            return
        logger.info(f"KOSync PUT: Triggering sync for '{title}' (debounced)")
        threading.Thread(
            target=self._manager.sync_cycle,
            kwargs={"target_book_id": book.id},
            daemon=True,
        ).start()
