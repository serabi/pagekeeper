"""Safe logging helper for Hardcover sync actions."""

import json
import logging

from src.db.models import HardcoverSyncLog

logger = logging.getLogger(__name__)


def log_hardcover_action(database_service, *, abs_id=None, book_title=None,
                         direction, action, detail=None, success=True, error_message=None):
    """Record a Hardcover sync event. Never raises — logging must not break sync."""
    try:
        detail_json = json.dumps(detail, default=str) if detail else None
        entry = HardcoverSyncLog(
            abs_id=abs_id,
            book_title=book_title,
            direction=direction,
            action=action,
            detail=detail_json,
            success=success,
            error_message=error_message,
        )
        database_service.add_hardcover_sync_log(entry)
    except Exception as e:
        logger.debug(f"Could not write Hardcover sync log: {e}")
