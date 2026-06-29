import json

import pytest

from src.db.grimmory_repository import GrimmoryRepository
from src.db.models import Base, DatabaseManager, GrimmoryBook


@pytest.fixture()
def repository(tmp_path):
    manager = DatabaseManager(str(tmp_path / "grimmory_repository.db"))
    Base.metadata.create_all(manager.engine)
    try:
        yield GrimmoryRepository(manager)
    finally:
        manager.close()


def test_replace_grimmory_book_filename_in_one_repository_call(repository):
    repository.save_grimmory_book(
        GrimmoryBook(
            filename="Mixed_Case.epub",
            title="Mixed Case Book",
            authors="Author",
            raw_metadata=json.dumps({"id": "777", "fileName": "Mixed_Case.epub"}),
            server_id="test",
        )
    )

    saved = repository.replace_grimmory_book_filename(
        "Mixed_Case.epub",
        GrimmoryBook(
            filename="mixed_case.epub",
            title="Mixed Case Book",
            authors="Author",
            raw_metadata=json.dumps({"id": "777", "fileName": "Mixed_Case.epub"}),
            server_id="test",
        ),
    )

    assert saved.filename == "mixed_case.epub"
    assert repository.get_grimmory_book("Mixed_Case.epub", server_id="test") is None
    assert repository.get_grimmory_book("mixed_case.epub", server_id="test").title == "Mixed Case Book"


def test_replace_grimmory_book_filename_keeps_distinct_case_collision_rows(repository):
    repository.save_grimmory_book(
        GrimmoryBook(
            filename="Mixed_Case.epub",
            title="Legacy Title",
            authors="Old Author",
            raw_metadata=json.dumps({"id": "old"}),
            server_id="test",
        )
    )
    repository.save_grimmory_book(
        GrimmoryBook(
            filename="mixed_case.epub",
            title="Existing Title",
            authors="Existing Author",
            raw_metadata=json.dumps({"id": "existing"}),
            server_id="test",
        )
    )

    saved = repository.replace_grimmory_book_filename(
        "Mixed_Case.epub",
        GrimmoryBook(
            filename="mixed_case.epub",
            title="Fresh Title",
            authors="Fresh Author",
            raw_metadata=json.dumps({"id": "fresh"}),
            server_id="test",
        ),
    )

    assert saved.filename == "mixed_case.epub"
    legacy = repository.get_grimmory_book("Mixed_Case.epub", server_id="test")
    assert legacy.title == "Legacy Title"
    assert legacy.authors == "Old Author"
    assert legacy.raw_metadata_dict["id"] == "old"

    normalized = repository.get_grimmory_book("mixed_case.epub", server_id="test")
    assert normalized.title == "Existing Title"
    assert normalized.authors == "Existing Author"
    assert normalized.raw_metadata_dict["id"] == "existing"
