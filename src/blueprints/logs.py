"""Logs blueprint — /logs, /api/logs, /api/logs/live, /api/logs/hardcover, /view_log."""

import json
import logging
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, redirect, render_template, request, url_for

from src.blueprints.helpers import get_database_service
from src.utils.logging_utils import LOG_PATH, memory_log_handler

logger = logging.getLogger(__name__)

logs_bp = Blueprint('logs_page', __name__)


@logs_bp.route('/logs')
def logs_view():
    """Display logs frontend with filtering capabilities."""
    return render_template('logs.html')


@logs_bp.route('/api/logs')
def api_logs():
    """API endpoint for fetching logs with filtering and pagination."""
    try:
        lines_count = request.args.get('lines', 1000, type=int)
        min_level = request.args.get('level', 'DEBUG')
        search_term = request.args.get('search', '').lower()
        offset = request.args.get('offset', 0, type=int)

        lines_count = min(lines_count, 5000)

        all_lines = []

        if LOG_PATH and LOG_PATH.exists():
            with open(LOG_PATH, encoding='utf-8') as f:
                all_lines.extend(f.readlines())

        if LOG_PATH and lines_count > len(all_lines):
            for i in range(1, 6):
                backup_path = Path(str(LOG_PATH) + f'.{i}')
                if backup_path.exists():
                    with open(backup_path, encoding='utf-8') as f:
                        backup_lines = f.readlines()
                        all_lines = backup_lines + all_lines
                        if len(all_lines) >= lines_count:
                            break

        log_levels = {
            'DEBUG': 10, 'INFO': 20, 'WARNING': 30, 'ERROR': 40, 'CRITICAL': 50
        }
        min_level_num = log_levels.get(min_level.upper(), 10)

        parsed_logs = []
        for line in all_lines:
            line = line.strip()
            if not line:
                continue

            try:
                if line.startswith('[') and '] ' in line:
                    timestamp_end = line.find('] ')
                    timestamp_str = line[1:timestamp_end]
                    rest = line[timestamp_end + 2:]

                    if ': ' in rest:
                        level_module_str, message = rest.split(': ', 1)

                        if ' - ' in level_module_str:
                            level_str, module_str = level_module_str.split(' - ', 1)
                        else:
                            level_str = level_module_str
                            module_str = 'unknown'

                        level_num = log_levels.get(level_str.upper(), 20)

                        if level_num >= min_level_num:
                            if not search_term or search_term in message.lower() or search_term in level_str.lower() or search_term in module_str.lower():
                                parsed_logs.append({
                                    'timestamp': timestamp_str,
                                    'level': level_str,
                                    'message': message,
                                    'module': module_str,
                                    'raw': line
                                })
                    else:
                        if min_level_num <= 20:
                            if not search_term or search_term in rest.lower():
                                parsed_logs.append({
                                    'timestamp': timestamp_str,
                                    'level': 'INFO',
                                    'message': rest,
                                    'module': 'unknown',
                                    'raw': line
                                })
                else:
                    if min_level_num <= 20:
                        if not search_term or search_term in line.lower():
                            parsed_logs.append({
                                'timestamp': '',
                                'level': 'INFO',
                                'message': line,
                                'module': 'unknown',
                                'raw': line
                            })
            except Exception as e:
                logger.debug(f"Failed to parse log line: {e}")
                if not search_term or search_term in line.lower():
                    parsed_logs.append({
                        'timestamp': '',
                        'level': 'INFO',
                        'message': line,
                        'module': 'unknown',
                        'raw': line
                    })

        recent_logs = parsed_logs[-lines_count:] if len(parsed_logs) > lines_count else parsed_logs

        if offset > 0:
            recent_logs = recent_logs[:-offset] if offset < len(recent_logs) else []

        return jsonify({
            'logs': recent_logs,
            'total_lines': len(parsed_logs),
            'displayed_lines': len(recent_logs),
            'has_more': len(parsed_logs) > lines_count + offset
        })

    except Exception as e:
        logger.error(f"Error fetching logs: {e}")
        return jsonify({'error': 'Failed to fetch logs', 'logs': [], 'total_lines': 0, 'displayed_lines': 0}), 500


@logs_bp.route('/api/logs/live')
def api_logs_live():
    """API endpoint for fetching recent live logs from memory."""
    try:
        count = request.args.get('count', 50, type=int)
        min_level = request.args.get('level', 'DEBUG')
        search_term = request.args.get('search', '').lower()

        count = min(count, 500)

        log_levels = {
            'DEBUG': 10, 'INFO': 20, 'WARNING': 30, 'ERROR': 40, 'CRITICAL': 50
        }
        min_level_num = log_levels.get(min_level.upper(), 10)

        recent_logs = memory_log_handler.get_recent_logs(count * 2)

        filtered_logs = []
        for log_entry in recent_logs:
            level_num = log_levels.get(log_entry['level'], 20)

            if level_num >= min_level_num:
                if not search_term or search_term in log_entry['message'].lower() or search_term in log_entry['level'].lower():
                    filtered_logs.append(log_entry)

        result_logs = filtered_logs[-count:] if len(filtered_logs) > count else filtered_logs

        return jsonify({
            'logs': result_logs,
            'timestamp': datetime.now().isoformat()
        })

    except Exception as e:
        logger.error(f"Error fetching live logs: {e}")
        return jsonify({'error': 'Failed to fetch live logs', 'logs': [], 'timestamp': datetime.now().isoformat()}), 500


@logs_bp.route('/api/logs/hardcover')
def api_logs_hardcover():
    """API endpoint for fetching Hardcover sync logs with filtering and pagination."""
    try:
        page = max(1, request.args.get('page', 1, type=int))
        per_page = max(1, min(request.args.get('per_page', 50, type=int), 200))
        direction = request.args.get('direction') or None
        action = request.args.get('action') or None
        search = request.args.get('search') or None

        database_service = get_database_service()
        items, total = database_service.get_hardcover_sync_logs(
            page=page, per_page=per_page,
            direction=direction, action=action, search=search,
        )

        logs = []
        for entry in items:
            detail_parsed = None
            if entry.detail:
                try:
                    detail_parsed = json.loads(entry.detail)
                except (json.JSONDecodeError, TypeError):
                    detail_parsed = entry.detail

            logs.append({
                'id': entry.id,
                'abs_id': entry.abs_id,
                'book_title': entry.book_title,
                'direction': entry.direction,
                'action': entry.action,
                'detail': detail_parsed,
                'success': entry.success,
                'error_message': entry.error_message,
                'created_at': entry.created_at.isoformat() if entry.created_at else None,
            })

        total_pages = (total + per_page - 1) // per_page if total > 0 else 1
        return jsonify({
            'logs': logs,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': total_pages,
        })

    except Exception as e:
        logger.error(f"Error fetching Hardcover sync logs: {e}")
        return jsonify({'error': 'Failed to fetch Hardcover sync logs', 'logs': [], 'total': 0}), 500


@logs_bp.route('/view_log')
def view_log():
    """Legacy endpoint - redirect to new logs page."""
    return redirect(url_for('logs_page.logs_view'))
