"""Repository for integration entities: Hardcover, Booklore, BookFusion."""

import logging
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .base_repository import BaseRepository
from .models import (
    BookfusionBook,
    BookfusionHighlight,
    BookloreBook,
    HardcoverDetails,
    HardcoverSyncLog,
    StorytellerSubmission,
)

logger = logging.getLogger(__name__)


class IntegrationRepository(BaseRepository):
    # ── Hardcover ──

    def get_hardcover_details(self, abs_id):
        return self._get_one(HardcoverDetails, HardcoverDetails.abs_id == abs_id)

    def save_hardcover_details(self, details):
        return self._upsert(
            HardcoverDetails,
            [HardcoverDetails.abs_id == details.abs_id],
            details,
            [
                "hardcover_book_id",
                "hardcover_slug",
                "hardcover_edition_id",
                "hardcover_pages",
                "hardcover_audio_seconds",
                "isbn",
                "asin",
                "matched_by",
                "hardcover_cover_url",
                "hardcover_user_book_id",
                "hardcover_user_book_read_id",
                "hardcover_status_id",
                "hardcover_audio_edition_id",
            ],
        )

    def delete_hardcover_details(self, abs_id):
        return self._delete_one(HardcoverDetails, HardcoverDetails.abs_id == abs_id)

    def get_all_hardcover_details(self):
        return self._get_all(HardcoverDetails)

    # ── Hardcover Sync Logs ──

    def add_hardcover_sync_log(self, entry):
        return self._save_new(entry)

    def get_hardcover_sync_logs(self, page=1, per_page=50, direction=None, action=None, search=None):
        safe_page = max(1, int(page))
        safe_per_page = max(1, int(per_page))
        with self.get_session() as session:
            query = session.query(HardcoverSyncLog)
            if direction:
                query = query.filter(HardcoverSyncLog.direction == direction)
            if action:
                query = query.filter(HardcoverSyncLog.action == action)
            if search:
                like = f"%{search}%"
                query = query.filter(
                    (HardcoverSyncLog.book_title.ilike(like))
                    | (HardcoverSyncLog.detail.ilike(like))
                    | (HardcoverSyncLog.error_message.ilike(like))
                )
            total = query.count()
            items = (
                query.order_by(HardcoverSyncLog.created_at.desc())
                .offset((safe_page - 1) * safe_per_page)
                .limit(safe_per_page)
                .all()
            )
            for item in items:
                session.expunge(item)
            return items, total

    def prune_hardcover_sync_logs(self, before_date):
        with self.get_session() as session:
            deleted = (
                session.query(HardcoverSyncLog)
                .filter(HardcoverSyncLog.created_at < before_date)
                .delete(synchronize_session=False)
            )
            return deleted

    # ── Storyteller Submissions ──

    def save_storyteller_submission(self, submission):
        """Save or update a storyteller submission. Supersedes any existing active submission for the same book."""
        with self.get_session() as session:
            # Mark any existing active submissions as superseded
            session.query(StorytellerSubmission).filter(
                StorytellerSubmission.abs_id == submission.abs_id,
                StorytellerSubmission.status.in_(["queued", "processing"]),
            ).update({StorytellerSubmission.status: "superseded"}, synchronize_session=False)
            session.add(submission)
            session.flush()
            session.refresh(submission)
            session.expunge(submission)
            return submission

    def get_active_storyteller_submission(self, abs_id):
        """Get the most recent non-terminal submission for a book."""
        with self.get_session() as session:
            sub = (
                session.query(StorytellerSubmission)
                .filter(
                    StorytellerSubmission.abs_id == abs_id,
                    StorytellerSubmission.status.in_(["queued", "processing"]),
                )
                .order_by(StorytellerSubmission.submitted_at.desc())
                .first()
            )
            if sub:
                session.expunge(sub)
            return sub

    def update_storyteller_submission_status(self, submission_id, status, last_checked_at=None,
                                               storyteller_uuid=None, submission_dir=None):
        """Update an existing submission's status without creating a new record."""
        with self.get_session() as session:
            sub = session.query(StorytellerSubmission).filter(StorytellerSubmission.id == submission_id).first()
            if sub:
                sub.status = status
                if last_checked_at is not None:
                    sub.last_checked_at = last_checked_at
                if storyteller_uuid is not None:
                    sub.storyteller_uuid = storyteller_uuid
                if submission_dir is not None:
                    sub.submission_dir = submission_dir

    def get_storyteller_submission(self, abs_id):
        """Get the most recent submission (any status) for a book."""
        with self.get_session() as session:
            sub = (
                session.query(StorytellerSubmission)
                .filter(
                    StorytellerSubmission.abs_id == abs_id,
                )
                .order_by(StorytellerSubmission.submitted_at.desc())
                .first()
            )
            if sub:
                session.expunge(sub)
            return sub

    def get_all_storyteller_submissions_latest(self):
        """Get the most recent submission per book (for dashboard bulk display).

        Returns a dict of {abs_id: StorytellerSubmission}.
        """
        from sqlalchemy import func

        with self.get_session() as session:
            # Subquery: max submitted_at per abs_id
            latest = (
                session.query(
                    StorytellerSubmission.abs_id,
                    func.max(StorytellerSubmission.submitted_at).label("max_ts"),
                )
                .group_by(StorytellerSubmission.abs_id)
                .subquery()
            )

            rows = (
                session.query(StorytellerSubmission)
                .join(
                    latest,
                    (StorytellerSubmission.abs_id == latest.c.abs_id)
                    & (StorytellerSubmission.submitted_at == latest.c.max_ts),
                )
                .all()
            )

            result = {}
            for sub in rows:
                session.expunge(sub)
                result[sub.abs_id] = sub
            return result

    # ── Booklore ──

    def get_booklore_book(self, filename, server_id='default'):
        return self._get_one(
            BookloreBook,
            BookloreBook.filename == filename,
            BookloreBook.server_id == server_id,
        )

    def get_all_booklore_books(self, server_id=None):
        if server_id is None:
            return self._get_all(BookloreBook)
        with self.get_session() as session:
            rows = session.query(BookloreBook).filter(BookloreBook.server_id == server_id).all()
            for r in rows:
                session.expunge(r)
            return rows

    def save_booklore_book(self, booklore_book):
        with self.get_session() as session:
            existing = (
                session.query(BookloreBook)
                .filter(
                    BookloreBook.server_id == booklore_book.server_id,
                    BookloreBook.filename == booklore_book.filename,
                )
                .first()
            )

            if existing:
                for attr in ["title", "authors", "raw_metadata"]:
                    if hasattr(booklore_book, attr):
                        setattr(existing, attr, getattr(booklore_book, attr))
                session.flush()
                session.refresh(existing)
                session.expunge(existing)
                return existing
            else:
                try:
                    session.add(booklore_book)
                    session.flush()
                except IntegrityError:
                    session.rollback()
                    existing = (
                        session.query(BookloreBook)
                        .filter(
                            BookloreBook.server_id == booklore_book.server_id,
                            BookloreBook.filename == booklore_book.filename,
                        )
                        .first()
                    )
                    if existing:
                        for attr in ["title", "authors", "raw_metadata"]:
                            if hasattr(booklore_book, attr):
                                setattr(existing, attr, getattr(booklore_book, attr))
                        session.flush()
                        session.refresh(existing)
                        session.expunge(existing)
                        return existing
                    raise
                session.refresh(booklore_book)
                session.expunge(booklore_book)
                return booklore_book

    def delete_booklore_book(self, filename, server_id='default'):
        try:
            with self.get_session() as session:
                deleted = (
                    session.query(BookloreBook)
                    .filter(
                        BookloreBook.server_id == server_id,
                        BookloreBook.filename == filename,
                    )
                    .delete(synchronize_session=False)
                )
                return deleted > 0
        except SQLAlchemyError as e:
            logger.error(f"Failed to delete Booklore book '{filename}': {e}")
            return False

    # ── BookFusion Highlights ──

    def save_bookfusion_highlights(self, highlights):
        saved = 0
        with self.get_session() as session:
            all_ids = [h["highlight_id"] for h in highlights if h.get("highlight_id")]
            existing_rows = (
                session.query(BookfusionHighlight).filter(BookfusionHighlight.highlight_id.in_(all_ids)).all()
                if all_ids
                else []
            )
            lookup = {row.highlight_id: row for row in existing_rows}

            seen_in_batch = set()
            for h in highlights:
                highlight_id = h.get("highlight_id")
                if not highlight_id or highlight_id in seen_in_batch:
                    continue
                seen_in_batch.add(highlight_id)
                existing = lookup.get(highlight_id)
                if existing:
                    existing.content = h.get("content", "")
                    existing.chapter_heading = h.get("chapter_heading")
                    existing.book_title = h.get("book_title")
                    existing.highlighted_at = h.get("highlighted_at")
                    existing.quote_text = h.get("quote_text")
                else:
                    session.add(
                        BookfusionHighlight(
                            bookfusion_book_id=h.get("bookfusion_book_id"),
                            highlight_id=highlight_id,
                            content=h.get("content", ""),
                            book_title=h.get("book_title"),
                            chapter_heading=h.get("chapter_heading"),
                            highlighted_at=h.get("highlighted_at"),
                            quote_text=h.get("quote_text"),
                        )
                    )
                    saved += 1
        return saved

    def get_bookfusion_highlights(self):
        with self.get_session() as session:
            highlights = (
                session.query(BookfusionHighlight)
                .order_by(BookfusionHighlight.book_title, BookfusionHighlight.id)
                .all()
            )
            session.expunge_all()
            return highlights

    def get_unmatched_bookfusion_highlights(self):
        with self.get_session() as session:
            highlights = (
                session.query(BookfusionHighlight)
                .filter(BookfusionHighlight.matched_abs_id.is_(None))
                .order_by(BookfusionHighlight.book_title, BookfusionHighlight.id)
                .all()
            )
            session.expunge_all()
            return highlights

    def link_bookfusion_highlight(self, highlight_id, abs_id):
        with self.get_session() as session:
            hl = session.query(BookfusionHighlight).filter(BookfusionHighlight.id == highlight_id).first()
            if hl:
                hl.matched_abs_id = abs_id
                return True
            return False

    def link_bookfusion_book(self, bookfusion_book_id, abs_id):
        with self.get_session() as session:
            session.query(BookfusionHighlight).filter(
                BookfusionHighlight.bookfusion_book_id == bookfusion_book_id
            ).update({BookfusionHighlight.matched_abs_id: abs_id}, synchronize_session=False)

    def get_bookfusion_highlights_for_book(self, abs_id):
        with self.get_session() as session:
            highlights = (
                session.query(BookfusionHighlight)
                .filter(BookfusionHighlight.matched_abs_id == abs_id)
                .order_by(BookfusionHighlight.highlighted_at.desc().nullslast(), BookfusionHighlight.id)
                .all()
            )
            session.expunge_all()
            return highlights

    # ── BookFusion Books (Library Catalog) ──

    def save_bookfusion_books(self, books):
        saved = 0
        with self.get_session() as session:
            for b in books:
                bookfusion_id = b.get("bookfusion_id")
                if not bookfusion_id:
                    continue
                existing = session.query(BookfusionBook).filter(BookfusionBook.bookfusion_id == bookfusion_id).first()
                title = b.get("title") or ""
                if title.endswith(".md"):
                    title = title[:-3].strip()

                if existing:
                    existing.title = title or existing.title
                    existing.authors = b.get("authors") or existing.authors
                    existing.filename = b.get("filename") or existing.filename
                    existing.frontmatter = b.get("frontmatter") or existing.frontmatter
                    existing.tags = b.get("tags") or existing.tags
                    existing.series = b.get("series") or existing.series
                    existing.highlight_count = b.get("highlight_count", existing.highlight_count)
                    existing.last_updated = datetime.now(UTC)
                else:
                    session.add(
                        BookfusionBook(
                            bookfusion_id=bookfusion_id,
                            title=title,
                            authors=b.get("authors"),
                            filename=b.get("filename"),
                            frontmatter=b.get("frontmatter"),
                            tags=b.get("tags"),
                            series=b.get("series"),
                            highlight_count=b.get("highlight_count", 0),
                            last_updated=datetime.now(UTC),
                        )
                    )
                    saved += 1
        return saved

    def get_bookfusion_books(self):
        return self._get_all(BookfusionBook, order_by=BookfusionBook.title)

    def is_bookfusion_linked(self, abs_id):
        with self.get_session() as session:
            return session.query(BookfusionBook).filter(BookfusionBook.matched_abs_id == abs_id).first() is not None

    def set_bookfusion_book_match(self, bookfusion_id, abs_id):
        with self.get_session() as session:
            book = session.query(BookfusionBook).filter(BookfusionBook.bookfusion_id == bookfusion_id).first()
            if book:
                book.matched_abs_id = abs_id

    def set_bookfusion_books_hidden(self, bookfusion_ids, hidden):
        with self.get_session() as session:
            session.query(BookfusionBook).filter(BookfusionBook.bookfusion_id.in_(bookfusion_ids)).update(
                {BookfusionBook.hidden: hidden}, synchronize_session=False
            )

    def get_bookfusion_book(self, bookfusion_id):
        return self._get_one(BookfusionBook, BookfusionBook.bookfusion_id == bookfusion_id)

    def get_bookfusion_book_by_abs_id(self, abs_id):
        return self._get_one(BookfusionBook, BookfusionBook.matched_abs_id == abs_id)

    def unlink_bookfusion_by_abs_id(self, abs_id):
        with self.get_session() as session:
            session.query(BookfusionBook).filter(BookfusionBook.matched_abs_id == abs_id).update(
                {BookfusionBook.matched_abs_id: None}, synchronize_session=False
            )
            session.query(BookfusionHighlight).filter(BookfusionHighlight.matched_abs_id == abs_id).update(
                {BookfusionHighlight.matched_abs_id: None}, synchronize_session=False
            )

    def get_bookfusion_highlight_date_range(self, bookfusion_book_ids):
        with self.get_session() as session:
            result = (
                session.query(
                    func.min(BookfusionHighlight.highlighted_at),
                    func.max(BookfusionHighlight.highlighted_at),
                    func.count(BookfusionHighlight.id),
                )
                .filter(
                    BookfusionHighlight.bookfusion_book_id.in_(bookfusion_book_ids),
                    BookfusionHighlight.highlighted_at.isnot(None),
                )
                .first()
            )
            if result and result[2] > 0:
                return result
            return None

    def get_bookfusion_linked_abs_ids(self):
        with self.get_session() as session:
            book_ids = {
                r[0]
                for r in session.query(BookfusionBook.matched_abs_id)
                .filter(BookfusionBook.matched_abs_id.isnot(None))
                .all()
            }
            highlight_ids = {
                r[0]
                for r in session.query(BookfusionHighlight.matched_abs_id)
                .filter(BookfusionHighlight.matched_abs_id.isnot(None))
                .distinct()
                .all()
            }
            return book_ids | highlight_ids

    def get_bookfusion_highlight_counts(self):
        with self.get_session() as session:
            rows = (
                session.query(BookfusionHighlight.matched_abs_id, func.count(BookfusionHighlight.id))
                .filter(BookfusionHighlight.matched_abs_id.isnot(None))
                .group_by(BookfusionHighlight.matched_abs_id)
                .all()
            )
            return {abs_id: count for abs_id, count in rows}
