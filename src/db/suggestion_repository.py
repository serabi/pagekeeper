"""Repository for pending suggestions."""

from .base_repository import BaseRepository
from .models import PendingSuggestion


class SuggestionRepository(BaseRepository):
    ACTIONABLE_STATUSES = ('pending', 'hidden', 'dismissed')

    def get_suggestion(self, source_id, source='abs'):
        return self._get_one(
            PendingSuggestion,
            PendingSuggestion.source_id == source_id,
            PendingSuggestion.source == source,
        )

    def get_pending_suggestion(self, source_id, source='abs'):
        return self._get_one(
            PendingSuggestion,
            PendingSuggestion.source_id == source_id,
            PendingSuggestion.source == source,
            PendingSuggestion.status == 'pending',
        )

    def suggestion_exists(self, source_id, source='abs'):
        with self.get_session() as session:
            return session.query(PendingSuggestion).filter(
                PendingSuggestion.source_id == source_id,
                PendingSuggestion.source == source,
            ).first() is not None

    def is_suggestion_ignored(self, source_id, source='abs'):
        with self.get_session() as session:
            return session.query(PendingSuggestion).filter(
                PendingSuggestion.source_id == source_id,
                PendingSuggestion.source == source,
                PendingSuggestion.status == 'ignored',
            ).first() is not None

    def save_pending_suggestion(self, suggestion):
        with self.get_session() as session:
            existing = session.query(PendingSuggestion).filter(
                PendingSuggestion.source_id == suggestion.source_id,
                PendingSuggestion.source == suggestion.source,
            ).first()
            if existing and existing.status in ('hidden', 'dismissed') and suggestion.status == 'pending':
                suggestion.status = 'hidden'

        return self._upsert(
            PendingSuggestion,
            [
                PendingSuggestion.source_id == suggestion.source_id,
                PendingSuggestion.source == suggestion.source,
            ],
            suggestion,
            ['title', 'author', 'cover_url', 'matches_json', 'status'],
        )

    def get_all_pending_suggestions(self):
        return self._get_all(
            PendingSuggestion,
            PendingSuggestion.status == 'pending',
            order_by=PendingSuggestion.created_at.desc(),
        )

    def get_all_actionable_suggestions(self):
        return self._get_all(
            PendingSuggestion,
            PendingSuggestion.status.in_(self.ACTIONABLE_STATUSES),
            order_by=PendingSuggestion.created_at.desc(),
        )

    def get_hidden_suggestions(self):
        return self._get_all(
            PendingSuggestion,
            PendingSuggestion.status.in_(('hidden', 'dismissed')),
            order_by=PendingSuggestion.created_at.desc(),
        )

    def delete_pending_suggestion(self, source_id, source='abs'):
        return self._delete_one(
            PendingSuggestion,
            PendingSuggestion.source_id == source_id,
            PendingSuggestion.source == source,
            PendingSuggestion.status == 'pending',
        )

    def resolve_suggestion(self, source_id, source='abs'):
        return self._delete_one(
            PendingSuggestion,
            PendingSuggestion.source_id == source_id,
            PendingSuggestion.source == source,
        )

    def hide_suggestion(self, source_id, source='abs'):
        with self.get_session() as session:
            suggestion = session.query(PendingSuggestion).filter(
                PendingSuggestion.source_id == source_id,
                PendingSuggestion.source == source,
            ).first()
            if suggestion and suggestion.status != 'ignored':
                suggestion.status = 'hidden'
                return True
            return False

    def unhide_suggestion(self, source_id, source='abs'):
        with self.get_session() as session:
            suggestion = session.query(PendingSuggestion).filter(
                PendingSuggestion.source_id == source_id,
                PendingSuggestion.source == source,
            ).first()
            if suggestion and suggestion.status in ('hidden', 'dismissed'):
                suggestion.status = 'pending'
                return True
            return False

    def ignore_suggestion(self, source_id, source='abs'):
        with self.get_session() as session:
            suggestion = session.query(PendingSuggestion).filter(
                PendingSuggestion.source_id == source_id,
                PendingSuggestion.source == source,
            ).first()
            if suggestion:
                suggestion.status = 'ignored'
                return True
            return False

    def clear_stale_suggestions(self):
        """Delete suggestions whose source_id is not in the books table."""
        from sqlalchemy import not_
        from .models import Book
        with self.get_session() as session:
            count = session.query(PendingSuggestion).filter(
                PendingSuggestion.source == 'abs',
                PendingSuggestion.status.in_(self.ACTIONABLE_STATUSES),
                not_(PendingSuggestion.source_id.in_(session.query(Book.abs_id)))
            ).delete(synchronize_session=False)
            return count

    def normalize_dismissed_suggestions(self):
        with self.get_session() as session:
            updated = session.query(PendingSuggestion).filter(
                PendingSuggestion.status == 'dismissed'
            ).update({'status': 'hidden'}, synchronize_session=False)
            return updated
