from unittest.mock import Mock

from src.services.kosync_progress_service import KosyncProgressService


class _ServiceStub:
    def __init__(self):
        self._db = Mock()
        self._container = Mock()
        self._manager = Mock()
        self.start_discovery_if_available = Mock(return_value=False)
        self.run_put_auto_discovery = Mock()
        self.run_get_auto_discovery = Mock()
        self.resolve_book_by_sibling_hash = Mock(return_value=None)
        self.register_hash_for_book = Mock()
        self.serialize_progress = Mock(return_value={"document": "doc"})


def test_handle_put_progress_rejects_invalid_percentage():
    service = _ServiceStub()
    progress = KosyncProgressService(service)

    body, status = progress.handle_put_progress(
        {
            "document": "doc-123",
            "percentage": "not-a-number",
        },
        remote_addr="127.0.0.1",
    )

    assert status == 400
    assert body["error"] == "Invalid percentage value"


def test_handle_get_progress_returns_502_for_unknown_document_without_discovery():
    service = _ServiceStub()
    service._db.get_kosync_document.return_value = None
    service._db.get_book_by_kosync_id.return_value = None
    progress = KosyncProgressService(service)

    body, status = progress.handle_get_progress("missing-doc", remote_addr="127.0.0.1")

    assert status == 502
    assert body["message"] == "Document not found on server"
