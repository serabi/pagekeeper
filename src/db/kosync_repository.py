"""Repository for KoSync document operations."""

from datetime import UTC, datetime

from .base_repository import BaseRepository
from .models import KosyncDocument


class KoSyncRepository(BaseRepository):

    def get_kosync_document(self, document_hash):
        return self._get_one(KosyncDocument, KosyncDocument.document_hash == document_hash)

    def save_kosync_document(self, doc):
        with self.get_session() as session:
            doc.last_updated = datetime.now(UTC)
            merged = session.merge(doc)
            session.flush()
            session.refresh(merged)
            session.expunge(merged)
            return merged

    def get_all_kosync_documents(self):
        return self._get_all(KosyncDocument, order_by=KosyncDocument.last_updated.desc())

    def get_unlinked_kosync_documents(self):
        return self._get_all(
            KosyncDocument,
            KosyncDocument.linked_abs_id == None,
            order_by=KosyncDocument.last_updated.desc(),
        )

    def get_linked_kosync_documents(self):
        return self._get_all(
            KosyncDocument,
            KosyncDocument.linked_abs_id != None,
            order_by=KosyncDocument.last_updated.desc(),
        )

    def link_kosync_document(self, document_hash, book_id):
        with self.get_session() as session:
            doc = session.query(KosyncDocument).filter(
                KosyncDocument.document_hash == document_hash
            ).first()
            if doc:
                doc.linked_book_id = book_id
                doc.last_updated = datetime.now(UTC)
                return True
            return False

    def unlink_kosync_document(self, document_hash):
        with self.get_session() as session:
            doc = session.query(KosyncDocument).filter(
                KosyncDocument.document_hash == document_hash
            ).first()
            if doc:
                doc.linked_abs_id = None
                doc.linked_book_id = None
                doc.last_updated = datetime.now(UTC)
                return True
            return False

    def delete_kosync_document(self, document_hash):
        return self._delete_one(KosyncDocument, KosyncDocument.document_hash == document_hash)

    def get_kosync_document_by_linked_book_id(self, book_id):
        return self._get_one(KosyncDocument, KosyncDocument.linked_book_id == book_id)

    def get_kosync_documents_for_book_by_book_id(self, book_id):
        return self._get_all(KosyncDocument, KosyncDocument.linked_book_id == book_id)

    def get_kosync_doc_by_filename(self, filename):
        if filename is None:
            return None
        return self._get_one(KosyncDocument, KosyncDocument.filename == filename)

    def get_kosync_doc_by_booklore_id(self, booklore_id):
        if booklore_id is None:
            return None
        return self._get_one(KosyncDocument, KosyncDocument.booklore_id == str(booklore_id))

