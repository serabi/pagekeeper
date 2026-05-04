# pyright: reportMissingImports=false

import logging
import os
import signal

from flask import Flask

from src.app_runtime import (
    get_or_create_secret_key,
    handle_exit_signal,
    reconcile_socket_listener,
    reconfigure_logging,
    start_runtime_services,
)
from src.app_setup import get_runtime_state
from src.app_setup import setup_dependencies as _setup_dependencies
from src.app_template_context import inject_global_vars
from src.blueprints import register_blueprints
from src.blueprints.helpers import safe_folder_name
from src.utils.markdown import render_markdown_markup, sanitize_html

logger = logging.getLogger(__name__)

container = None
manager = None
database_service = None
SYNC_PERIOD_MINS = 5.0


# Backward-compatible exports for tests and existing callers
_reconfigure_logging = reconfigure_logging
_reconcile_socket_listener = reconcile_socket_listener


def setup_dependencies(app, test_container=None):
    """Initialize app dependencies and expose runtime globals for legacy callers/tests."""
    global container, manager, database_service, SYNC_PERIOD_MINS
    container, manager, database_service = _setup_dependencies(
        app,
        test_container=test_container,
        logging_reconfigure=reconfigure_logging,
    )
    _, _, _, SYNC_PERIOD_MINS = get_runtime_state()
    return container, manager, database_service


# --- Application Factory ---
def create_app(test_container=None):
    static_dir = os.environ.get("STATIC_DIR", "/app/static")
    template_dir = os.environ.get("TEMPLATE_DIR", "/app/templates")
    app = Flask(__name__, static_folder=static_dir, static_url_path="/static", template_folder=template_dir)
    app.secret_key = get_or_create_secret_key()
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_HTTPONLY"] = True

    setup_dependencies(app, test_container=test_container)

    app.context_processor(inject_global_vars)
    app.jinja_env.globals["safe_folder_name"] = safe_folder_name
    app.jinja_env.filters["sanitize_html"] = sanitize_html
    app.jinja_env.filters["markdown"] = render_markdown_markup

    register_blueprints(app)

    @app.after_request
    def set_security_headers(response):
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    return app, container


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, handle_exit_signal)
    signal.signal(signal.SIGINT, handle_exit_signal)

    app, container = create_app()

    start_runtime_services(app, container, database_service, manager)

    logger.info("Web interface starting on port 4477")
    app.run(host="0.0.0.0", port=4477, debug=False)
