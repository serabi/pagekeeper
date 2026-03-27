# KoSync Protocol Server — KOReader sync endpoints
# Implements KOSync protocol compatible with kosync-dotnet
import hmac
import logging
import os

from flask import Blueprint, current_app, jsonify, make_response, request

from src.api.kosync_auth import kosync_auth_required
from src.utils.kosync_headers import hash_kosync_key

logger = logging.getLogger(__name__)

kosync_sync_bp = Blueprint("kosync", __name__)


# ---------------- CORS: block browser preflight ----------------


@kosync_sync_bp.before_request
def _kosync_cors_preflight():
    """Return bare 204 for OPTIONS requests. KOReader is native — it never
    sends Origin/OPTIONS.  This blocks browser-based cross-origin abuse."""
    if request.method == "OPTIONS":
        return "", 204


# ---------------- KOSync Protocol Endpoints ----------------


@kosync_sync_bp.route("/healthcheck")
@kosync_sync_bp.route("/koreader/healthcheck")
def kosync_healthcheck():
    """KOSync connectivity check"""
    return "OK", 200


@kosync_sync_bp.route("/users/auth", methods=["GET"])
@kosync_sync_bp.route("/koreader/users/auth", methods=["GET"])
def kosync_users_auth():
    """KOReader auth check - validates credentials per kosync-dotnet spec"""
    remote = request.remote_addr
    rate_limiter = current_app.config.get("rate_limiter")
    if remote not in ("127.0.0.1", "::1") and rate_limiter:
        from src.utils.rate_limiter import TokenBucketRateLimiter

        if not rate_limiter.check(remote, TokenBucketRateLimiter.AUTH_TOKEN_COST):
            return jsonify({"message": "Too many requests"}), 429

    user = request.headers.get("x-auth-user")
    key = request.headers.get("x-auth-key")

    expected_user = os.environ.get("KOSYNC_USER")
    expected_password = os.environ.get("KOSYNC_KEY")

    if not user or not key:
        logger.warning(f"KOSync Auth: Missing credentials from '{request.remote_addr}'")
        return jsonify({"message": "Invalid credentials"}), 401

    if not expected_user or not expected_password:
        logger.error("KOSync Auth: Server credentials not configured")
        return jsonify({"message": "Server not configured"}), 500

    expected_hash = hash_kosync_key(expected_password)

    if user.lower() == expected_user.lower() and hmac.compare_digest(key, expected_hash):
        logger.debug(f"KOSync Auth: User '{user}' authenticated successfully")
        return jsonify({"username": user}), 200

    logger.warning(f"KOSync Auth: Failed auth attempt for user '{user}' from '{request.remote_addr}'")
    return jsonify({"message": "Unauthorized"}), 401


@kosync_sync_bp.route("/users/create", methods=["POST"])
@kosync_sync_bp.route("/koreader/users/create", methods=["POST"])
def kosync_users_create():
    """Stub for KOReader user registration check"""
    remote = request.remote_addr
    rate_limiter = current_app.config.get("rate_limiter")
    if remote not in ("127.0.0.1", "::1") and rate_limiter:
        from src.utils.rate_limiter import TokenBucketRateLimiter

        if not rate_limiter.check(remote, TokenBucketRateLimiter.AUTH_TOKEN_COST):
            return jsonify({"error": "Too many requests"}), 429

    return jsonify({"id": 1, "username": os.environ.get("KOSYNC_USER", "user")}), 201


@kosync_sync_bp.route("/users/login", methods=["POST"])
@kosync_sync_bp.route("/koreader/users/login", methods=["POST"])
def kosync_users_login():
    """Stub for KOReader login check"""
    remote = request.remote_addr
    rate_limiter = current_app.config.get("rate_limiter")
    if remote not in ("127.0.0.1", "::1") and rate_limiter:
        from src.utils.rate_limiter import TokenBucketRateLimiter

        if not rate_limiter.check(remote, TokenBucketRateLimiter.AUTH_TOKEN_COST):
            return jsonify({"error": "Too many requests"}), 429

    return jsonify({"id": 1, "username": os.environ.get("KOSYNC_USER", "user"), "active": True}), 200


@kosync_sync_bp.route("/syncs/progress/<doc_id>", methods=["GET"])
@kosync_sync_bp.route("/koreader/syncs/progress/<doc_id>", methods=["GET"])
@kosync_auth_required
def kosync_get_progress(doc_id):
    """Fetch progress for a specific document.
    Returns 502 (not 404) if document not found, per kosync-dotnet spec."""
    if not doc_id or len(doc_id) > 64 or not all(c.isalnum() or c in "-_" for c in doc_id):
        return jsonify({"message": "Invalid document ID"}), 400
    svc = current_app.config["kosync_service"]
    result, status = svc.handle_get_progress(doc_id, request.remote_addr)
    resp = make_response(jsonify(result), status)
    resp.content_type = "application/json"
    return resp


@kosync_sync_bp.route("/syncs/progress", methods=["PUT"])
@kosync_sync_bp.route("/koreader/syncs/progress", methods=["PUT"])
@kosync_auth_required
def kosync_put_progress():
    """Receive progress update from KOReader."""
    data = request.json
    if not data or not isinstance(data, dict):
        return jsonify({"error": "Invalid request body"}), 400
    svc = current_app.config["kosync_service"]
    debounce = current_app.config.get("debounce_manager")
    result, status = svc.handle_put_progress(data, request.remote_addr, debounce)
    resp = make_response(jsonify(result), status)
    resp.content_type = "application/json"
    return resp
