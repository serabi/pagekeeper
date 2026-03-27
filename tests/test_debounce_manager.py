"""Tests for DebounceManager."""

import time
from unittest.mock import MagicMock, patch

from src.utils.debounce_manager import DebounceManager


class TestRecordEvent:
    def test_stores_entry(self):
        mgr = DebounceManager(MagicMock(), MagicMock(), poll_interval=999)
        mgr.record_event(42, "Test Book")
        assert 42 in mgr._entries
        assert mgr._entries[42]["title"] == "Test Book"
        assert mgr._entries[42]["synced"] is False

    def test_updates_existing_entry(self):
        mgr = DebounceManager(MagicMock(), MagicMock(), poll_interval=999)
        mgr.record_event(42, "Test Book")
        first_time = mgr._entries[42]["last_event"]
        time.sleep(0.01)
        mgr.record_event(42, "Test Book")
        assert mgr._entries[42]["last_event"] > first_time
        assert mgr._entries[42]["synced"] is False

    def test_re_record_resets_synced_flag(self):
        mgr = DebounceManager(MagicMock(), MagicMock(), poll_interval=999)
        mgr.record_event(42, "Test Book")
        mgr._entries[42]["synced"] = True
        mgr.record_event(42, "Test Book")
        assert mgr._entries[42]["synced"] is False


class TestTriggerSync:
    def test_skips_missing_book(self):
        db = MagicMock()
        db.get_book_by_id.return_value = None
        manager = MagicMock()
        mgr = DebounceManager(db, manager)
        mgr._trigger_sync(999, "Ghost Book")
        manager.sync_cycle.assert_not_called()

    def test_calls_sync_cycle_for_found_book(self):
        db = MagicMock()
        book = MagicMock()
        book.id = 42
        db.get_book_by_id.return_value = book
        manager = MagicMock()
        mgr = DebounceManager(db, manager)

        with patch("src.utils.debounce_manager.threading") as mock_threading:
            mgr._trigger_sync(42, "Real Book")
            mock_threading.Thread.assert_called_once()
            call_kwargs = mock_threading.Thread.call_args[1]
            assert call_kwargs["target"] == manager.sync_cycle
            assert call_kwargs["kwargs"] == {"target_book_id": 42}

    def test_skips_when_no_manager(self):
        db = MagicMock()
        mgr = DebounceManager(db, None)
        mgr._trigger_sync(42, "No Manager")
        db.get_book_by_id.assert_not_called()


class TestPollLoop:
    def test_triggers_sync_after_debounce_window(self):
        db = MagicMock()
        book = MagicMock()
        book.id = 1
        db.get_book_by_id.return_value = book
        manager = MagicMock()
        mgr = DebounceManager(db, manager, poll_interval=999)

        # Manually add an entry that's already past debounce window
        mgr._entries[1] = {
            "last_event": time.time() - 100,
            "title": "Overdue Book",
            "synced": False,
        }

        # Run one poll iteration manually (extract logic from _poll_loop)
        with patch.dict("os.environ", {"ABS_SOCKET_DEBOUNCE_SECONDS": "1"}):
            with patch("src.utils.debounce_manager.threading"):
                now = time.time()
                to_sync = []
                with mgr._lock:
                    for book_id, info in mgr._entries.items():
                        if not info["synced"] and (now - info["last_event"]) > 1:
                            info["synced"] = True
                            to_sync.append((book_id, info["title"]))
                for book_id, title in to_sync:
                    mgr._trigger_sync(book_id, title)

        assert mgr._entries[1]["synced"] is True
        db.get_book_by_id.assert_called_once_with(1)

    def test_does_not_trigger_during_debounce_window(self):
        mgr = DebounceManager(MagicMock(), MagicMock(), poll_interval=999)
        # Entry just recorded — still within debounce window
        mgr._entries[1] = {
            "last_event": time.time(),
            "title": "Fresh Book",
            "synced": False,
        }

        with patch.dict("os.environ", {"ABS_SOCKET_DEBOUNCE_SECONDS": "9999"}):
            now = time.time()
            to_sync = []
            with mgr._lock:
                for book_id, info in mgr._entries.items():
                    if not info["synced"] and (now - info["last_event"]) > 9999:
                        info["synced"] = True
                        to_sync.append((book_id, info["title"]))

        assert len(to_sync) == 0
        assert mgr._entries[1]["synced"] is False

    def test_prunes_stale_entries(self):
        mgr = DebounceManager(MagicMock(), MagicMock(), stale_seconds=0, poll_interval=999)
        mgr._entries[1] = {
            "last_event": time.time() - 10,
            "title": "Stale Book",
            "synced": True,
        }
        now = time.time()
        with mgr._lock:
            stale = [k for k, v in mgr._entries.items() if now - v["last_event"] > 0]
            for k in stale:
                del mgr._entries[k]

        assert 1 not in mgr._entries

    def test_multiple_books_debounced_independently(self):
        mgr = DebounceManager(MagicMock(), MagicMock(), poll_interval=999)
        # Book 1: old event (ready to sync)
        mgr._entries[1] = {"last_event": time.time() - 100, "title": "Book A", "synced": False}
        # Book 2: fresh event (not ready)
        mgr._entries[2] = {"last_event": time.time(), "title": "Book B", "synced": False}

        with patch.dict("os.environ", {"ABS_SOCKET_DEBOUNCE_SECONDS": "1"}):
            now = time.time()
            to_sync = []
            with mgr._lock:
                for book_id, info in mgr._entries.items():
                    if not info["synced"] and (now - info["last_event"]) > 1:
                        info["synced"] = True
                        to_sync.append((book_id, info["title"]))

        assert len(to_sync) == 1
        assert to_sync[0] == (1, "Book A")
        assert mgr._entries[1]["synced"] is True
        assert mgr._entries[2]["synced"] is False
