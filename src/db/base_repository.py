# pyright: reportMissingImports=false

"""Base repository with shared query helpers to reduce boilerplate."""

import logging
from contextlib import contextmanager

from sqlalchemy.exc import IntegrityError

from src.utils.logging_utils import sanitize_log_data

logger = logging.getLogger(__name__)


class BaseRepository:
    """Provides common query patterns for domain repositories."""

    def __init__(self, db_manager):
        self.db_manager = db_manager

    @contextmanager
    def get_session(self):
        """Context manager for database sessions with automatic commit/rollback."""
        session = self.db_manager.get_session()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error("Database error: %s", sanitize_log_data(e))
            raise
        finally:
            session.close()

    def _expunge_items(self, session, items):
        for item in items:
            session.expunge(item)
        return items

    def _query_and_expunge(self, session, query, first=False):
        if first:
            obj = query.first()
            if obj:
                session.expunge(obj)
            return obj
        items = query.all()
        return self._expunge_items(session, items)

    def _paginate(self, query, page=1, per_page=50):
        safe_page = max(1, int(page))
        safe_per_page = max(1, int(per_page))
        total = query.count()
        items = query.offset((safe_page - 1) * safe_per_page).limit(safe_per_page).all()
        return items, total

    def _get_one(self, model, *filters):
        """Query a single row, expunge and return it (or None)."""
        with self.get_session() as session:
            query = session.query(model).filter(*filters)
            return self._query_and_expunge(session, query, first=True)

    def _get_all(self, model, *filters, order_by=None):
        """Query multiple rows, expunge and return them."""
        with self.get_session() as session:
            query = session.query(model)
            if filters:
                query = query.filter(*filters)
            if order_by is not None:
                query = query.order_by(order_by)
            return self._query_and_expunge(session, query)

    def _delete_one(self, model, *filters):
        """Find and delete a single row. Returns True if deleted."""
        with self.get_session() as session:
            obj = session.query(model).filter(*filters).first()
            if obj:
                session.delete(obj)
                return True
            return False

    def _save_new(self, obj):
        """Insert a new object, flush/refresh/expunge and return it."""
        with self.get_session() as session:
            session.add(obj)
            session.flush()
            session.refresh(obj)
            session.expunge(obj)
            return obj

    def _upsert(self, model, lookup_filters, obj, update_attrs):
        """Find existing by filters and update attrs, or insert new. Returns the saved object."""
        with self.get_session() as session:
            existing = session.query(model).filter(*lookup_filters).first()
            if existing:
                for attr in update_attrs:
                    if hasattr(obj, attr):
                        setattr(existing, attr, getattr(obj, attr))
                session.flush()
                session.refresh(existing)
                session.expunge(existing)
                return existing
            else:
                try:
                    session.add(obj)
                    session.flush()
                except IntegrityError:
                    session.rollback()
                    existing = session.query(model).filter(*lookup_filters).first()
                    if existing:
                        for attr in update_attrs:
                            if hasattr(obj, attr):
                                setattr(existing, attr, getattr(obj, attr))
                        session.flush()
                        session.refresh(existing)
                        session.expunge(existing)
                        return existing
                    raise
                session.refresh(obj)
                session.expunge(obj)
                return obj
