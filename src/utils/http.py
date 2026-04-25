# pyright: reportMissingImports=false
from flask import jsonify


def json_error(error_message, status=400, **payload):
    body = {"success": False, "error": error_message}
    body.update(payload)
    return jsonify(body), status


def json_detail_error(detail, status=400, **payload):
    body = {"success": False, "detail": detail}
    body.update(payload)
    return jsonify(body), status


def json_success(status=200, **payload):
    body = {"success": True}
    body.update(payload)
    return jsonify(body), status
