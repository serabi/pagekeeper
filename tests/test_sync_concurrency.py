"""Concurrency tests for SyncManager parallel fetching and sync_cycle locking."""

import threading
import time
from concurrent.futures import TimeoutError as FuturesTimeoutError
from unittest.mock import Mock, MagicMock, patch, PropertyMock

import pytest

from src.sync_manager import SyncManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sync_manager(**overrides):
    """Build a SyncManager with fully mocked dependencies (skips startup_checks)."""
    db = Mock()
    db.get_all_settings.return_value = {}
    db.get_books_by_status.return_value = []
    db.get_all_books.return_value = []
    db.get_book_by_id.return_value = None

    defaults = dict(
        abs_client=Mock(),
        booklore_client=Mock(),
        hardcover_client=Mock(),
        transcriber=Mock(),
        ebook_parser=Mock(),
        database_service=db,
        storyteller_client=Mock(),
        sync_clients={},
        alignment_service=Mock(),
        library_service=Mock(),
        migration_service=Mock(),
        suggestion_service=Mock(),
        background_job_service=Mock(),
        data_dir=None,
        books_dir=None,
        epub_cache_dir='/tmp/test_epub_cache',
    )
    defaults.update(overrides)

    with patch.object(SyncManager, 'startup_checks'):
        mgr = SyncManager(**defaults)
    return mgr


def _make_mock_client(name, state=None, delay=0, error=None):
    """Create a mock sync client whose get_service_state behaves as configured."""
    client = Mock()
    client.is_configured.return_value = True
    client.check_connection.return_value = True
    client.fetch_bulk_state.return_value = None

    def get_service_state(book, prev_state, title_snip, bulk_ctx=None):
        if delay:
            time.sleep(delay)
        if error:
            raise error
        return state

    client.get_service_state = get_service_state
    return client


# ---------------------------------------------------------------------------
# _fetch_states_parallel: one client times out / raises
# ---------------------------------------------------------------------------

class TestFetchStatesParallel:
    """_fetch_states_parallel resilience when individual clients fail."""

    def test_one_client_raises_others_still_return(self):
        """If one client throws, the other clients' states are still collected."""
        good_state = Mock()
        good_state.delta = 0.1
        good_state.current = {'pct': 0.5}

        clients = {
            'Good': _make_mock_client('Good', state=good_state),
            'Bad': _make_mock_client('Bad', error=RuntimeError('API down')),
        }

        mgr = _make_sync_manager(sync_clients=clients)
        # Manually set sync_clients since _setup_sync_clients filters by is_configured
        mgr.sync_clients = clients

        book = Mock()
        book.abs_id = 'test-book'
        result = mgr._fetch_states_parallel(
            book, prev_states_by_client={}, title_snip='Test',
            clients_to_use=clients,
        )

        assert 'Good' in result
        assert result['Good'] is good_state
        assert 'Bad' not in result

    def test_one_client_times_out_others_still_return(self):
        """A slow client that exceeds the timeout does not block results from fast clients."""
        fast_state = Mock()
        fast_state.delta = 0.05
        fast_state.current = {'pct': 0.3}

        clients = {
            'Fast': _make_mock_client('Fast', state=fast_state),
            'Slow': _make_mock_client('Slow', state=Mock(), delay=2),
        }

        mgr = _make_sync_manager(sync_clients=clients)
        mgr.sync_clients = clients

        book = Mock()
        book.abs_id = 'timeout-book'

        result = mgr._fetch_states_parallel(
            book, prev_states_by_client={}, title_snip='Timeout',
            clients_to_use=clients,
        )

        # Fast client should always be present
        assert 'Fast' in result
        assert result['Fast'] is fast_state
        # Slow client may or may not be present depending on executor timeout;
        # the important thing is that the call completes and Fast is not lost

    def test_all_clients_raise_returns_empty(self):
        """When every client fails, result is an empty dict (no crash)."""
        clients = {
            'A': _make_mock_client('A', error=ConnectionError('refused')),
            'B': _make_mock_client('B', error=TimeoutError('too slow')),
        }

        mgr = _make_sync_manager(sync_clients=clients)
        mgr.sync_clients = clients

        book = Mock()
        book.abs_id = 'all-fail'
        result = mgr._fetch_states_parallel(
            book, prev_states_by_client={}, title_snip='Fail',
            clients_to_use=clients,
        )

        assert result == {}

    def test_client_returns_none_is_excluded(self):
        """A client that returns None is not included in results."""
        clients = {
            'NoneClient': _make_mock_client('NoneClient', state=None),
        }

        mgr = _make_sync_manager(sync_clients=clients)
        mgr.sync_clients = clients

        book = Mock()
        book.abs_id = 'none-book'
        result = mgr._fetch_states_parallel(
            book, prev_states_by_client={}, title_snip='None',
            clients_to_use=clients,
        )

        assert 'NoneClient' not in result


# ---------------------------------------------------------------------------
# sync_cycle concurrent access: no state corruption
# ---------------------------------------------------------------------------

class TestSyncCycleConcurrency:
    """sync_cycle called from two threads must not corrupt shared state."""

    def test_concurrent_daemon_calls_one_wins(self):
        """Two daemon (non-targeted) sync_cycle calls: only one runs, the other skips."""
        mgr = _make_sync_manager()
        mgr.database_service.get_books_by_status.return_value = []

        call_count = 0
        original_internal = mgr._sync_cycle_internal

        def counting_internal(target_book_id=None):
            nonlocal call_count
            call_count += 1
            time.sleep(0.2)  # simulate work so overlap is likely
            original_internal(target_book_id)

        mgr._sync_cycle_internal = counting_internal

        t1 = threading.Thread(target=mgr.sync_cycle)
        t2 = threading.Thread(target=mgr.sync_cycle)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        # Exactly one should have run; the other was skipped (non-blocking acquire)
        assert call_count == 1

    def test_targeted_sync_waits_for_daemon(self):
        """Instant-sync (targeted) waits for the daemon lock, then runs."""
        mgr = _make_sync_manager()
        mgr.database_service.get_books_by_status.return_value = []

        book = Mock()
        book.abs_id = 'targeted-book'
        book.id = 42
        book.status = 'active'
        mgr.database_service.get_book_by_id.return_value = book

        call_order = []

        original_internal = mgr._sync_cycle_internal

        def tracking_internal(target_book_id=None):
            label = 'targeted' if target_book_id else 'daemon'
            call_order.append(f'{label}_start')
            time.sleep(0.1)
            original_internal(target_book_id)
            call_order.append(f'{label}_end')

        mgr._sync_cycle_internal = tracking_internal

        # Start daemon first, then targeted shortly after
        t_daemon = threading.Thread(target=mgr.sync_cycle)
        t_targeted = threading.Thread(target=lambda: mgr.sync_cycle(target_book_id=42))

        t_daemon.start()
        time.sleep(0.02)  # let daemon grab the lock first
        t_targeted.start()

        t_daemon.join(timeout=5)
        t_targeted.join(timeout=12)

        # Both should have run (targeted waits up to 10s)
        assert 'daemon_start' in call_order
        assert 'daemon_end' in call_order
        assert 'targeted_start' in call_order
        assert 'targeted_end' in call_order

        # daemon must finish before targeted starts
        daemon_end_idx = call_order.index('daemon_end')
        targeted_start_idx = call_order.index('targeted_start')
        assert daemon_end_idx < targeted_start_idx

    def test_sync_lock_not_held_after_exception(self):
        """If _sync_cycle_internal raises, the lock is still released."""
        mgr = _make_sync_manager()

        mgr._sync_cycle_internal = Mock(side_effect=RuntimeError('unexpected'))

        mgr.sync_cycle()  # should not raise — exception is caught

        # Lock must be released: another acquire should succeed immediately
        acquired = mgr._sync_lock.acquire(blocking=False)
        assert acquired, "Lock was not released after exception"
        mgr._sync_lock.release()

    def test_pending_clears_not_corrupted_by_concurrent_access(self):
        """_pending_clears set modifications under lock are safe across threads."""
        mgr = _make_sync_manager()

        errors = []

        def writer():
            for i in range(100):
                with mgr._pending_clears_lock:
                    mgr._pending_clears.add(i)
                time.sleep(0.001)

        def reader():
            for _ in range(100):
                with mgr._pending_clears_lock:
                    # Just read — should never see a partial state
                    snapshot = set(mgr._pending_clears)
                time.sleep(0.001)

        threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        # All 100 items should be present
        assert len(mgr._pending_clears) == 100
