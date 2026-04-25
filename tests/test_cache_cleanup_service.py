from pathlib import Path
from unittest.mock import Mock

from src.services.cache_cleanup_service import CacheCleanupService


def test_cache_cleanup_service_removes_only_orphaned_files(tmp_path: Path):
    cache_dir = tmp_path / "epub_cache"
    cache_dir.mkdir()

    keep_book = cache_dir / "keep-book.epub"
    keep_suggestion = cache_dir / "keep-suggestion.epub"
    orphan = cache_dir / "orphan.epub"

    keep_book.write_text("book")
    keep_suggestion.write_text("suggestion")
    orphan.write_text("orphan")

    db = Mock()
    db.get_all_books.return_value = [Mock(ebook_filename="keep-book.epub")]
    db.get_all_actionable_suggestions.return_value = [Mock(matches=[{"filename": "keep-suggestion.epub"}])]

    service = CacheCleanupService(db, cache_dir)
    service.cleanup()

    assert keep_book.exists()
    assert keep_suggestion.exists()
    assert not orphan.exists()
