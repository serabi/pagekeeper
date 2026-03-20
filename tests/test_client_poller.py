"""Tests for ClientPoller — error isolation and timeout handling."""

import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.client_poller import ClientPoller


def _make_book(book_id=1, title='Test Book'):
    book = Mock()
    book.id = book_id
    book.title = title
    return book


def _make_sync_client(configured=True, states=None):
    """Create a mock sync client.

    states: dict mapping book_id -> mock state (or exception).
    """
    client = Mock()
    client.is_configured.return_value = configured

    if states is None:
        states = {}

    def get_state(book, prev_state=None):
        val = states.get(book.id)
        if isinstance(val, Exception):
            raise val
        return val

    client.get_service_state.side_effect = get_state
    return client


def _make_state_result(pct):
    """Create a mock state result with current.get('pct') returning pct."""
    result = Mock()
    result.current = {'pct': pct}
    return result


class TestClientRaisesDoesNotBlockOthers:
    """One client raising should not prevent other clients from being polled."""

    @patch.dict(os.environ, {
        'STORYTELLER_POLL_MODE': 'custom',
        'STORYTELLER_POLL_SECONDS': '10',
        'BOOKLORE_POLL_MODE': 'custom',
        'BOOKLORE_POLL_SECONDS': '10',
        'BOOKLORE_2_POLL_MODE': 'global',
        'HARDCOVER_POLL_MODE': 'global',
    })
    def test_failing_client_does_not_block_next_client(self):
        db = Mock()
        books = [_make_book(1, 'Book A')]
        db.get_books_by_status.return_value = books

        storyteller = _make_sync_client(configured=True)
        storyteller.get_service_state.side_effect = RuntimeError("Storyteller crashed")

        booklore_state = _make_state_result(0.5)
        booklore = _make_sync_client(configured=True, states={1: booklore_state})

        sync_clients = {
            'Storyteller': storyteller,
            'BookLore': booklore,
        }
        sync_manager = Mock()

        poller = ClientPoller(db, sync_manager, sync_clients)
        poller._poll_cycle()

        # Storyteller failed, but BookLore should still have been polled
        booklore.get_service_state.assert_called_once()

    @patch.dict(os.environ, {
        'STORYTELLER_POLL_MODE': 'custom',
        'STORYTELLER_POLL_SECONDS': '10',
        'BOOKLORE_POLL_MODE': 'global',
        'BOOKLORE_2_POLL_MODE': 'global',
        'HARDCOVER_POLL_MODE': 'global',
    })
    def test_per_book_exception_does_not_block_other_books(self):
        """If get_service_state raises for book A, book B should still be checked."""
        book_a = _make_book(1, 'Book A')
        book_b = _make_book(2, 'Book B')

        db = Mock()
        db.get_books_by_status.return_value = [book_a, book_b]

        state_b = _make_state_result(0.3)
        storyteller = Mock()
        storyteller.is_configured.return_value = True

        def get_state(book, prev_state=None):
            if book.id == 1:
                raise ValueError("corrupt state for book A")
            return state_b

        storyteller.get_service_state.side_effect = get_state

        sync_clients = {'Storyteller': storyteller}
        sync_manager = Mock()

        poller = ClientPoller(db, sync_manager, sync_clients)
        poller._poll_cycle()

        # Both books attempted
        assert storyteller.get_service_state.call_count == 2
        # Book B's position was cached
        assert poller._last_known[('Storyteller', 2)] == 0.3

    @patch.dict(os.environ, {
        'STORYTELLER_POLL_MODE': 'custom',
        'STORYTELLER_POLL_SECONDS': '10',
        'BOOKLORE_POLL_MODE': 'global',
        'BOOKLORE_2_POLL_MODE': 'global',
        'HARDCOVER_POLL_MODE': 'global',
    })
    def test_db_failure_in_get_active_books_returns_early(self):
        db = Mock()
        db.get_books_by_status.side_effect = RuntimeError("DB is down")

        storyteller = _make_sync_client(configured=True)
        sync_clients = {'Storyteller': storyteller}
        sync_manager = Mock()

        poller = ClientPoller(db, sync_manager, sync_clients)
        # Should not crash
        poller._poll_cycle()

        # Client state was never queried because we couldn't get books
        storyteller.get_service_state.assert_not_called()


class TestTimeoutHandling:
    """Interval and poll timing tests."""

    @patch.dict(os.environ, {
        'STORYTELLER_POLL_MODE': 'custom',
        'STORYTELLER_POLL_SECONDS': '60',
        'BOOKLORE_POLL_MODE': 'global',
        'BOOKLORE_2_POLL_MODE': 'global',
        'HARDCOVER_POLL_MODE': 'global',
    })
    def test_client_not_polled_before_interval_elapses(self):
        """A client polled recently should be skipped until its interval elapses."""
        db = Mock()
        db.get_books_by_status.return_value = []

        storyteller = _make_sync_client(configured=True)
        sync_clients = {'Storyteller': storyteller}
        sync_manager = Mock()

        poller = ClientPoller(db, sync_manager, sync_clients)

        # First cycle: should poll (last_poll is 0)
        poller._poll_cycle()
        assert db.get_books_by_status.call_count == 1

        # Second cycle immediately after: interval not elapsed, should skip
        db.reset_mock()
        poller._poll_cycle()
        assert db.get_books_by_status.call_count == 0

    @patch.dict(os.environ, {
        'STORYTELLER_POLL_MODE': 'custom',
        'STORYTELLER_POLL_SECONDS': '10',
        'BOOKLORE_POLL_MODE': 'global',
        'BOOKLORE_2_POLL_MODE': 'global',
        'HARDCOVER_POLL_MODE': 'global',
    })
    def test_client_polled_again_after_interval(self):
        """After the interval elapses, the client should be polled again."""
        import time as time_module

        db = Mock()
        db.get_books_by_status.return_value = []

        storyteller = _make_sync_client(configured=True)
        sync_clients = {'Storyteller': storyteller}
        sync_manager = Mock()

        poller = ClientPoller(db, sync_manager, sync_clients)

        # First poll
        poller._poll_cycle()
        assert db.get_books_by_status.call_count == 1

        # Simulate time passing beyond the interval
        poller._last_poll['Storyteller'] -= 20  # subtract 20s, interval is 10s
        db.reset_mock()

        poller._poll_cycle()
        assert db.get_books_by_status.call_count == 1

    @patch.dict(os.environ, {
        'STORYTELLER_POLL_MODE': 'custom',
        'STORYTELLER_POLL_SECONDS': 'not_a_number',
        'BOOKLORE_POLL_MODE': 'global',
        'BOOKLORE_2_POLL_MODE': 'global',
        'HARDCOVER_POLL_MODE': 'global',
    })
    def test_invalid_poll_seconds_uses_default(self):
        """Non-numeric POLL_SECONDS should fall back to the default (300s)."""
        db = Mock()
        db.get_books_by_status.return_value = []
        sync_clients = {'Storyteller': _make_sync_client()}
        sync_manager = Mock()

        poller = ClientPoller(db, sync_manager, sync_clients)
        interval = poller._get_interval('STORYTELLER')

        assert interval == 300

    @patch.dict(os.environ, {
        'STORYTELLER_POLL_MODE': 'global',
        'BOOKLORE_POLL_MODE': 'global',
        'BOOKLORE_2_POLL_MODE': 'global',
        'HARDCOVER_POLL_MODE': 'global',
    })
    def test_global_mode_clients_are_skipped(self):
        """Clients in 'global' poll mode should never be individually polled."""
        db = Mock()
        storyteller = _make_sync_client(configured=True)
        sync_clients = {'Storyteller': storyteller}
        sync_manager = Mock()

        poller = ClientPoller(db, sync_manager, sync_clients)
        poller._poll_cycle()

        db.get_books_by_status.assert_not_called()
        storyteller.get_service_state.assert_not_called()


class TestChangeDetection:
    """Verify that sync is triggered only when progress actually changes."""

    @patch.dict(os.environ, {
        'STORYTELLER_POLL_MODE': 'custom',
        'STORYTELLER_POLL_SECONDS': '10',
        'BOOKLORE_POLL_MODE': 'global',
        'BOOKLORE_2_POLL_MODE': 'global',
        'HARDCOVER_POLL_MODE': 'global',
    })
    @patch('src.services.client_poller.threading')
    def test_sync_triggered_on_position_change(self, mock_threading):
        db = Mock()
        book = _make_book(1, 'Moving Book')
        db.get_books_by_status.return_value = [book]

        state = _make_state_result(0.6)
        storyteller = _make_sync_client(configured=True, states={1: state})
        sync_manager = Mock()

        poller = ClientPoller(db, sync_manager, {'Storyteller': storyteller})

        # First poll: caches position, no sync
        poller._poll_cycle()
        mock_threading.Thread.assert_not_called()

        # Second poll with changed position
        poller._last_poll['Storyteller'] -= 20
        new_state = _make_state_result(0.8)
        storyteller.get_service_state.side_effect = lambda book, prev_state=None: new_state

        with patch('src.services.write_tracker.is_own_write', return_value=False):
            poller._poll_cycle()

        mock_threading.Thread.assert_called_once()
        call_kwargs = mock_threading.Thread.call_args
        assert call_kwargs.kwargs['kwargs'] == {'target_book_id': 1}

    @patch.dict(os.environ, {
        'STORYTELLER_POLL_MODE': 'custom',
        'STORYTELLER_POLL_SECONDS': '10',
        'BOOKLORE_POLL_MODE': 'global',
        'BOOKLORE_2_POLL_MODE': 'global',
        'HARDCOVER_POLL_MODE': 'global',
    })
    @patch('src.services.client_poller.threading')
    def test_own_write_suppresses_sync(self, mock_threading):
        """Changes caused by our own writes should not trigger sync."""
        db = Mock()
        book = _make_book(1, 'Self-updated Book')
        db.get_books_by_status.return_value = [book]

        state = _make_state_result(0.5)
        storyteller = _make_sync_client(configured=True, states={1: state})
        sync_manager = Mock()

        poller = ClientPoller(db, sync_manager, {'Storyteller': storyteller})

        # Cache initial position
        poller._poll_cycle()

        # Change position
        poller._last_poll['Storyteller'] -= 20
        new_state = _make_state_result(0.7)
        storyteller.get_service_state.side_effect = lambda book, prev_state=None: new_state

        with patch('src.services.write_tracker.is_own_write', return_value=True):
            poller._poll_cycle()

        mock_threading.Thread.assert_not_called()
