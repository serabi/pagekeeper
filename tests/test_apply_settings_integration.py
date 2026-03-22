"""Integration tests for apply_settings hot-reload behaviour."""

import os
from unittest.mock import MagicMock, Mock, patch

import pytest
import schedule

# ---------------------------------------------------------------------------
# Unit-level tests for apply_settings (mock dependencies directly)
# ---------------------------------------------------------------------------

class TestApplySettingsSyncPeriod:
    """SYNC_PERIOD_MINS reschedule logic."""

    def setup_method(self):
        schedule.clear()

    def teardown_method(self):
        schedule.clear()

    def test_valid_sync_period_reschedules(self):
        """apply_settings clears old job and creates a new one with correct period."""
        from src.web_server import apply_settings

        app = MagicMock()
        sync_mgr = Mock()
        app.config = {
            'sync_manager': sync_mgr,
            'abs_listener': None,
            '_abs_listener_server': '',
            '_abs_listener_key': '',
        }

        with patch.dict(os.environ, {
            'SYNC_PERIOD_MINS': '10',
            'LOG_LEVEL': 'INFO',
            'INSTANT_SYNC_ENABLED': 'false',
            'ABS_SOCKET_ENABLED': 'false',
            'TELEGRAM_ENABLED': 'false',
        }), patch('src.utils.logging_utils.reconcile_telegram_logging'):
            apply_settings(app)

        # Should have exactly one sync_cycle job tagged 'sync_cycle'
        jobs = schedule.get_jobs('sync_cycle')
        assert len(jobs) == 1
        assert jobs[0].interval == 10

    def test_invalid_sync_period_non_integer(self):
        """Non-integer SYNC_PERIOD_MINS is collected as an error but does not crash."""
        from src.web_server import apply_settings

        app = MagicMock()
        app.config = {
            'sync_manager': Mock(),
            'abs_listener': None,
            '_abs_listener_server': '',
            '_abs_listener_key': '',
        }

        with patch.dict(os.environ, {
            'SYNC_PERIOD_MINS': 'abc',
            'LOG_LEVEL': 'INFO',
            'INSTANT_SYNC_ENABLED': 'false',
            'ABS_SOCKET_ENABLED': 'false',
            'TELEGRAM_ENABLED': 'false',
        }), patch('src.utils.logging_utils.reconcile_telegram_logging'):
            with pytest.raises(RuntimeError, match='sync reschedule failed'):
                apply_settings(app)

    def test_zero_sync_period_raises(self):
        """SYNC_PERIOD_MINS=0 is rejected."""
        from src.web_server import apply_settings

        app = MagicMock()
        app.config = {
            'sync_manager': Mock(),
            'abs_listener': None,
            '_abs_listener_server': '',
            '_abs_listener_key': '',
        }

        with patch.dict(os.environ, {
            'SYNC_PERIOD_MINS': '0',
            'LOG_LEVEL': 'INFO',
            'INSTANT_SYNC_ENABLED': 'false',
            'ABS_SOCKET_ENABLED': 'false',
            'TELEGRAM_ENABLED': 'false',
        }), patch('src.utils.logging_utils.reconcile_telegram_logging'):
            with pytest.raises(RuntimeError, match='must be an integer greater than 0'):
                apply_settings(app)

    def test_negative_sync_period_raises(self):
        """Negative SYNC_PERIOD_MINS is rejected."""
        from src.web_server import apply_settings

        app = MagicMock()
        app.config = {
            'sync_manager': Mock(),
            'abs_listener': None,
            '_abs_listener_server': '',
            '_abs_listener_key': '',
        }

        with patch.dict(os.environ, {
            'SYNC_PERIOD_MINS': '-5',
            'LOG_LEVEL': 'INFO',
            'INSTANT_SYNC_ENABLED': 'false',
            'ABS_SOCKET_ENABLED': 'false',
            'TELEGRAM_ENABLED': 'false',
        }), patch('src.utils.logging_utils.reconcile_telegram_logging'):
            with pytest.raises(RuntimeError, match='must be an integer greater than 0'):
                apply_settings(app)


# ---------------------------------------------------------------------------
# Socket listener reconciliation
# ---------------------------------------------------------------------------

class TestSocketListenerReconciliation:
    """_reconcile_socket_listener start/stop/restart behaviour."""

    @patch('src.services.abs_socket_listener.ABSSocketListener')
    @patch('threading.Thread')
    def test_starts_listener_when_enabled_and_none_running(self, mock_thread_cls, mock_listener_cls):
        """Listener is created and started when config says enabled and none exists."""
        from src.web_server import _reconcile_socket_listener

        mock_listener = Mock()
        mock_listener_cls.return_value = mock_listener

        mock_thread = Mock()
        mock_thread_cls.return_value = mock_thread

        app = MagicMock()
        app.config = {
            'abs_listener': None,
            '_abs_listener_server': '',
            '_abs_listener_key': '',
            'database_service': Mock(),
            'sync_manager': Mock(),
        }

        with patch.dict(os.environ, {
            'INSTANT_SYNC_ENABLED': 'true',
            'ABS_SOCKET_ENABLED': 'true',
            'ABS_SERVER': 'http://abs:13378',
            'ABS_KEY': 'secret-key',
        }):
            _reconcile_socket_listener(app)

        mock_listener_cls.assert_called_once()
        mock_thread.start.assert_called_once()
        assert app.config['abs_listener'] is mock_listener

    @patch('src.services.abs_socket_listener.ABSSocketListener')
    def test_stops_listener_when_disabled(self, mock_listener_cls):
        """Running listener is stopped when socket is disabled."""
        from src.web_server import _reconcile_socket_listener

        existing_listener = Mock()
        app = MagicMock()
        app.config = {
            'abs_listener': existing_listener,
            '_abs_listener_server': 'http://abs:13378',
            '_abs_listener_key': 'old-key',
            'database_service': Mock(),
            'sync_manager': Mock(),
        }

        with patch.dict(os.environ, {
            'INSTANT_SYNC_ENABLED': 'true',
            'ABS_SOCKET_ENABLED': 'false',
            'ABS_SERVER': 'http://abs:13378',
            'ABS_KEY': 'secret-key',
        }):
            _reconcile_socket_listener(app)

        existing_listener.stop.assert_called_once()
        assert app.config['abs_listener'] is None

    @patch('src.services.abs_socket_listener.ABSSocketListener')
    @patch('threading.Thread')
    def test_restarts_listener_when_credentials_change(self, mock_thread_cls, mock_listener_cls):
        """Listener is restarted when server URL or key change."""
        from src.web_server import _reconcile_socket_listener

        old_listener = Mock()
        new_listener = Mock()
        mock_listener_cls.return_value = new_listener
        mock_thread_cls.return_value = Mock()

        app = MagicMock()
        app.config = {
            'abs_listener': old_listener,
            '_abs_listener_server': 'http://old-server:13378',
            '_abs_listener_key': 'old-key',
            'database_service': Mock(),
            'sync_manager': Mock(),
        }

        with patch.dict(os.environ, {
            'INSTANT_SYNC_ENABLED': 'true',
            'ABS_SOCKET_ENABLED': 'true',
            'ABS_SERVER': 'http://new-server:13378',
            'ABS_KEY': 'new-key',
        }):
            _reconcile_socket_listener(app)

        old_listener.stop.assert_called_once()
        mock_listener_cls.assert_called_once()
        assert app.config['abs_listener'] is new_listener


# ---------------------------------------------------------------------------
# Telegram reconciliation failure
# ---------------------------------------------------------------------------

class TestTelegramReconciliationFailure:
    """Telegram failure is collected but other settings still apply."""

    def setup_method(self):
        schedule.clear()

    def teardown_method(self):
        schedule.clear()

    def test_telegram_failure_collects_error_others_still_apply(self):
        """If reconcile_telegram_logging raises, error is collected in RuntimeError
        but sync schedule and config refresh still happen."""
        from src.web_server import apply_settings

        app = MagicMock()
        sync_mgr = Mock()
        app.config = {
            'sync_manager': sync_mgr,
            'abs_listener': None,
            '_abs_listener_server': '',
            '_abs_listener_key': '',
        }

        with patch.dict(os.environ, {
            'SYNC_PERIOD_MINS': '7',
            'LOG_LEVEL': 'INFO',
            'INSTANT_SYNC_ENABLED': 'false',
            'ABS_SOCKET_ENABLED': 'false',
            'ABS_COLLECTION_NAME': 'MyCollection',
            'SUGGESTIONS_ENABLED': 'true',
        }), patch('src.utils.logging_utils.reconcile_telegram_logging', side_effect=RuntimeError('telegram boom')):
            with pytest.raises(RuntimeError, match='telegram logging reconciliation failed'):
                apply_settings(app)

        # Sync schedule was still updated despite telegram failure
        jobs = schedule.get_jobs('sync_cycle')
        assert len(jobs) == 1
        assert jobs[0].interval == 7

        # Config refresh still happened
        assert app.config['ABS_COLLECTION_NAME'] == 'MyCollection'
        assert app.config['SUGGESTIONS_ENABLED'] is True


# ---------------------------------------------------------------------------
# Route-level: POST /settings → apply_settings called → schedule changed
# ---------------------------------------------------------------------------

class TestSettingsRouteIntegration:
    """Full POST /settings verifying apply_settings is called and schedule is updated."""

    def test_post_settings_calls_apply_and_updates_schedule(self, mock_container, flask_app, client):
        """POST /settings saves settings to DB and calls apply_settings."""
        schedule.clear()

        with patch('src.blueprints.settings_bp.get_database_service',
                   return_value=mock_container.mock_database_service):
            with patch('src.web_server.apply_settings') as mock_apply:
                mock_apply.return_value = True

                resp = client.post('/settings', data={
                    'SYNC_PERIOD_MINS': '15',
                    '_active_tab': 'general',
                }, follow_redirects=False)

        # Should redirect back to settings page
        assert resp.status_code == 302

        # apply_settings was invoked
        mock_apply.assert_called_once()

    def test_post_settings_apply_failure_sets_error_session(self, mock_container, flask_app, client):
        """When apply_settings raises, the session message is an error."""
        with patch('src.blueprints.settings_bp.get_database_service',
                   return_value=mock_container.mock_database_service):
            with patch('src.web_server.apply_settings', side_effect=RuntimeError('boom')):
                resp = client.post('/settings', data={
                    'SYNC_PERIOD_MINS': '15',
                    '_active_tab': 'general',
                }, follow_redirects=False)

        assert resp.status_code == 302

        # Follow the redirect to GET /settings to verify the error message
        with patch('src.version.get_update_status', return_value=(None, False)):
            get_resp = client.get('/settings')
        assert b'Error applying settings' in get_resp.data
