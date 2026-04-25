import logging

logger = logging.getLogger(__name__)


class CacheCleanupService:
    """Delete orphaned EPUB cache files not referenced by books or suggestions."""

    def __init__(self, database_service, epub_cache_dir):
        self.database_service = database_service
        self.epub_cache_dir = epub_cache_dir

    def cleanup(self):
        if not self.epub_cache_dir.exists():
            return

        logger.info("Starting ebook cache cleanup...")
        try:
            valid_filenames = set()

            for book in self.database_service.get_all_books():
                if book.ebook_filename:
                    valid_filenames.add(book.ebook_filename)

            for suggestion in self.database_service.get_all_actionable_suggestions():
                for match in suggestion.matches:
                    if match.get("filename"):
                        valid_filenames.add(match["filename"])

            deleted_count = 0
            reclaimed_bytes = 0
            for file_path in self.epub_cache_dir.iterdir():
                if file_path.is_file() and file_path.name not in valid_filenames:
                    try:
                        size = file_path.stat().st_size
                        file_path.unlink()
                        deleted_count += 1
                        reclaimed_bytes += size
                        logger.debug("   Deleted orphaned cache file: %s", file_path.name)
                    except Exception as exc:
                        logger.warning("   Failed to delete %s: %s", file_path.name, exc)

            if deleted_count > 0:
                mb = reclaimed_bytes / (1024 * 1024)
                logger.info("Cache cleanup complete: Removed %s files (%.2f MB)", deleted_count, mb)
            else:
                logger.info("Cache is clean (no orphaned files found)")
        except Exception as exc:
            logger.error("Error during cache cleanup: %s", exc)
