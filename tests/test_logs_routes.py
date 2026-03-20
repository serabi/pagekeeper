"""Route tests for the Logs blueprint (/logs, /api/logs, /api/logs/live, /api/logs/hardcover)."""

import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# GET /logs — renders the page
# ---------------------------------------------------------------------------

def test_logs_view_renders(client):
    resp = client.get('/logs')
    assert resp.status_code == 200
    assert b'<!DOCTYPE html>' in resp.data or b'<!doctype html>' in resp.data.lower()


# ---------------------------------------------------------------------------
# GET /view_log — legacy redirect
# ---------------------------------------------------------------------------

def test_view_log_redirects_to_logs(client):
    resp = client.get('/view_log')
    assert resp.status_code == 302
    assert '/logs' in resp.headers['Location']


# ---------------------------------------------------------------------------
# GET /api/logs — file-based log reading
# ---------------------------------------------------------------------------

class TestApiLogs:
    """Tests for the /api/logs endpoint that reads from the log file."""

    def test_no_log_file(self, client):
        """When LOG_PATH does not exist, returns an empty log list."""
        with patch('src.blueprints.logs.LOG_PATH', Path('/tmp/nonexistent_log_file.log')):
            resp = client.get('/api/logs')
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['logs'] == []
        assert data['total_lines'] == 0

    def test_well_formed_lines(self, client, tmp_path):
        """Properly formatted log lines are parsed into structured entries."""
        log_file = tmp_path / 'test.log'
        log_file.write_text(
            '[2026-03-19 10:00:00] INFO - src.app: Application started\n'
            '[2026-03-19 10:00:01] WARNING - src.sync: Sync delayed\n'
            '[2026-03-19 10:00:02] ERROR - src.db: Connection lost\n'
        )
        with patch('src.blueprints.logs.LOG_PATH', log_file):
            resp = client.get('/api/logs')
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['total_lines'] == 3
        assert len(data['logs']) == 3

        first = data['logs'][0]
        assert first['level'] == 'INFO'
        assert first['module'] == 'src.app'
        assert first['message'] == 'Application started'
        assert first['timestamp'] == '2026-03-19 10:00:00'

    def test_malformed_lines_treated_as_info(self, client, tmp_path):
        """Lines that don't match the expected format are still returned as INFO."""
        log_file = tmp_path / 'test.log'
        log_file.write_text('just some random text\n')
        with patch('src.blueprints.logs.LOG_PATH', log_file):
            resp = client.get('/api/logs')
        data = resp.get_json()
        assert resp.status_code == 200
        assert len(data['logs']) == 1
        assert data['logs'][0]['level'] == 'INFO'
        assert data['logs'][0]['message'] == 'just some random text'

    def test_level_filter(self, client, tmp_path):
        """The ?level= parameter filters out lower-severity entries."""
        log_file = tmp_path / 'test.log'
        log_file.write_text(
            '[2026-03-19 10:00:00] DEBUG - mod: debug msg\n'
            '[2026-03-19 10:00:01] INFO - mod: info msg\n'
            '[2026-03-19 10:00:02] ERROR - mod: error msg\n'
        )
        with patch('src.blueprints.logs.LOG_PATH', log_file):
            resp = client.get('/api/logs?level=ERROR')
        data = resp.get_json()
        assert len(data['logs']) == 1
        assert data['logs'][0]['level'] == 'ERROR'

    def test_search_filter(self, client, tmp_path):
        """The ?search= parameter filters entries by substring match."""
        log_file = tmp_path / 'test.log'
        log_file.write_text(
            '[2026-03-19 10:00:00] INFO - mod: Alpha message\n'
            '[2026-03-19 10:00:01] INFO - mod: Beta message\n'
            '[2026-03-19 10:00:02] INFO - mod: Alpha again\n'
        )
        with patch('src.blueprints.logs.LOG_PATH', log_file):
            resp = client.get('/api/logs?search=alpha')
        data = resp.get_json()
        assert len(data['logs']) == 2
        assert all('Alpha' in log['message'] or 'alpha' in log['message'] for log in data['logs'])

    def test_lines_limit(self, client, tmp_path):
        """The ?lines= parameter caps the number of returned entries."""
        log_file = tmp_path / 'test.log'
        lines = ''.join(
            f'[2026-03-19 10:00:{i:02d}] INFO - mod: Line {i}\n' for i in range(20)
        )
        log_file.write_text(lines)
        with patch('src.blueprints.logs.LOG_PATH', log_file):
            resp = client.get('/api/logs?lines=5')
        data = resp.get_json()
        assert data['displayed_lines'] == 5
        assert data['total_lines'] == 20

    def test_offset_parameter(self, client, tmp_path):
        """The ?offset= parameter skips the most recent N entries."""
        log_file = tmp_path / 'test.log'
        lines = ''.join(
            f'[2026-03-19 10:00:{i:02d}] INFO - mod: Line {i}\n' for i in range(10)
        )
        log_file.write_text(lines)
        with patch('src.blueprints.logs.LOG_PATH', log_file):
            resp_all = client.get('/api/logs')
            resp_offset = client.get('/api/logs?offset=3')
        all_data = resp_all.get_json()
        offset_data = resp_offset.get_json()
        assert offset_data['displayed_lines'] == all_data['displayed_lines'] - 3

    def test_empty_lines_skipped(self, client, tmp_path):
        """Blank lines in the log file are ignored."""
        log_file = tmp_path / 'test.log'
        log_file.write_text(
            '\n\n[2026-03-19 10:00:00] INFO - mod: One line\n\n\n'
        )
        with patch('src.blueprints.logs.LOG_PATH', log_file):
            resp = client.get('/api/logs')
        data = resp.get_json()
        assert data['total_lines'] == 1

    def test_line_without_colon_separator(self, client, tmp_path):
        """A timestamped line with no ': ' is treated as INFO with unknown module."""
        log_file = tmp_path / 'test.log'
        log_file.write_text('[2026-03-19 10:00:00] some text without colon separator\n')
        with patch('src.blueprints.logs.LOG_PATH', log_file):
            resp = client.get('/api/logs')
        data = resp.get_json()
        assert len(data['logs']) == 1
        assert data['logs'][0]['level'] == 'INFO'
        assert data['logs'][0]['module'] == 'unknown'

    def test_log_path_none(self, client):
        """When LOG_PATH is None, returns empty results."""
        with patch('src.blueprints.logs.LOG_PATH', None):
            resp = client.get('/api/logs')
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['logs'] == []


# ---------------------------------------------------------------------------
# GET /api/logs/live — memory-based live logs
# ---------------------------------------------------------------------------

class TestApiLogsLive:
    """Tests for the /api/logs/live endpoint backed by MemoryLogHandler."""

    def _make_log_entries(self, count=5, level='INFO'):
        return [
            {
                'timestamp': f'2026-03-19 10:00:{i:02d}',
                'level': level,
                'message': f'Live message {i}',
                'module': 'src.test',
            }
            for i in range(count)
        ]

    def test_happy_path(self, client):
        """Returns recent memory logs as JSON."""
        entries = self._make_log_entries(3)
        with patch('src.blueprints.logs.memory_log_handler') as mock_handler:
            mock_handler.get_recent_logs.return_value = entries
            resp = client.get('/api/logs/live')
        data = resp.get_json()
        assert resp.status_code == 200
        assert len(data['logs']) == 3
        assert 'timestamp' in data

    def test_level_filter(self, client):
        """The ?level= parameter filters memory logs by severity."""
        entries = [
            {'timestamp': 't1', 'level': 'DEBUG', 'message': 'dbg', 'module': 'm'},
            {'timestamp': 't2', 'level': 'ERROR', 'message': 'err', 'module': 'm'},
        ]
        with patch('src.blueprints.logs.memory_log_handler') as mock_handler:
            mock_handler.get_recent_logs.return_value = entries
            resp = client.get('/api/logs/live?level=ERROR')
        data = resp.get_json()
        assert len(data['logs']) == 1
        assert data['logs'][0]['level'] == 'ERROR'

    def test_search_filter(self, client):
        """The ?search= parameter filters memory logs by substring."""
        entries = [
            {'timestamp': 't1', 'level': 'INFO', 'message': 'apple pie', 'module': 'm'},
            {'timestamp': 't2', 'level': 'INFO', 'message': 'banana split', 'module': 'm'},
        ]
        with patch('src.blueprints.logs.memory_log_handler') as mock_handler:
            mock_handler.get_recent_logs.return_value = entries
            resp = client.get('/api/logs/live?search=apple')
        data = resp.get_json()
        assert len(data['logs']) == 1
        assert 'apple' in data['logs'][0]['message']

    def test_count_parameter(self, client):
        """The ?count= parameter limits the number of returned entries."""
        entries = self._make_log_entries(10)
        with patch('src.blueprints.logs.memory_log_handler') as mock_handler:
            mock_handler.get_recent_logs.return_value = entries
            resp = client.get('/api/logs/live?count=3')
        data = resp.get_json()
        assert len(data['logs']) == 3

    def test_error_returns_500(self, client):
        """When an exception is raised, the endpoint returns 500."""
        with patch('src.blueprints.logs.memory_log_handler') as mock_handler:
            mock_handler.get_recent_logs.side_effect = RuntimeError('boom')
            resp = client.get('/api/logs/live')
        assert resp.status_code == 500
        data = resp.get_json()
        assert 'error' in data
        assert data['logs'] == []


# ---------------------------------------------------------------------------
# GET /api/logs/hardcover — database-backed Hardcover sync logs
# ---------------------------------------------------------------------------

class TestApiLogsHardcover:
    """Tests for the /api/logs/hardcover endpoint."""

    @staticmethod
    def _make_entry(id=1, abs_id='abc-123', book_title='Test Book',
                    direction='pull', action='update_progress',
                    detail=None, success=True, error_message=None):
        entry = SimpleNamespace(
            id=id,
            abs_id=abs_id,
            book_title=book_title,
            direction=direction,
            action=action,
            detail=detail,
            success=success,
            error_message=error_message,
            created_at=datetime(2026, 3, 19, 12, 0, 0),
        )
        return entry

    def test_basic_pagination(self, client, mock_container):
        """Returns paginated results with correct metadata."""
        entries = [self._make_entry(id=i) for i in range(3)]
        mock_container.mock_database_service.get_hardcover_sync_logs.return_value = (entries, 3)

        resp = client.get('/api/logs/hardcover')
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['total'] == 3
        assert len(data['logs']) == 3
        assert data['page'] == 1
        assert data['total_pages'] == 1

    def test_page_and_per_page(self, client, mock_container):
        """The ?page= and ?per_page= parameters are forwarded to the database."""
        mock_container.mock_database_service.get_hardcover_sync_logs.return_value = ([], 100)

        resp = client.get('/api/logs/hardcover?page=3&per_page=10')
        data = resp.get_json()
        assert resp.status_code == 200
        assert data['page'] == 3
        assert data['per_page'] == 10
        assert data['total_pages'] == 10

        call_kwargs = mock_container.mock_database_service.get_hardcover_sync_logs.call_args
        assert call_kwargs.kwargs['page'] == 3
        assert call_kwargs.kwargs['per_page'] == 10

    def test_direction_and_action_filters(self, client, mock_container):
        """The ?direction= and ?action= query params are forwarded."""
        mock_container.mock_database_service.get_hardcover_sync_logs.return_value = ([], 0)

        resp = client.get('/api/logs/hardcover?direction=push&action=update_progress')
        assert resp.status_code == 200

        call_kwargs = mock_container.mock_database_service.get_hardcover_sync_logs.call_args
        assert call_kwargs.kwargs['direction'] == 'push'
        assert call_kwargs.kwargs['action'] == 'update_progress'

    def test_search_filter(self, client, mock_container):
        """The ?search= query param is forwarded."""
        mock_container.mock_database_service.get_hardcover_sync_logs.return_value = ([], 0)

        resp = client.get('/api/logs/hardcover?search=Dune')
        assert resp.status_code == 200

        call_kwargs = mock_container.mock_database_service.get_hardcover_sync_logs.call_args
        assert call_kwargs.kwargs['search'] == 'Dune'

    def test_json_detail_parsed(self, client, mock_container):
        """When entry.detail is valid JSON, it is returned as a parsed object."""
        entry = self._make_entry(detail='{"progress": 0.5}')
        mock_container.mock_database_service.get_hardcover_sync_logs.return_value = ([entry], 1)

        resp = client.get('/api/logs/hardcover')
        data = resp.get_json()
        assert data['logs'][0]['detail'] == {'progress': 0.5}

    def test_non_json_detail_returned_as_is(self, client, mock_container):
        """When entry.detail is not valid JSON, it is returned as a plain string."""
        entry = self._make_entry(detail='plain text detail')
        mock_container.mock_database_service.get_hardcover_sync_logs.return_value = ([entry], 1)

        resp = client.get('/api/logs/hardcover')
        data = resp.get_json()
        assert data['logs'][0]['detail'] == 'plain text detail'

    def test_null_detail(self, client, mock_container):
        """When entry.detail is None, detail is returned as null."""
        entry = self._make_entry(detail=None)
        mock_container.mock_database_service.get_hardcover_sync_logs.return_value = ([entry], 1)

        resp = client.get('/api/logs/hardcover')
        data = resp.get_json()
        assert data['logs'][0]['detail'] is None

    def test_entry_fields_serialized(self, client, mock_container):
        """All expected fields are present in the serialized log entry."""
        entry = self._make_entry(
            id=42, abs_id='xyz', book_title='Dune', direction='push',
            action='set_status', success=False, error_message='timeout',
        )
        mock_container.mock_database_service.get_hardcover_sync_logs.return_value = ([entry], 1)

        resp = client.get('/api/logs/hardcover')
        log = resp.get_json()['logs'][0]
        assert log['id'] == 42
        assert log['abs_id'] == 'xyz'
        assert log['book_title'] == 'Dune'
        assert log['direction'] == 'push'
        assert log['action'] == 'set_status'
        assert log['success'] is False
        assert log['error_message'] == 'timeout'
        assert log['created_at'] == '2026-03-19T12:00:00'

    def test_null_created_at(self, client, mock_container):
        """When created_at is None, it is serialized as null."""
        entry = self._make_entry()
        entry.created_at = None
        mock_container.mock_database_service.get_hardcover_sync_logs.return_value = ([entry], 1)

        resp = client.get('/api/logs/hardcover')
        assert resp.get_json()['logs'][0]['created_at'] is None

    def test_error_returns_500(self, client, mock_container):
        """When an exception is raised, the endpoint returns 500."""
        mock_container.mock_database_service.get_hardcover_sync_logs.side_effect = RuntimeError('db down')

        resp = client.get('/api/logs/hardcover')
        assert resp.status_code == 500
        data = resp.get_json()
        assert 'error' in data
        assert data['logs'] == []
        assert data['total'] == 0

    def test_empty_filters_become_none(self, client, mock_container):
        """Empty string filter params are converted to None."""
        mock_container.mock_database_service.get_hardcover_sync_logs.return_value = ([], 0)

        resp = client.get('/api/logs/hardcover?direction=&action=&search=')
        assert resp.status_code == 200

        call_kwargs = mock_container.mock_database_service.get_hardcover_sync_logs.call_args
        assert call_kwargs.kwargs['direction'] is None
        assert call_kwargs.kwargs['action'] is None
        assert call_kwargs.kwargs['search'] is None

    def test_per_page_clamped_to_max(self, client, mock_container):
        """per_page cannot exceed 200."""
        mock_container.mock_database_service.get_hardcover_sync_logs.return_value = ([], 0)

        resp = client.get('/api/logs/hardcover?per_page=999')
        assert resp.status_code == 200

        call_kwargs = mock_container.mock_database_service.get_hardcover_sync_logs.call_args
        assert call_kwargs.kwargs['per_page'] == 200
