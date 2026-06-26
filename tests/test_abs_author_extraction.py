import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.api.api_clients import _abs_author


def test_abs_author_prefers_author_name():
    assert _abs_author({"authorName": "Jane Doe", "author": "ignored"}) == "Jane Doe"


def test_abs_author_falls_back_to_author_name_lf():
    assert _abs_author({"authorName": None, "authorNameLF": "Doe, Jane"}) == "Doe, Jane"


def test_abs_author_falls_back_to_flat_author():
    assert _abs_author({"authorName": None, "author": "Jane Doe"}) == "Jane Doe"


def test_abs_author_joins_authors_list():
    meta = {"authorName": None, "authors": [{"name": "Jane Doe"}, {"name": "John Roe"}]}
    assert _abs_author(meta) == "Jane Doe, John Roe"


def test_abs_author_skips_malformed_author_entries():
    meta = {"authors": [{"id": "1"}, "not-a-dict", {"name": "Jane Doe"}]}
    assert _abs_author(meta) == "Jane Doe"


def test_abs_author_none_when_absent():
    assert _abs_author({}) is None
    assert _abs_author(None) is None
    assert _abs_author({"authors": []}) is None
