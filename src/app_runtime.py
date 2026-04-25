# pyright: reportMissingImports=false, reportMissingModuleSource=false

import logging
import os
import secrets
import sys
import threading
import time
from pathlib import Path

import schedule
from flask import Flask

from src.api.kosync_server import kosync_sync_bp
from src.utils.runtime_config import get_bool, get_float, get_str
from src.version import get_update_status

logger = logging.getLogger(__name__)


def reconfigure_logging():
    """Update root logger level from LOG_LEVEL."""
    try:
        new_level_str = get_str("LOG_LEVEL", "INFO").upper()
        new_level = getattr(logging, new_level_str, logging.INFO)
        logging.getLogger().setLevel(new_level)
        logger.info("Logging level updated to %s", new_level_str)
    except Exception as exc:
        logger.warning("Failed to reconfigure logging: %s", exc)


def reconcile_socket_listener(app):
    """Start, stop, or restart the ABS socket listener to match current config."""
    from src.services.abs_socket_listener import ABSSocketListener

    instant_sync = get_bool("INSTANT_SYNC_ENABLED", True)
    socket_enabled = get_bool("ABS_SOCKET_ENABLED", True)
    abs_server = get_str("ABS_SERVER", "")
    abs_key = get_str("ABS_KEY", "")
    should_run = instant_sync and socket_enabled and abs_server and abs_key

    current: ABSSocketListener | None = app.config.get("abs_listener")
    current_server = app.config.get("_abs_listener_server", "")
    current_key = app.config.get("_abs_listener_key", "")

    if should_run and current is None:
        listener = ABSSocketListener(
            abs_server_url=abs_server,
            abs_api_token=abs_key,
            database_service=app.config["database_service"],
            sync_manager=app.config["sync_manager"],
        )
        threading.Thread(target=listener.start, daemon=True).start()
        app.config["abs_listener"] = listener
        app.config["_abs_listener_server"] = abs_server
        app.config["_abs_listener_key"] = abs_key
        logger.info("ABS Socket.IO listener started via hot-reload")
        return

    if not should_run and current is not None:
        current.stop()
        app.config["abs_listener"] = None
        app.config["_abs_listener_server"] = ""
        app.config["_abs_listener_key"] = ""
        logger.info("ABS Socket.IO listener stopped via hot-reload")
        return

    if should_run and current is not None and (abs_server != current_server or abs_key != current_key):
        current.stop()
        listener = ABSSocketListener(
            abs_server_url=abs_server,
            abs_api_token=abs_key,
            database_service=app.config["database_service"],
            sync_manager=app.config["sync_manager"],
        )
        threading.Thread(target=listener.start, daemon=True).start()
        app.config["abs_listener"] = listener
        app.config["_abs_listener_server"] = abs_server
        app.config["_abs_listener_key"] = abs_key
        logger.info("ABS Socket.IO listener restarted via hot-reload (credentials changed)")


def apply_settings(app):
    """Hot-reload settings that do not propagate automatically via os.environ."""
    errors = []
    reconfigure_logging()

    try:
        sync_mgr = app.config.get("sync_manager")
        raw_period = get_str("SYNC_PERIOD_MINS", "5")
        new_period = int(raw_period)
        if new_period <= 0:
            raise ValueError("SYNC_PERIOD_MINS must be an integer greater than 0")

        schedule.clear("sync_cycle")
        if sync_mgr:
            schedule.every(new_period).minutes.do(sync_mgr.sync_cycle).tag("sync_cycle")
        logger.info("Sync schedule updated to every %s minutes", new_period)
    except Exception as exc:
        errors.append(f"sync reschedule failed: {exc}")

    try:
        reconcile_socket_listener(app)
    except Exception as exc:
        errors.append(f"socket listener reconciliation failed: {exc}")

    app.config["ABS_COLLECTION_NAME"] = get_str("ABS_COLLECTION_NAME", "Synced with KOReader")
    app.config["SUGGESTIONS_ENABLED"] = get_bool("SUGGESTIONS_ENABLED", False)

    try:
        from src.utils.logging_utils import reconcile_telegram_logging

        reconcile_telegram_logging()
    except Exception as exc:
        errors.append(f"telegram logging reconciliation failed: {exc}")

    if errors:
        error_message = "; ".join(errors)
        logger.error("Failed to apply one or more settings: %s", error_message)
        raise RuntimeError(error_message)

    return True


def wait_for_split_port_healthcheck(timeout=30):
    """Wait for the split-port KoSync server to become available before the first sync."""
    kosync_port = get_str("KOSYNC_PORT", "")
    if not kosync_port or kosync_port == "4477":
        return

    import urllib.request

    url = f"http://127.0.0.1:{kosync_port}/healthcheck"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            logger.info("Split-port KoSync server ready on port %s", kosync_port)
            return
        except Exception:
            time.sleep(1)
    logger.warning("Split-port KoSync server not ready after %ss — proceeding anyway", timeout)


def run_sync_daemon(sync_manager, sync_period_mins):
    """Run the schedule-based background sync loop."""
    try:
        schedule.every(int(sync_period_mins)).minutes.do(sync_manager.sync_cycle).tag("sync_cycle")
        schedule.every(1).minutes.do(sync_manager.check_pending_jobs).tag("check_jobs")
        logger.info("Sync daemon started (period: %s minutes)", sync_period_mins)

        wait_for_split_port_healthcheck()

        try:
            sync_manager.sync_cycle()
        except Exception as exc:
            logger.error("Initial sync cycle failed: %s", exc)

        while True:
            try:
                schedule.run_pending()
                time.sleep(30)
            except Exception as exc:
                logger.error("Sync daemon error: %s", exc)
                time.sleep(60)
    except Exception as exc:
        logger.error("Sync daemon crashed: %s", exc)


def start_sync_daemon_thread(sync_manager, sync_period_mins):
    thread = threading.Thread(target=run_sync_daemon, args=(sync_manager, sync_period_mins), daemon=True)
    thread.start()
    logger.info("Sync daemon thread started")
    return thread


def get_or_create_secret_key() -> str:
    """Return a persistent random secret key, falling back to ephemeral."""
    data_dir = Path(get_str("DATA_DIR", "/data"))
    key_file = data_dir / ".flask_secret_key"
    try:
        if key_file.exists():
            key = key_file.read_text().strip()
            if key:
                return key
        key = secrets.token_hex(32)
        data_dir.mkdir(parents=True, exist_ok=True)
        key_file.write_text(key)
        key_file.chmod(0o600)
        return key
    except Exception:
        logger.warning("Could not persist Flask secret key — using ephemeral key")
        return secrets.token_hex(32)


def log_security_warnings():
    """Log warnings for common security misconfigurations at startup."""
    kosync_user = get_str("KOSYNC_USER", "")
    kosync_key = get_str("KOSYNC_KEY", "")
    kosync_port = get_str("KOSYNC_PORT", "")
    public_url = get_str("KOSYNC_PUBLIC_URL", "")

    if not kosync_user or not kosync_key:
        logger.warning("SECURITY: KOSYNC_USER/KOSYNC_KEY not configured — sync endpoints will reject all requests")
    elif len(kosync_key) < 8:
        logger.warning("SECURITY: KOSYNC_KEY is shorter than 8 characters — consider using a stronger password")

    if not kosync_port or kosync_port == "4477":
        logger.warning(
            "SECURITY: Split-port mode not active — dashboard and sync API share port 4477. "
            "Set KOSYNC_PORT to a different port before exposing sync to the internet."
        )

    if public_url:
        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(public_url)
        safe_netloc = parts.hostname or ""
        if parts.port:
            safe_netloc = f"{safe_netloc}:{parts.port}"
        safe_url = urlunsplit((parts.scheme, safe_netloc, parts.path or "", "", ""))
        logger.info("KOSync public URL: %s", safe_url)
    elif kosync_port and kosync_port != "4477":
        logger.info("Tip: Set KOSYNC_PUBLIC_URL in settings if you expose KOSync through a reverse proxy")


def initialize_abs_listener(app, container, database_service, sync_manager):
    """Start or disable the ABS listener based on runtime config."""
    instant_sync_enabled = get_bool("INSTANT_SYNC_ENABLED", True)
    abs_socket_enabled = get_bool("ABS_SOCKET_ENABLED", True)

    if instant_sync_enabled and abs_socket_enabled and container.abs_client().is_configured():
        from src.services.abs_socket_listener import ABSSocketListener

        abs_listener = ABSSocketListener(
            abs_server_url=get_str("ABS_SERVER", ""),
            abs_api_token=get_str("ABS_KEY", ""),
            database_service=database_service,
            sync_manager=sync_manager,
        )
        threading.Thread(target=abs_listener.start, daemon=True).start()
        app.config["abs_listener"] = abs_listener
        app.config["_abs_listener_server"] = get_str("ABS_SERVER", "")
        app.config["_abs_listener_key"] = get_str("ABS_KEY", "")
        logger.info("ABS Socket.IO listener started (instant sync enabled)")
        return abs_listener

    app.config["abs_listener"] = None
    app.config["_abs_listener_server"] = ""
    app.config["_abs_listener_key"] = ""
    if not instant_sync_enabled:
        logger.info("ABS Socket.IO listener disabled (INSTANT_SYNC_ENABLED=false)")
    elif not abs_socket_enabled:
        logger.info("ABS Socket.IO listener disabled (ABS_SOCKET_ENABLED=false)")
    return None


def start_client_poller(database_service, sync_manager, sync_clients_dict):
    from src.services.client_poller import ClientPoller

    client_poller = ClientPoller(
        database_service=database_service,
        sync_manager=sync_manager,
        sync_clients_dict=sync_clients_dict,
    )
    thread = threading.Thread(target=client_poller.start, daemon=True)
    thread.start()
    return client_poller, thread


def log_ebook_source_configuration(container):
    grimmory_configured = container.grimmory_client().is_configured()
    books_volume_exists = container.books_dir().exists()

    if grimmory_configured:
        logger.info("Grimmory integration enabled - ebooks sourced from API")
    elif books_volume_exists:
        logger.info("Ebooks directory mounted at %s", container.books_dir())
    else:
        logger.info(
            "NO EBOOK SOURCE CONFIGURED: Neither Grimmory integration nor /books volume is available. "
            "New book matches will fail. Enable Grimmory (GRIMMORY_SERVER, GRIMMORY_USER, GRIMMORY_PASSWORD) "
            "or mount the ebooks directory to /books."
        )


def start_split_port_server(app, port):
    def run_sync_only_server(server_port):
        sync_app = Flask(__name__)
        sync_app.config["kosync_service"] = app.config["kosync_service"]
        sync_app.config["debounce_manager"] = app.config["debounce_manager"]
        sync_app.config["rate_limiter"] = app.config["rate_limiter"]
        sync_app.register_blueprint(kosync_sync_bp)

        @sync_app.route("/")
        def sync_health():
            return "Sync Server OK", 200

        sync_app.run(host="0.0.0.0", port=server_port, debug=False, use_reloader=False)

    thread = threading.Thread(target=run_sync_only_server, args=(int(port),), daemon=True)
    thread.start()
    logger.info("Split-Port Mode Active: Sync-only server on port %s", port)
    return thread


def handle_exit_signal(signum, frame):
    logger.warning("Received signal %s - Shutting down...", signum)
    for handler in logger.handlers:
        handler.flush()
    if hasattr(logging.getLogger(), "handlers"):
        for handler in logging.getLogger().handlers:
            handler.flush()
    sys.exit(0)


def start_runtime_services(app, container, database_service, sync_manager):
    logger.info("=== Unified ABS Manager Started (Integrated Mode) ===")
    log_security_warnings()
    sync_period_mins = int(get_float("SYNC_PERIOD_MINS", 5))
    start_sync_daemon_thread(sync_manager, sync_period_mins)
    threading.Thread(target=get_update_status, daemon=True).start()
    initialize_abs_listener(app, container, database_service, sync_manager)
    start_client_poller(database_service, sync_manager, container.sync_clients())
    log_ebook_source_configuration(container)

    sync_port = get_str("KOSYNC_PORT", "")
    if sync_port and int(sync_port) != 4477:
        start_split_port_server(app, sync_port)
