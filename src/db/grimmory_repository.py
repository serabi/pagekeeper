"""Repository for Grimmory integration: book metadata cache."""

import logging

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .base_repository import BaseRepository
from .models import AbsGrimmoryMigration, GrimmoryBook

logger = logging.getLogger(__name__)


class GrimmoryRepository(BaseRepository):
    def get_grimmory_book(self, filename, server_id="default"):
        return self._get_one(
            GrimmoryBook,
            GrimmoryBook.filename == filename,
            GrimmoryBook.server_id == server_id,
        )

    def get_all_grimmory_books(self, server_id=None):
        if server_id is None:
            return self._get_all(GrimmoryBook)
        with self.get_session() as session:
            rows = session.query(GrimmoryBook).filter(GrimmoryBook.server_id == server_id).all()
            for r in rows:
                session.expunge(r)
            return rows

    def save_grimmory_book(self, grimmory_book):
        with self.get_session() as session:
            existing = (
                session.query(GrimmoryBook)
                .filter(
                    GrimmoryBook.server_id == grimmory_book.server_id,
                    GrimmoryBook.filename == grimmory_book.filename,
                )
                .first()
            )

            if existing:
                for attr in ["title", "authors", "raw_metadata"]:
                    if hasattr(grimmory_book, attr):
                        setattr(existing, attr, getattr(grimmory_book, attr))
                session.flush()
                session.refresh(existing)
                session.expunge(existing)
                return existing
            else:
                try:
                    session.add(grimmory_book)
                    session.flush()
                except IntegrityError:
                    session.rollback()
                    existing = (
                        session.query(GrimmoryBook)
                        .filter(
                            GrimmoryBook.server_id == grimmory_book.server_id,
                            GrimmoryBook.filename == grimmory_book.filename,
                        )
                        .first()
                    )
                    if existing:
                        for attr in ["title", "authors", "raw_metadata"]:
                            if hasattr(grimmory_book, attr):
                                setattr(existing, attr, getattr(grimmory_book, attr))
                        session.flush()
                        session.refresh(existing)
                        session.expunge(existing)
                        return existing
                    raise
                session.refresh(grimmory_book)
                session.expunge(grimmory_book)
                return grimmory_book

    # ── ABS -> Grimmory migration audit ──

    def save_abs_grimmory_migration(self, migration):
        """Insert or update an AbsGrimmoryMigration audit row by its unique key."""
        return self._upsert(
            AbsGrimmoryMigration,
            (
                AbsGrimmoryMigration.abs_id == migration.abs_id,
                AbsGrimmoryMigration.grimmory_book_id == migration.grimmory_book_id,
                AbsGrimmoryMigration.grimmory_instance_id == migration.grimmory_instance_id,
            ),
            migration,
            (
                "book_title",
                "matched_by",
                "finished_at",
                "sessions_written",
                "bookmarks_written",
                "outcome",
                "error_message",
                "created_at",
            ),
        )

    def get_abs_grimmory_migration(self, abs_id, grimmory_book_id, grimmory_instance_id="default"):
        return self._get_one(
            AbsGrimmoryMigration,
            AbsGrimmoryMigration.abs_id == abs_id,
            AbsGrimmoryMigration.grimmory_book_id == grimmory_book_id,
            AbsGrimmoryMigration.grimmory_instance_id == grimmory_instance_id,
        )

    def get_all_abs_grimmory_migrations(self):
        return self._get_all(AbsGrimmoryMigration, order_by=AbsGrimmoryMigration.created_at.desc())

    def delete_grimmory_book(self, filename, server_id="default"):
        try:
            with self.get_session() as session:
                deleted = (
                    session.query(GrimmoryBook)
                    .filter(
                        GrimmoryBook.server_id == server_id,
                        GrimmoryBook.filename == filename,
                    )
                    .delete(synchronize_session=False)
                )
                return deleted > 0
        except SQLAlchemyError as e:
            logger.error(f"Failed to delete Grimmory book '{filename}': {e}")
            return False
