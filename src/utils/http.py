# pyright: reportMissingImports=false
from flask import jsonify


def json_error(error_message, status=400, **payload):
    body = dict(payload)
    body["success"] = False
    body["error"] = error_message
    return jsonify(body), status


def json_detail_error(detail, status=400, **payload):
    body = dict(payload)
    body["success"] = False
    body["detail"] = detail
    return jsonify(body), status


def json_success(status=200, **payload):
    body = dict(payload)
    body["success"] = True
    return jsonify(body), status
