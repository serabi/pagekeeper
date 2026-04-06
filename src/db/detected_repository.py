"""Repository for detected external books."""

from datetime import UTC, datetime

from .base_repository import BaseRepository
from .models import DetectedBook


class DetectedRepository(BaseRepository):
    ACTIVE_STATUSES = ("detected",)

    def get_detected_book(self, source_id, source="abs"):
        return self._get_one(
            DetectedBook,
            DetectedBook.source_id == source_id,
            DetectedBook.source == source,
        )

    def get_active_detected_books(self, limit=None):
        with self.get_session() as session:
            query = (
                session.query(DetectedBook)
                .filter(DetectedBook.status.in_(self.ACTIVE_STATUSES))
                .order_by(DetectedBook.last_seen_at.desc())
            )
            if limit is not None:
                query = query.limit(limit)
            items = query.all()
            for item in items:
                session.expunge(item)
            return items

    def save_detected_book(self, detected_book):
        """Upsert a detected book while preserving dismissed status."""
        filters = [
            DetectedBook.source_id == detected_book.source_id,
            DetectedBook.source == detected_book.source,
        ]
        with self.get_session() as session:
            existing = session.query(DetectedBook).filter(*filters).first()
            now = datetime.now(UTC)
            if existing:
                if existing.status == "dismissed" and detected_book.status == "detected":
                    detected_book.status = "dismissed"

                if detected_book.title:
                    existing.title = detected_book.title
                if detected_book.author:
                    existing.author = detected_book.author
                if detected_book.cover_url:
                    existing.cover_url = detected_book.cover_url
                if detected_book.matches_json is not None:
                    existing.matches_json = detected_book.matches_json
                if detected_book.device:
                    existing.device = detected_book.device
                if detected_book.ebook_filename:
                    existing.ebook_filename = detected_book.ebook_filename

                existing.progress_percentage = detected_book.progress_percentage
                existing.status = detected_book.status
                existing.last_seen_at = detected_book.last_seen_at or now
                if existing.first_detected_at is None:
                    existing.first_detected_at = detected_book.first_detected_at or now

                session.flush()
                session.refresh(existing)
                session.expunge(existing)
                return existing

            session.add(detected_book)
            session.flush()
            session.refresh(detected_book)
            session.expunge(detected_book)
            return detected_book

    def dismiss_detected_book(self, source_id, source="abs"):
        with self.get_session() as session:
            detected = (
                session.query(DetectedBook)
                .filter(
                    DetectedBook.source_id == source_id,
                    DetectedBook.source == source,
                )
                .first()
            )
            if not detected:
                return False
            detected.status = "dismissed"
            detected.last_seen_at = datetime.now(UTC)
            return True

    def resolve_detected_book(self, source_id, source="abs"):
        with self.get_session() as session:
            detected = (
                session.query(DetectedBook)
                .filter(
                    DetectedBook.source_id == source_id,
                    DetectedBook.source == source,
                )
                .first()
            )
            if not detected:
                return False
            detected.status = "resolved"
            detected.last_seen_at = datetime.now(UTC)
            return True
