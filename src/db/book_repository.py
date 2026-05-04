"""Repository for Book, State, and Job entities."""

import logging

from sqlalchemy import func

from .base_repository import BaseRepository
from .models import (
    Book,
    Job,
    KosyncDocument,
    ReadingJournal,
    State,
    StorytellerSubmission,
)

logger = logging.getLogger(__name__)
_UNSET = object()


class BookRepository(BaseRepository):
    # ── Book CRUD ──

    def get_book_by_abs_id(self, abs_id):
        if not abs_id:
            return None
        return self._get_one(Book, Book.abs_id == abs_id)

    def get_book_by_id(self, book_id):
        return self._get_one(Book, Book.id == book_id)

    def get_book_by_ref(self, ref):
        """Resolve a book reference as abs_id first, then integer book_id.

        This keeps legacy ABS-ID URLs working while allowing new routes and
        templates to use the canonical integer primary key.
        """
        if ref is None:
            return None

        if isinstance(ref, int):
            return self.get_book_by_id(ref)

        ref_str = str(ref).strip()
        if not ref_str:
            return None

        book = self.get_book_by_abs_id(ref_str)
        if book is not None:
            return book

        if ref_str.isdigit():
            return self.get_book_by_id(int(ref_str))

        return None

    def get_book_by_kosync_id(self, kosync_id):
        return self._get_one(Book, Book.kosync_doc_id == kosync_id)

    def get_book_by_storyteller_uuid(self, uuid):
        return self._get_one(Book, Book.storyteller_uuid == uuid)

    def get_all_books(self):
        return self._get_all(Book)

    def get_books_by_status(self, status):
        return self._get_all(Book, Book.status == status)

    def search_books(self, query, limit=10):
        """Search books by title (case-insensitive substring match)."""
        if not query or not query.strip():
            return []
        with self.get_session() as session:
            results = session.query(Book).filter(Book.title.ilike(f"%{query}%")).limit(limit).all()
            for r in results:
                session.expunge(r)
            return results

    def get_book_by_ebook_filename(self, filename):
        """Find a book by its ebook filename (current or original)."""
        from sqlalchemy import or_

        return self._get_one(Book, or_(Book.ebook_filename == filename, Book.original_ebook_filename == filename))

    def create_book(self, book):
        return self._save_new(book)

    def save_book(self, book):
        update_attrs = [
            "title",
            "author",
            "subtitle",
            "ebook_filename",
            "original_ebook_filename",
            "kosync_doc_id",
            "transcript_file",
            "status",
            "duration",
            "sync_mode",
            "storyteller_uuid",
            "abs_ebook_item_id",
            "ebook_item_id",
            "activity_flag",
            "custom_cover_url",
            "started_at",
            "finished_at",
            "rating",
            "read_count",
        ]
        if book.id:
            return self._upsert(Book, [Book.id == book.id], book, update_attrs)
        elif book.abs_id:
            return self._upsert(Book, [Book.abs_id == book.abs_id], book, update_attrs)
        else:
            return self._save_new(book)

    def update_book_metadata_overrides(self, book_id, *, title_override=_UNSET, author_override=_UNSET):
        """Update PageKeeper-local metadata override fields for a book."""
        with self.get_session() as session:
            book = session.query(Book).filter(Book.id == book_id).first()
            if not book:
                return None

            if title_override is not _UNSET:
                book.title_override = title_override or None
            if author_override is not _UNSET:
                book.author_override = author_override or None

            session.flush()
            session.refresh(book)
            session.expunge(book)
            return book

    def delete_book(self, book_id):
        with self.get_session() as session:
            session.query(KosyncDocument).filter(KosyncDocument.linked_book_id == book_id).update(
                {KosyncDocument.linked_abs_id: None, KosyncDocument.linked_book_id: None}
            )
            book = session.query(Book).filter(Book.id == book_id).first()
            if book:
                session.delete(book)
                return True
            return False

    def migrate_book_data(self, old_abs_id, new_abs_id):
        """Migrate book identity: update abs_id and resolve state conflicts.

        With book_id as FK, child rows follow the book automatically —
        only abs_id and state dedup need updating.
        """
        with self.get_session() as session:
            try:
                book = session.query(Book).filter(Book.abs_id == old_abs_id).first()
                if not book:
                    logger.warning(f"migrate_book_data: book '{old_abs_id}' not found")
                    return

                # Delete states for the new abs_id that would conflict
                incoming_clients = {
                    r[0] for r in session.query(State.client_name).filter(State.book_id == book.id).all()
                }
                target_book = session.query(Book).filter(Book.abs_id == new_abs_id).first()
                if target_book:
                    if incoming_clients:
                        session.query(State).filter(
                            State.book_id == target_book.id,
                            State.client_name.in_(incoming_clients),
                        ).delete(synchronize_session=False)
                    # Delete the target book so we can reuse its abs_id
                    session.delete(target_book)
                    session.flush()

                # Update the book's abs_id — child rows follow via book_id FK
                book.abs_id = new_abs_id

                # Update denormalized abs_id on child rows
                session.query(State).filter(State.book_id == book.id).update(
                    {State.abs_id: new_abs_id}, synchronize_session=False
                )
                session.query(Job).filter(Job.book_id == book.id).update(
                    {Job.abs_id: new_abs_id}, synchronize_session=False
                )
                session.query(ReadingJournal).filter(ReadingJournal.book_id == book.id).update(
                    {ReadingJournal.abs_id: new_abs_id}, synchronize_session=False
                )
                session.query(StorytellerSubmission).filter(StorytellerSubmission.book_id == book.id).update(
                    {StorytellerSubmission.abs_id: new_abs_id}, synchronize_session=False
                )

                session.query(KosyncDocument).filter(KosyncDocument.linked_abs_id == old_abs_id).update(
                    {KosyncDocument.linked_abs_id: new_abs_id}, synchronize_session=False
                )

                logger.info(f"Migrated book identity from '{old_abs_id}' to '{new_abs_id}'")
            except Exception as e:
                logger.error(f"Failed to migrate book data: {e}")
                raise

    # ── State CRUD ──

    def get_state(self, book_id, client_name):
        return self._get_one(State, State.book_id == book_id, State.client_name == client_name)

    def get_states_for_book(self, book_id):
        return self._get_all(State, State.book_id == book_id)

    def get_all_states(self):
        return self._get_all(State)

    def save_state(self, state):
        if not state.book_id and not state.abs_id:
            logger.error("save_state called without book_id or abs_id — skipping")
            return None
        # Prefer book_id for upsert lookup; fall back to abs_id for backward compat
        if state.book_id:
            lookup = [State.book_id == state.book_id, State.client_name == state.client_name]
        else:
            lookup = [State.abs_id == state.abs_id, State.client_name == state.client_name]
        return self._upsert(
            State,
            lookup,
            state,
            ["last_updated", "percentage", "timestamp", "xpath", "cfi", "abs_id", "book_id"],
        )

    def delete_states_for_book(self, book_id):
        with self.get_session() as session:
            count = session.query(State).filter(State.book_id == book_id).count()
            session.query(State).filter(State.book_id == book_id).delete()
            return count

    # ── Job CRUD ──

    def get_latest_job(self, book_id):
        with self.get_session() as session:
            job = session.query(Job).filter(Job.book_id == book_id).order_by(Job.last_attempt.desc()).first()
            if job:
                session.expunge(job)
            return job

    def get_latest_jobs_bulk(self, book_ids):
        """Fetch the latest job for each book_id in one query.

        Returns a dict of {book_id: Job}.
        """
        if not book_ids:
            return {}
        with self.get_session() as session:
            latest = (
                session.query(
                    Job.book_id,
                    func.max(Job.last_attempt).label("max_ts"),
                )
                .filter(Job.book_id.in_(book_ids))
                .group_by(Job.book_id)
                .subquery()
            )
            rows = (
                session.query(Job)
                .join(
                    latest,
                    (Job.book_id == latest.c.book_id)
                    & (func.coalesce(Job.last_attempt, "1970-01-01") == func.coalesce(latest.c.max_ts, "1970-01-01")),
                )
                .all()
            )
            result = {}
            for job in rows:
                session.expunge(job)
                result[job.book_id] = job
            return result

    def get_jobs_for_book(self, book_id):
        return self._get_all(Job, Job.book_id == book_id, order_by=Job.last_attempt.desc())

    def get_all_jobs(self):
        return self._get_all(Job)

    def save_job(self, job):
        return self._save_new(job)

    def update_latest_job(self, book_id, **kwargs):
        with self.get_session() as session:
            job = session.query(Job).filter(Job.book_id == book_id).order_by(Job.last_attempt.desc()).first()
            if job:
                for key, value in kwargs.items():
                    if hasattr(job, key):
                        setattr(job, key, value)
                    else:
                        logger.warning(f"update_latest_job: unknown attribute '{key}' for job {job.id}")
                session.flush()
                session.refresh(job)
                session.expunge(job)
                return job
            return None

    def delete_jobs_for_book(self, book_id):
        with self.get_session() as session:
            count = session.query(Job).filter(Job.book_id == book_id).count()
            session.query(Job).filter(Job.book_id == book_id).delete()
            return count

    # ── Advanced Queries ──

    def get_books_with_recent_activity(self, limit=10):
        with self.get_session() as session:
            latest = (
                session.query(State.book_id, func.max(State.last_updated).label("max_updated"))
                .group_by(State.book_id)
                .subquery()
            )
            books = (
                session.query(Book)
                .join(latest, Book.id == latest.c.book_id)
                .order_by(latest.c.max_updated.desc())
                .limit(limit)
                .all()
            )
            for book in books:
                session.expunge(book)
            return books

    def get_failed_jobs(self, limit=20):
        with self.get_session() as session:
            jobs = (
                session.query(Job)
                .filter(Job.last_error.isnot(None))
                .order_by(Job.last_attempt.desc())
                .limit(limit)
                .all()
            )
            for job in jobs:
                session.expunge(job)
            return jobs

    def get_statistics(self):
        with self.get_session() as session:
            stats = {
                "total_books": session.query(Book).count(),
                "active_books": session.query(Book).filter(Book.status == "active").count(),
                "paused_books": session.query(Book).filter(Book.status == "paused").count(),
                "dnf_books": session.query(Book).filter(Book.status == "dnf").count(),
                "total_states": session.query(State).count(),
                "total_jobs": session.query(Job).count(),
                "failed_jobs": session.query(Job).filter(Job.last_error.isnot(None)).count(),
            }
            client_counts = session.query(State.client_name, func.count(State.id)).group_by(State.client_name).all()
            stats["states_by_client"] = {client: count for client, count in client_counts}
            return stats
