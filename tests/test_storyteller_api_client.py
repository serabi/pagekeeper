import os
from unittest.mock import Mock, patch

import pytest
import requests

from src.api.storyteller_api import StorytellerAPIClient
from src.sync_clients.storyteller_sync_client import StorytellerSyncClient


@patch.dict(
    os.environ,
    {
        "STORYTELLER_API_URL": "http://storyteller:8001",
        "STORYTELLER_USER": "user",
        "STORYTELLER_PASSWORD": "pass",
        "STORYTELLER_RETRY_COOLDOWN_SECONDS": "60",
    },
    clear=False,
)
def test_check_connection_cooldown_suppresses_repeated_login_attempts():
    client = StorytellerAPIClient()
    client.session.post = Mock(side_effect=requests.exceptions.ConnectionError("dns failed"))

    with patch("src.api.storyteller_api.logger") as mock_logger:
        assert client.check_connection() is False
        assert client.check_connection() is False

        assert client.session.post.call_count == 1
        assert mock_logger.error.call_count == 1

        client._connection_retry_after = 0
        client._failure_logged_at = 0
        assert client.check_connection() is False

    assert client.session.post.call_count == 2
    assert mock_logger.error.call_count == 2
    assert "Storyteller login error: dns failed" == mock_logger.error.call_args_list[-1].args[0]


def test_get_position_details_raises_when_request_is_unavailable():
    client = StorytellerAPIClient()

    with patch.object(client, "_make_request", return_value=None):
        with pytest.raises(ConnectionError, match="unavailable"):
            client.get_position_details("book-uuid")


def test_storyteller_sync_client_returns_none_when_position_fetch_fails():
    storyteller_client = Mock()
    storyteller_client.is_configured.return_value = True
    storyteller_client.get_position_details.side_effect = ConnectionError("dns failed")

    sync_client = StorytellerSyncClient(storyteller_client, Mock())
    book = Mock()
    book.storyteller_uuid = "book-uuid"

    assert sync_client.get_service_state(book, prev_state=None, title_snip="Broken Storyteller") is None
