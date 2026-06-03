# pyright: reportMissingImports=false

from unittest.mock import MagicMock, Mock, patch

import schedule

from src.app_runtime import apply_settings, initialize_abs_listener, reconcile_socket_listener, start_runtime_services


class TestApplySettingsHelpers:
    def setup_method(self):
        schedule.clear()

    def teardown_method(self):
        schedule.clear()

    def test_apply_settings_updates_app_config_flags(self):
        app = MagicMock()
        app.config = {
            "sync_manager": Mock(),
            "abs_listener": None,
            "_abs_listener_server": "",
            "_abs_listener_key": "",
        }

        with (
            patch.dict(
                "os.environ",
                {
                    "SYNC_PERIOD_MINS": "7",
                    "LOG_LEVEL": "INFO",
                    "ABS_COLLECTION_NAME": "Shelf Sync",
                    "SUGGESTIONS_ENABLED": "true",
                    "INSTANT_SYNC_ENABLED": "false",
                    "ABS_SOCKET_ENABLED": "false",
                    "TELEGRAM_ENABLED": "false",
                },
                clear=False,
            ),
            patch("src.utils.logging_utils.reconcile_telegram_logging"),
        ):
            apply_settings(app)

        assert app.config["ABS_COLLECTION_NAME"] == "Shelf Sync"
        assert app.config["SUGGESTIONS_ENABLED"] is True
        jobs = schedule.get_jobs("sync_cycle")
        assert len(jobs) == 1
        assert jobs[0].interval == 7


class TestSocketListenerHelpers:
    @patch("src.services.abs_socket_listener.ABSSocketListener")
    @patch("threading.Thread")
    def test_reconcile_socket_listener_starts_listener(self, mock_thread_cls, mock_listener_cls):
        app = MagicMock()
        app.config = {
            "abs_listener": None,
            "_abs_listener_server": "",
            "_abs_listener_key": "",
            "database_service": Mock(),
            "sync_manager": Mock(),
        }
        mock_listener = Mock()
        mock_listener_cls.return_value = mock_listener
        mock_thread_cls.return_value = Mock()

        with patch.dict(
            "os.environ",
            {
                "INSTANT_SYNC_ENABLED": "true",
                "ABS_SOCKET_ENABLED": "true",
                "ABS_SERVER": "http://abs:13378",
                "ABS_KEY": "secret",
            },
            clear=False,
        ):
            reconcile_socket_listener(app)

        assert app.config["abs_listener"] is mock_listener
        mock_thread_cls.return_value.start.assert_called_once()

    def test_initialize_abs_listener_logs_when_abs_client_unconfigured(self, caplog):
        app = MagicMock()
        app.config = {}
        container = Mock()
        container.abs_client.return_value.is_configured.return_value = False
        caplog.set_level("INFO")

        with patch.dict(
            "os.environ",
            {
                "INSTANT_SYNC_ENABLED": "true",
                "ABS_SOCKET_ENABLED": "true",
            },
            clear=False,
        ):
            listener = initialize_abs_listener(app, container, Mock(), Mock())

        assert listener is None
        assert "ABS Socket.IO listener disabled (ABS client not configured)" in caplog.text


class TestRuntimeStartup:
    def test_start_runtime_services_rejects_non_positive_sync_period(self):
        with patch.dict("os.environ", {"SYNC_PERIOD_MINS": "0"}, clear=False):
            try:
                start_runtime_services(MagicMock(), Mock(), Mock(), Mock())
            except ValueError as exc:
                assert str(exc) == "SYNC_PERIOD_MINS must be an integer greater than 0"
            else:
                raise AssertionError("expected ValueError")
