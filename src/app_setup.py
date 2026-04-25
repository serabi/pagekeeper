# pyright: reportMissingImports=false, reportMissingModuleSource=false

import logging
import os
from pathlib import Path

from dependency_injector import providers

from src.api.hardcover_routes import hardcover_bp
from src.api.kosync_admin import kosync_admin_bp
from src.api.kosync_server import kosync_sync_bp
from src.utils.config_loader import ConfigLoader
from src.utils.runtime_config import get_bool, get_str

logger = logging.getLogger(__name__)

container = None
manager = None
database_service = None
SYNC_PERIOD_MINS = 5.0


def _get_float_env(key, default):
    try:
        return float(os.environ.get(key, str(default)))
    except (ValueError, TypeError):
        logger.warning("Invalid '%s' value, defaulting to %s", key, default)
        return float(default)


def _migrate_abs_library_ids(database_service):
    old_lib_id = get_str("ABS_LIBRARY_ID", "")
    new_lib_ids = get_str("ABS_LIBRARY_IDS", "")
    if old_lib_id and not new_lib_ids:
        old_only_search = get_str("ABS_ONLY_SEARCH_IN_ABS_LIBRARY_ID", "false")
        if old_only_search.lower() == "true":
            database_service.set_setting("ABS_LIBRARY_IDS", old_lib_id)
            os.environ["ABS_LIBRARY_IDS"] = old_lib_id
            logger.info("Migrated ABS_LIBRARY_ID '%s' to ABS_LIBRARY_IDS", old_lib_id)


def setup_dependencies(app, test_container=None, logging_reconfigure=None):
    """Initialize database, DI container, shared services, and app.config state."""
    global container, manager, database_service, SYNC_PERIOD_MINS

    from src.db.migration_utils import initialize_database
    from src.services.kosync_service import KosyncService
    from src.utils.debounce_manager import DebounceManager
    from src.utils.rate_limiter import TokenBucketRateLimiter

    database_service = initialize_database(get_str("DATA_DIR", "/data"))

    if database_service:
        ConfigLoader.bootstrap_config(database_service)
        ConfigLoader.load_settings(database_service)
        logger.info("Settings loaded into environment variables")
        _migrate_abs_library_ids(database_service)
        if logging_reconfigure:
            logging_reconfigure()

    SYNC_PERIOD_MINS = _get_float_env("SYNC_PERIOD_MINS", 5)
    logger.info("Globals reloaded from settings (ABS_SERVER=%s)", get_str("ABS_SERVER", ""))

    if test_container is not None:
        container = test_container
    else:
        from src.utils.di_container import create_container

        container = create_container()
        container.database_service.override(providers.Object(database_service))

    manager = container.sync_manager()
    data_dir = container.data_dir()
    ebook_dir = container.books_dir()
    covers_dir = data_dir / "covers"
    covers_dir.mkdir(parents=True, exist_ok=True)

    app.config["container"] = container
    app.config["sync_manager"] = manager
    app.config["database_service"] = database_service
    if hasattr(container, "abs_service"):
        app.config["abs_service"] = container.abs_service()
    else:
        from src.services.abs_service import ABSService

        app.config["abs_service"] = ABSService(container.abs_client())
    app.config["DATA_DIR"] = data_dir
    app.config["EBOOK_DIR"] = ebook_dir
    app.config["COVERS_DIR"] = covers_dir
    app.config["ABS_COLLECTION_NAME"] = get_str("ABS_COLLECTION_NAME", "Synced with KOReader")
    app.config["SUGGESTIONS_ENABLED"] = get_bool("SUGGESTIONS_ENABLED", False)

    rate_limiter = TokenBucketRateLimiter()
    kosync_service = KosyncService(database_service, container, manager, ebook_dir)
    debounce_manager = DebounceManager(database_service, manager, rate_limiter=rate_limiter)
    app.config["kosync_service"] = kosync_service
    app.config["debounce_manager"] = debounce_manager
    app.config["rate_limiter"] = rate_limiter

    app.register_blueprint(kosync_sync_bp)
    app.register_blueprint(kosync_admin_bp)

    app.register_blueprint(hardcover_bp)

    logger.info("Web server dependencies initialized (DATA_DIR=%s)", data_dir)
    return container, manager, database_service


def get_runtime_state():
    return container, manager, database_service, SYNC_PERIOD_MINS
