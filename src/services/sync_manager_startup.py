import logging
import time

from src.utils.logging_utils import sanitize_exception

logger = logging.getLogger(__name__)


class SyncManagerStartup:
    """Startup checks and one-time initialization for SyncManager."""

    def __init__(self, sync_clients, library_service, abs_client, migration_service):
        self.sync_clients = sync_clients
        self.library_service = library_service
        self.abs_client = abs_client
        self.migration_service = migration_service

    def run(self):
        for client_name, client in (self.sync_clients or {}).items():
            first_err = RuntimeError("check_connection() returned False")
            try:
                if client.check_connection():
                    logger.info("'%s' connection verified", client_name)
                    continue
            except Exception as err:
                first_err = err

            time.sleep(2)
            try:
                if client.check_connection():
                    logger.info("'%s' connection verified (retry)", client_name)
                else:
                    raise RuntimeError("check_connection() returned False")
            except Exception as exc:
                logger.warning(
                    "'%s' connection failed after retry: %s (first attempt: %s)",
                    client_name,
                    sanitize_exception(exc),
                    sanitize_exception(first_err),
                )

        if self.library_service and self.library_service.cwa_client:
            cwa = self.library_service.cwa_client
            if cwa.is_configured():
                if cwa.check_connection():
                    template = cwa._get_search_template()
                    if template:
                        logger.info("   CWA search template: %s", template)
            else:
                logger.debug("CWA not configured (disabled or missing server URL)")
        else:
            logger.debug("CWA not available (library_service or cwa_client missing)")

        if self.abs_client and self.abs_client.is_configured():
            try:
                if hasattr(self.abs_client, "get_ebook_files") and hasattr(self.abs_client, "search_ebooks"):
                    logger.info("ABS ebook methods available (get_ebook_files, search_ebooks)")
                else:
                    logger.warning("ABS ebook methods missing - ebook search may not work")
            except Exception as exc:
                logger.warning("ABS ebook check failed: %s", sanitize_exception(exc))

        if self.migration_service:
            logger.info("Checking for legacy data to migrate...")
            self.migration_service.migrate_legacy_data()

        hc_client = self.sync_clients.get("Hardcover") if self.sync_clients else None
        if hc_client and getattr(hc_client, "hardcover_service", None):
            try:
                hc_client.hardcover_service.backfill_hardcover_states()
            except Exception as exc:
                logger.warning("Hardcover state backfill failed (non-fatal): %s", sanitize_exception(exc))
