import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.api.api_clients import ABSClient


@pytest.fixture
def abs_client():
    with patch.dict(os.environ, {"ABS_SERVER": "http://mock-abs", "ABS_KEY": "tok", "ABS_ENABLED": "true"}):
        yield ABSClient()


def _resp(status=200, json_data=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data if json_data is not None else {}
    return r


def test_get_finished_books_filters_is_finished(abs_client):
    abs_client.get_all_progress_raw = MagicMock(
        return_value={
            "item-1": {"isFinished": True, "finishedAt": 1700000000000, "startedAt": 1699000000000,
                       "duration": 3600, "currentTime": 3600, "progress": 1.0},
            "item-2": {"isFinished": False, "progress": 0.4},
        }
    )
    abs_client.get_item_details = MagicMock(
        return_value={"media": {"metadata": {"title": "Done Book", "authorName": "A", "isbn": "111", "asin": "B0X"}}}
    )

    result = abs_client.get_finished_books()

    assert len(result) == 1
    book = result[0]
    assert book["id"] == "item-1"
    assert book["title"] == "Done Book"
    assert book["isbn"] == "111"
    assert book["asin"] == "B0X"
    assert book["finished_at_ms"] == 1700000000000
    assert book["started_at_ms"] == 1699000000000


def test_get_finished_books_empty_when_none_finished(abs_client):
    abs_client.get_all_progress_raw = MagicMock(return_value={"x": {"isFinished": False}})
    assert abs_client.get_finished_books() == []


def test_get_listening_sessions_paginates_and_filters(abs_client):
    page0 = {"sessions": [{"libraryItemId": "a", "timeListening": 100}, {"libraryItemId": "b", "timeListening": 50}],
             "numPages": 2}
    page1 = {"sessions": [{"libraryItemId": "a", "timeListening": 200}], "numPages": 2}
    abs_client.session = MagicMock()
    abs_client.session.get.side_effect = [_resp(200, page0), _resp(200, page1)]

    all_sessions = abs_client.get_listening_sessions()
    assert len(all_sessions) == 3

    abs_client.session.get.side_effect = [_resp(200, page0), _resp(200, page1)]
    only_a = abs_client.get_listening_sessions(item_id="a")
    assert len(only_a) == 2
    assert all(s["libraryItemId"] == "a" for s in only_a)


def test_get_bookmarks_grouped_by_item(abs_client):
    abs_client.session = MagicMock()
    abs_client.session.get.return_value = _resp(
        200,
        {"bookmarks": [
            {"libraryItemId": "a", "title": "Ch1", "time": 10, "createdAt": 1},
            {"libraryItemId": "a", "title": "Ch2", "time": 20, "createdAt": 2},
            {"libraryItemId": "b", "title": "Start", "time": 0, "createdAt": 3},
        ]},
    )

    grouped = abs_client.get_bookmarks()
    assert set(grouped.keys()) == {"a", "b"}
    assert len(grouped["a"]) == 2
    assert grouped["a"][0]["time"] == 10


def test_get_bookmarks_returns_none_on_error(abs_client):
    abs_client.session = MagicMock()
    abs_client.session.get.return_value = _resp(500, {})
    assert abs_client.get_bookmarks() is None


def test_get_bookmarks_empty_when_no_bookmarks(abs_client):
    abs_client.session = MagicMock()
    abs_client.session.get.return_value = _resp(200, {"bookmarks": []})
    assert abs_client.get_bookmarks() == {}


def test_get_finished_books_not_configured_returns_empty():
    with patch.dict(os.environ, {"ABS_ENABLED": "false"}, clear=False):
        client = ABSClient()
        assert client.get_finished_books() == []
