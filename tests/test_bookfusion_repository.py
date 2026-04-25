import tempfile
from pathlib import Path

from src.db.database_service import DatabaseService
from src.db.models import Book, BookfusionBook


def test_unlink_bookfusion_by_book_id_clears_book_and_highlight_links():
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "test.db"
        db_service = DatabaseService(str(db_path))

        book = db_service.save_book(
            Book(
                abs_id="test-abs-id",
                title="Test Book",
                ebook_filename="test.epub",
                kosync_doc_id="doc-1",
                status="active",
            )
        )

        db_service.save_bookfusion_book(
            BookfusionBook(
                bookfusion_id="bf-123",
                title="BF Book",
                matched_book_id=book.id,
            )
        )
        db_service.save_bookfusion_highlights(
            [
                {
                    "bookfusion_book_id": "bf-123",
                    "highlight_id": "hl-1",
                    "content": "Quote",
                    "quote_text": "Quote",
                    "book_title": "BF Book",
                    "chapter_heading": "Chapter 1",
                }
            ]
        )
        db_service.link_bookfusion_highlights_by_book_id("bf-123", book.id)

        db_service.unlink_bookfusion_by_book_id(book.id)

        assert db_service.get_bookfusion_book_by_book_id(book.id) is None
        assert db_service.get_bookfusion_highlights_for_book_by_book_id(book.id) == []

        db_service.db_manager.close()
