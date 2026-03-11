"""Repository for TBR (To Be Read) list management."""

from sqlalchemy import func

from .base_repository import BaseRepository
from .models import TbrItem

# Fields that can be passed to add/update — keeps validation in one place.
ENRICHMENT_FIELDS = (
    'description', 'page_count', 'rating', 'ratings_count',
    'release_year', 'genres', 'subtitle',
)


class TbrRepository(BaseRepository):

    def get_tbr_items(self, source=None):
        """Get all TBR items, optionally filtered by source.

        Sorted by priority descending (up-next first), then by recency.
        """
        with self.get_session() as session:
            query = session.query(TbrItem)
            if source:
                query = query.filter(TbrItem.source == source)
            query = query.order_by(TbrItem.priority.desc(), TbrItem.added_at.desc())
            items = query.all()
            for item in items:
                session.expunge(item)
            return items

    def get_tbr_item(self, item_id):
        """Get a single TBR item by ID."""
        return self._get_one(TbrItem, TbrItem.id == item_id)

    def add_tbr_item(self, title, author=None, cover_url=None, notes=None,
                     source='manual', hardcover_book_id=None, hardcover_slug=None,
                     ol_work_key=None, isbn=None,
                     hardcover_list_id=None, hardcover_list_name=None,
                     book_abs_id=None, **enrichment):
        """Add a TBR item, deduplicating by hardcover_book_id or ol_work_key.

        Returns (item, created) tuple — created=False if duplicate found.
        Dedup check and insert run in a single session to avoid race conditions.
        Extra keyword arguments matching ENRICHMENT_FIELDS are stored on the item.
        """
        with self.get_session() as session:
            # Dedup by Hardcover book ID
            if hardcover_book_id:
                existing = session.query(TbrItem).filter(
                    TbrItem.hardcover_book_id == hardcover_book_id
                ).first()
                if existing:
                    session.expunge(existing)
                    return existing, False

            # Dedup by Open Library work key
            if ol_work_key:
                existing = session.query(TbrItem).filter(
                    TbrItem.ol_work_key == ol_work_key
                ).first()
                if existing:
                    session.expunge(existing)
                    return existing, False

            extras = {k: v for k, v in enrichment.items() if k in ENRICHMENT_FIELDS and v is not None}

            item = TbrItem(
                title=title, author=author, cover_url=cover_url, notes=notes,
                source=source, hardcover_book_id=hardcover_book_id,
                hardcover_slug=hardcover_slug, ol_work_key=ol_work_key, isbn=isbn,
                hardcover_list_id=hardcover_list_id, hardcover_list_name=hardcover_list_name,
                book_abs_id=book_abs_id, **extras,
            )
            session.add(item)
            session.flush()
            session.refresh(item)
            session.expunge(item)
            return item, True

    def update_tbr_item(self, item_id, **fields):
        """Update arbitrary fields on a TBR item. Returns the updated item or None."""
        ALLOWED = {'title', 'author', 'cover_url', 'notes', 'priority',
                    *ENRICHMENT_FIELDS}
        with self.get_session() as session:
            item = session.query(TbrItem).filter(TbrItem.id == item_id).first()
            if not item:
                return None
            for key, value in fields.items():
                if key in ALLOWED:
                    setattr(item, key, value)
            session.flush()
            session.refresh(item)
            session.expunge(item)
            return item

    def delete_tbr_item(self, item_id):
        """Remove a TBR item. Returns True if deleted."""
        return self._delete_one(TbrItem, TbrItem.id == item_id)

    def link_tbr_to_book(self, item_id, abs_id):
        """Set book_abs_id on a TBR item (linking it to an owned book)."""
        with self.get_session() as session:
            item = session.query(TbrItem).filter(TbrItem.id == item_id).first()
            if not item:
                return None
            item.book_abs_id = abs_id
            session.flush()
            session.refresh(item)
            session.expunge(item)
            return item

    def find_tbr_by_hardcover_id(self, hc_book_id):
        """Find a TBR item by its Hardcover book ID."""
        return self._get_one(TbrItem, TbrItem.hardcover_book_id == hc_book_id)

    def get_tbr_count(self):
        """Return the total number of TBR items."""
        with self.get_session() as session:
            return session.query(func.count(TbrItem.id)).scalar()
