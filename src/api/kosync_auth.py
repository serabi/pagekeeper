"""
KoSync authentication decorators.

Shared by both kosync_sync_bp (protocol routes) and kosync_admin_bp
(dashboard admin routes).
"""

import hmac
import ipaddress
import logging
import os
from functools import wraps

from flask import current_app, jsonify, request

from src.utils.kosync_headers import hash_kosync_key

logger = logging.getLogger(__name__)

_PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fd00::/8"),
)


def _is_private_ip(addr: str) -> bool:
    """Check if an address is on a private/local network."""
    try:
        ip = ipaddress.ip_address(addr)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        return any(ip in net for net in _PRIVATE_NETWORKS)
    except (ValueError, TypeError):
        return False


def kosync_auth_required(f):
    """Decorator for KOSync authentication with rate limiting."""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        remote = request.remote_addr
        is_loopback = remote in ("127.0.0.1", "::1")

        rate_limiter = current_app.config.get("rate_limiter")
        if not is_loopback and rate_limiter:
            from src.utils.rate_limiter import TokenBucketRateLimiter

            if not rate_limiter.check(remote, TokenBucketRateLimiter.AUTH_TOKEN_COST):
                return jsonify({"error": "Too many requests"}), 429

        user = request.headers.get("x-auth-user")
        key = request.headers.get("x-auth-key")

        expected_user = os.environ.get("KOSYNC_USER")
        expected_password = os.environ.get("KOSYNC_KEY")

        if not expected_user or not expected_password:
            logger.error(
                f"KOSync Integrated Server: Credentials not configured in settings (request from {request.remote_addr})"
            )
            return jsonify({"error": "Server not configured"}), 500

        expected_hash = hash_kosync_key(expected_password)

        if (
            user
            and key
            and expected_user
            and user.lower() == expected_user.lower()
            and hmac.compare_digest(key, expected_hash)
        ):
            return f(*args, **kwargs)

        logger.warning(
            f"KOSync Integrated Server: Unauthorized access attempt from '{request.remote_addr}' (user: '{user}')"
        )
        return jsonify({"error": "Unauthorized"}), 401

    return decorated_function


def admin_or_local_required(f):
    """Allow private IPs through; require KOSync credentials from public IPs.

    Safety: this decorator is only used on kosync_admin_bp routes, which are
    registered exclusively on the LAN dashboard (port 4477). The internet-facing
    sync port only serves kosync_sync_bp, so the proxy bypass here is never
    reachable from outside the local network.
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if _is_private_ip(request.remote_addr):
            return f(*args, **kwargs)

        # Public IP — require KOSync credentials
        user = request.headers.get("x-auth-user")
        key = request.headers.get("x-auth-key")
        expected_user = os.environ.get("KOSYNC_USER")
        expected_password = os.environ.get("KOSYNC_KEY")

        if not expected_user or not expected_password:
            return jsonify({"error": "Unauthorized"}), 401

        expected_hash = hash_kosync_key(expected_password)
        if (
            user
            and expected_user
            and user.lower() == expected_user.lower()
            and key
            and hmac.compare_digest(key, expected_hash)
        ):
            return f(*args, **kwargs)

        logger.warning(f"KOSync Admin: Unauthorized access attempt from public IP '{request.remote_addr}'")
        return jsonify({"error": "Unauthorized"}), 401

    return decorated_function
