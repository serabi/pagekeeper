# KoSync Server - Extracted from web_server.py for clean code separation
# Implements KOSync protocol compatible with kosync-dotnet
import hmac
import ipaddress
import logging
import os
import threading
import time
from datetime import datetime
from functools import wraps

from flask import Blueprint, jsonify, render_template, request

from src.utils.constants import INTERNAL_DEVICE_NAMES
from src.utils.kosync_headers import hash_kosync_key
from src.utils.path_utils import is_safe_path_within

logger = logging.getLogger(__name__)

# Create Blueprints for KoSync endpoints
# kosync_sync_bp: KOReader protocol routes (safe to expose to internet)
# kosync_admin_bp: Dashboard management routes (LAN only)
kosync_sync_bp = Blueprint('kosync', __name__)
kosync_admin_bp = Blueprint('kosync_admin', __name__)

# Module-level references - set via init_kosync_server()
_database_service = None
_container = None
_manager = None
_kosync_service = None

# KoSync PUT debounce state
_kosync_debounce: dict = {}  # {book_id: {'last_event': float, 'title': str, 'synced': bool}}
_kosync_debounce_lock = threading.Lock()
_debounce_thread_started = False

# Rate limiting (token bucket per IP)
_RATE_LIMIT_CAPACITY = 30     # max burst
_RATE_LIMIT_REFILL = 2.0      # tokens per second
_AUTH_TOKEN_COST = 5           # auth attempts are expensive
_rate_limit_store: dict = {}   # {ip: {'tokens': float, 'last': float}}
_rate_limit_lock = threading.Lock()

# Stale entry cleanup threshold (seconds)
_STALE_ENTRY_SECONDS = 300
# Debounce loop poll interval (seconds)
_DEBOUNCE_POLL_INTERVAL = 10


def _rate_limit_check(ip: str, cost: int = 1) -> bool:
    """Consume tokens from the bucket for `ip`. Returns True if allowed."""
    now = time.time()
    with _rate_limit_lock:
        bucket = _rate_limit_store.get(ip)
        if bucket is None:
            bucket = {'tokens': _RATE_LIMIT_CAPACITY, 'last': now}
            _rate_limit_store[ip] = bucket

        elapsed = now - bucket['last']
        bucket['tokens'] = min(_RATE_LIMIT_CAPACITY, bucket['tokens'] + elapsed * _RATE_LIMIT_REFILL)
        bucket['last'] = now

        if bucket['tokens'] >= cost:
            bucket['tokens'] -= cost
            return True
        return False


def _prune_rate_limit_store():
    """Remove entries idle for more than 5 minutes."""
    now = time.time()
    with _rate_limit_lock:
        stale = [ip for ip, b in _rate_limit_store.items() if now - b['last'] > _STALE_ENTRY_SECONDS]
        for ip in stale:
            del _rate_limit_store[ip]


def init_kosync_server(database_service, container, manager, ebook_dir=None):
    """Initialize KoSync server with required dependencies."""
    global _database_service, _container, _manager, _kosync_service

    from src.services.kosync_service import KosyncService

    _database_service = database_service
    _container = container
    _manager = manager
    _kosync_service = KosyncService(database_service, container, manager, ebook_dir)


def _record_kosync_event(book_id: int, title: str) -> None:
    """Record a KoSync PUT event for debounced sync triggering."""
    global _debounce_thread_started
    with _kosync_debounce_lock:
        _kosync_debounce[book_id] = {
            'last_event': time.time(),
            'title': title,
            'synced': False,
        }
    if not _debounce_thread_started:
        _debounce_thread_started = True
        threading.Thread(target=_kosync_debounce_loop, daemon=True).start()


def _kosync_debounce_loop() -> None:
    """Check periodically for books that stopped receiving KoSync PUTs."""
    debounce_seconds = int(os.environ.get('ABS_SOCKET_DEBOUNCE_SECONDS', '30'))
    while True:
        time.sleep(_DEBOUNCE_POLL_INTERVAL)
        now = time.time()
        to_sync = []

        with _kosync_debounce_lock:
            for book_id, info in _kosync_debounce.items():
                if not info['synced'] and (now - info['last_event']) > debounce_seconds:
                    info['synced'] = True
                    to_sync.append((book_id, info['title']))

        for book_id, title in to_sync:
            if _manager:
                book = _database_service.get_book_by_id(book_id) if _database_service else None
                if not book:
                    logger.warning(f"KOSync PUT: No book found for id={book_id} — skipping sync")
                    continue
                logger.info(f"KOSync PUT: Triggering sync for '{title}' (debounced)")
                threading.Thread(
                    target=_manager.sync_cycle,
                    kwargs={'target_book_id': book.id},
                    daemon=True,
                ).start()

        # Clean up stale debounce entries
        with _kosync_debounce_lock:
            stale = [k for k, v in _kosync_debounce.items() if now - v['last_event'] > _STALE_ENTRY_SECONDS]
            for k in stale:
                del _kosync_debounce[k]

        # Prune stale rate-limit buckets
        _prune_rate_limit_store()


def kosync_auth_required(f):
    """Decorator for KOSync authentication."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        remote = request.remote_addr
        is_loopback = remote in ('127.0.0.1', '::1')
        if not is_loopback and not _rate_limit_check(remote, _AUTH_TOKEN_COST):
            return jsonify({"error": "Too many requests"}), 429

        user = request.headers.get('x-auth-user')
        key = request.headers.get('x-auth-key')

        expected_user = os.environ.get("KOSYNC_USER")
        expected_password = os.environ.get("KOSYNC_KEY")

        if not expected_user or not expected_password:
            logger.error(f"KOSync Integrated Server: Credentials not configured in settings (request from {request.remote_addr})")
            return jsonify({"error": "Server not configured"}), 500

        expected_hash = hash_kosync_key(expected_password)

        if (user and key and expected_user
                and user.lower() == expected_user.lower()
                and hmac.compare_digest(key, expected_hash)):
            return f(*args, **kwargs)

        logger.warning(f"KOSync Integrated Server: Unauthorized access attempt from '{request.remote_addr}' (user: '{user}')")
        return jsonify({"error": "Unauthorized"}), 401
    return decorated_function


# ---------------- CORS: block browser preflight ----------------

@kosync_sync_bp.before_request
def _kosync_cors_preflight():
    """Return bare 204 for OPTIONS requests. KOReader is native — it never
    sends Origin/OPTIONS.  This blocks browser-based cross-origin abuse."""
    if request.method == 'OPTIONS':
        return '', 204


# ---------------- KOSync Protocol Endpoints ----------------

@kosync_sync_bp.route('/healthcheck')
@kosync_sync_bp.route('/koreader/healthcheck')
def kosync_healthcheck():
    """KOSync connectivity check"""
    return "OK", 200


@kosync_sync_bp.route('/users/auth', methods=['GET'])
@kosync_sync_bp.route('/koreader/users/auth', methods=['GET'])
def kosync_users_auth():
    """KOReader auth check - validates credentials per kosync-dotnet spec"""
    remote = request.remote_addr
    if remote not in ('127.0.0.1', '::1') and not _rate_limit_check(remote, _AUTH_TOKEN_COST):
        return jsonify({"message": "Too many requests"}), 429

    user = request.headers.get('x-auth-user')
    key = request.headers.get('x-auth-key')

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


@kosync_sync_bp.route('/users/create', methods=['POST'])
@kosync_sync_bp.route('/koreader/users/create', methods=['POST'])
def kosync_users_create():
    """Stub for KOReader user registration check"""
    remote = request.remote_addr
    if remote not in ('127.0.0.1', '::1') and not _rate_limit_check(remote, _AUTH_TOKEN_COST):
        return jsonify({"error": "Too many requests"}), 429

    return jsonify({
        "id": 1,
        "username": os.environ.get("KOSYNC_USER", "user")
    }), 201


@kosync_sync_bp.route('/users/login', methods=['POST'])
@kosync_sync_bp.route('/koreader/users/login', methods=['POST'])
def kosync_users_login():
    """Stub for KOReader login check"""
    remote = request.remote_addr
    if remote not in ('127.0.0.1', '::1') and not _rate_limit_check(remote, _AUTH_TOKEN_COST):
        return jsonify({"error": "Too many requests"}), 429

    return jsonify({
        "id": 1,
        "username": os.environ.get("KOSYNC_USER", "user"),
        "active": True
    }), 200


@kosync_sync_bp.route('/syncs/progress/<doc_id>', methods=['GET'])
@kosync_sync_bp.route('/koreader/syncs/progress/<doc_id>', methods=['GET'])
@kosync_auth_required
def kosync_get_progress(doc_id):
    """
    Fetch progress for a specific document.
    Returns 502 (not 404) if document not found, per kosync-dotnet spec.

    Lookup order:
      1. Direct hash match in kosync_documents
      2. Book lookup by kosync_doc_id
      3. Sibling hash resolution (same book, different epub hash)
      4. Background auto-discovery for completely unknown hashes
    """
    if len(doc_id) > 64:
        return jsonify({"error": "Document ID too long"}), 400

    logger.info(f"KOSync: GET progress for doc {doc_id[:8]}... from {request.remote_addr}")

    # Step 1: Direct hash lookup
    kosync_doc = _database_service.get_kosync_document(doc_id)
    if kosync_doc:
        # If linked to a book, always check siblings for freshest progress.
        # This prevents "shadow" docs (created by sync-bot PUTs) from returning
        # stale data when the real device hash has advanced further.
        if kosync_doc.linked_abs_id:
            book = _database_service.get_book_by_abs_id(kosync_doc.linked_abs_id)
            if book:
                return _respond_from_book_states(doc_id, book)

        has_progress = kosync_doc.percentage and float(kosync_doc.percentage) > 0
        if has_progress:
            return jsonify(_kosync_service.serialize_progress(kosync_doc, device_default="")), 200
        # Document exists but has no progress and no linked book — fall through
        # to try sibling resolution for better data

    # Step 2: Book lookup by kosync_doc_id
    book = _database_service.get_book_by_kosync_id(doc_id)
    if book:
        return _respond_from_book_states(doc_id, book)

    # Step 3: Sibling hash resolution — find the book via other linked hashes
    resolved_book = _kosync_service.resolve_book_by_sibling_hash(doc_id, existing_doc=kosync_doc)
    if resolved_book:
        _kosync_service.register_hash_for_book(doc_id, resolved_book)
        return _respond_from_book_states(doc_id, resolved_book)

    # Step 4: Unknown hash — register stub and start background discovery
    auto_create = os.environ.get('AUTO_CREATE_EBOOK_MAPPING', 'true').lower() == 'true'
    if auto_create and _kosync_service.start_discovery_if_available(doc_id):
        from src.db.models import KosyncDocument as KD
        stub = KD(document_hash=doc_id)
        _database_service.save_kosync_document(stub)
        logger.info(f"KOSync: Created stub for unknown hash {doc_id[:8]}..., starting background discovery")
        threading.Thread(target=_kosync_service.run_get_auto_discovery, args=(doc_id,), daemon=True).start()

    logger.warning(f"KOSync: Document not found: {doc_id[:8]}... (GET from {request.remote_addr})")
    return jsonify({"message": "Document not found on server"}), 502


@kosync_sync_bp.route('/syncs/progress', methods=['PUT'])
@kosync_sync_bp.route('/koreader/syncs/progress', methods=['PUT'])
@kosync_auth_required
def kosync_put_progress():
    """
    Receive progress update from KOReader.
    Stores ALL documents, whether mapped to ABS or not.
    """
    from src.db.models import KosyncDocument

    data = request.json
    if not data:
        logger.warning(f"KOSync: PUT progress with no JSON data from {request.remote_addr}")
        return jsonify({"error": "No data"}), 400

    doc_hash = data.get('document')
    if not doc_hash or not isinstance(doc_hash, str):
        logger.warning(f"KOSync: PUT progress with no document ID from {request.remote_addr}")
        return jsonify({"error": "Missing document ID"}), 400
    if len(doc_hash) > 64:
        return jsonify({"error": "Document hash too long"}), 400

    # Validate percentage
    percentage = data.get('percentage', 0)
    try:
        percentage = float(percentage)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid percentage value"}), 400
    if percentage < 0.0 or percentage > 1.0:
        return jsonify({"error": "Percentage must be between 0.0 and 1.0"}), 400

    logger.info(f"KOSync: PUT progress request for doc {doc_hash[:8]}... from {request.remote_addr} (device: {data.get('device', 'unknown')})")

    progress = str(data.get('progress', ''))[:512]
    device = str(data.get('device', ''))[:128]
    device_id = str(data.get('device_id', ''))[:64]

    now = datetime.utcnow()

    kosync_doc = _database_service.get_kosync_document(doc_hash)

    # Optional "furthest wins" protection
    furthest_wins = os.environ.get('KOSYNC_FURTHEST_WINS', 'true').lower() == 'true'
    force_update = data.get('force', False)

    # Allow rewinds if:
    # 1. Force flag is set (e.g. from SyncManager)
    # 2. Update comes from the SAME device (user moved slider back)
    same_device = (kosync_doc and kosync_doc.device_id == device_id)

    if furthest_wins and kosync_doc and kosync_doc.percentage and not force_update and not same_device:
        existing_pct = float(kosync_doc.percentage)
        new_pct = float(percentage)

        if new_pct < existing_pct - 0.0001:
            logger.info(f"KOSync: Ignored progress from '{device}' for doc {doc_hash[:8]}... (server has higher: {existing_pct:.2f}% vs new {new_pct:.2f}%)")
            return jsonify({
                "document": doc_hash,
                "timestamp": int(kosync_doc.timestamp.timestamp()) if kosync_doc.timestamp else int(now.timestamp())
            }), 200

    if kosync_doc is None:
        kosync_doc = KosyncDocument(
            document_hash=doc_hash,
            progress=progress,
            percentage=percentage,
            device=device,
            device_id=device_id,
            timestamp=now
        )
        logger.info(f"KOSync: New document tracked: {doc_hash[:8]}... from device '{device}'")
    else:
        logger.info(f"KOSync: Received progress from '{device}' for doc {doc_hash[:8]}... -> {float(percentage):.2f}% (Updated from {float(kosync_doc.percentage) if kosync_doc.percentage else 0:.2f}%)")
        kosync_doc.progress = progress
        kosync_doc.percentage = percentage
        kosync_doc.device = device
        kosync_doc.device_id = device_id
        kosync_doc.timestamp = now

    _database_service.save_kosync_document(kosync_doc)

    # Update linked book if exists
    linked_book = None
    if kosync_doc.linked_abs_id:
        linked_book = _database_service.get_book_by_abs_id(kosync_doc.linked_abs_id)
    else:
        linked_book = _database_service.get_book_by_kosync_id(doc_hash)
        if linked_book:
            _database_service.link_kosync_document(doc_hash, linked_book.id, linked_book.abs_id)

    # AUTO-DISCOVERY
    if not linked_book:
        auto_create = os.environ.get('AUTO_CREATE_EBOOK_MAPPING', 'true').lower() == 'true'
        if auto_create and _kosync_service.start_discovery_if_available(doc_hash):
            threading.Thread(
                target=_kosync_service.run_put_auto_discovery, args=(doc_hash,), daemon=True
            ).start()

    if linked_book:
        # Flag activity on paused/DNF books
        if linked_book.status in ('paused', 'dnf', 'not_started') and not linked_book.activity_flag:
            linked_book.activity_flag = True
            _database_service.save_book(linked_book)
            logger.info(f"KOSync PUT: Activity detected on {linked_book.status} book '{linked_book.title}'")

        # NOTE: We intentionally do NOT update book_states here.
        # The sync cycle is the only thing that should update book_states.
        # This ensures proper delta detection between cycles.
        logger.debug(f"KOSync: Updated linked book '{linked_book.title}' to {percentage:.2%}")

        # Debounce sync trigger — wait until the reader stops turning pages
        # Skip if the update came from the sync bot itself (prevents sync→PUT→sync loop)
        # Skip if instant sync is globally disabled.
        is_internal = device and device.lower() in INTERNAL_DEVICE_NAMES
        instant_sync_enabled = os.environ.get('INSTANT_SYNC_ENABLED', 'true').lower() != 'false'
        if linked_book.status == 'active' and _manager and not is_internal and instant_sync_enabled:
            logger.debug(f"KOSync PUT: Progress event recorded for '{linked_book.title}'")
            _record_kosync_event(linked_book.id, linked_book.title)

    response_timestamp = now.isoformat() + "Z"
    if device and device.lower() == "booknexus":
        # BookNexus expects an integer timestamp (Unix epoch)
        response_timestamp = int(now.timestamp())

    return jsonify({
        "document": doc_hash,
        "timestamp": response_timestamp
    }), 200


# ---------------- GET Fallback Helpers ----------------

def _respond_from_book_states(doc_id, book):
    """Build a GET response from a book's state data. Returns (response, status_code)."""
    states = _database_service.get_states_for_book(book.id)

    # Also check sibling kosync_documents for device-specific progress
    sibling_docs = _database_service.get_kosync_documents_for_book_by_book_id(book.id)
    # Filter out stale siblings (not updated in >30 days), with fallback to any sibling with progress
    now_ts = time.time()
    docs_with_progress = [
        d for d in sibling_docs
        if d.percentage and float(d.percentage) > 0
        and d.timestamp and (now_ts - d.timestamp.timestamp()) < 30 * 86400
    ]
    if not docs_with_progress:
        docs_with_progress = [
            d for d in sibling_docs
            if d.percentage and float(d.percentage) > 0 and d.timestamp
        ]
    if docs_with_progress:
        best_doc = max(docs_with_progress, key=lambda d: float(d.percentage))
        logger.info(f"KOSync: Resolved {doc_id[:8]}... to '{book.title}' via sibling hash {best_doc.document_hash[:8]}... ({float(best_doc.percentage):.2%})")
        return jsonify(_kosync_service.serialize_progress(best_doc, doc_id)), 200

    if not states:
        return jsonify({"message": "Document not found on server"}), 502

    kosync_state = next((s for s in states if s.client_name.lower() == 'kosync'), None)
    latest_state = kosync_state or max(states, key=lambda s: s.last_updated if s.last_updated else 0)

    return jsonify({
        "device": "pagekeeper",
        "device_id": "pagekeeper",
        "document": doc_id,
        "percentage": float(latest_state.percentage) if latest_state.percentage else 0,
        "progress": (latest_state.xpath or latest_state.cfi) if hasattr(latest_state, 'xpath') else "",
        "timestamp": int(latest_state.last_updated) if latest_state.last_updated else 0
    }), 200


# ---------------- Admin Auth ----------------

_PRIVATE_NETWORKS = (
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.168.0.0/16'),
    ipaddress.ip_network('127.0.0.0/8'),
    ipaddress.ip_network('::1/128'),
    ipaddress.ip_network('fd00::/8'),
)


def _is_private_ip(addr: str) -> bool:
    """Check if an address is on a private/local network."""
    try:
        ip = ipaddress.ip_address(addr)
        return any(ip in net for net in _PRIVATE_NETWORKS)
    except (ValueError, TypeError):
        return False


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
        user = request.headers.get('x-auth-user')
        key = request.headers.get('x-auth-key')
        expected_user = os.environ.get("KOSYNC_USER")
        expected_password = os.environ.get("KOSYNC_KEY")

        if not expected_user or not expected_password:
            return jsonify({"error": "Unauthorized"}), 401

        expected_hash = hash_kosync_key(expected_password)
        if (user and expected_user and user.lower() == expected_user.lower()
                and key and hmac.compare_digest(key, expected_hash)):
            return f(*args, **kwargs)

        logger.warning(f"KOSync Admin: Unauthorized access attempt from public IP '{request.remote_addr}'")
        return jsonify({"error": "Unauthorized"}), 401
    return decorated_function


# ---------------- KOSync Document Management API ----------------

@kosync_admin_bp.route('/api/kosync-documents', methods=['GET'])
@admin_or_local_required
def api_get_kosync_documents():
    """Get all KOSync documents with their link status."""
    docs = _database_service.get_all_kosync_documents()
    result = []
    for doc in docs:
        linked_book = None
        if doc.linked_book_id:
            linked_book = _database_service.get_book_by_id(doc.linked_book_id)

        result.append({
            'document_hash': doc.document_hash,
            'progress': doc.progress,
            'percentage': float(doc.percentage) if doc.percentage else 0,
            'device': doc.device,
            'device_id': doc.device_id,
            'timestamp': doc.timestamp.isoformat() if doc.timestamp else None,
            'first_seen': doc.first_seen.isoformat() if doc.first_seen else None,
            'last_updated': doc.last_updated.isoformat() if doc.last_updated else None,
            'linked_book_id': doc.linked_book_id,
            'linked_abs_id': doc.linked_abs_id,
            'linked_book_title': linked_book.title if linked_book else None,
        })

    return jsonify({
        'documents': result,
        'total': len(result),
        'linked': sum(1 for d in result if d['linked_book_id']),
        'unlinked': sum(1 for d in result if not d['linked_book_id']),
    })


@kosync_admin_bp.route('/api/kosync-documents/<doc_hash>/link', methods=['POST'])
@admin_or_local_required
def api_link_kosync_document(doc_hash):
    """Link a KOSync document to a book (by abs_id or book_id)."""
    data = request.json
    if not data:
        return jsonify({'error': 'Missing request body'}), 400

    # Accept abs_id or book_id for ebook-only books that have no abs_id
    book = None
    if data.get('abs_id'):
        book = _database_service.get_book_by_abs_id(data['abs_id'])
    elif data.get('book_id'):
        book = _database_service.get_book_by_id(data['book_id'])
    else:
        return jsonify({'error': 'Missing abs_id or book_id'}), 400

    if not book:
        return jsonify({'error': 'Book not found'}), 404

    doc = _database_service.get_kosync_document(doc_hash)
    if not doc:
        return jsonify({'error': 'KOSync document not found'}), 404

    success = _database_service.link_kosync_document(doc_hash, book.id, book.abs_id)
    if success:
        # [FIX] Always update the book's KOSync ID to match what we just linked.
        # This handles cases where the book had a "wrong" hash (e.g. from Storyteller artifact)
        # and we want to align it with the actual device hash.
        current_id = book.kosync_doc_id
        if current_id != doc_hash:
            logger.info(f"Updating Book {book.title} KOSync ID: {current_id} -> {doc_hash}")
            book.kosync_doc_id = doc_hash
            _database_service.save_book(book)

        # Cleanup: remove any actionable suggestion for this document since it's now linked
        _database_service.resolve_suggestion(doc_hash)

        return jsonify({'success': True, 'message': f'Linked to {book.title}'})

    return jsonify({'error': 'Failed to link document'}), 500


@kosync_admin_bp.route('/api/kosync-documents/<doc_hash>/unlink', methods=['POST'])
@admin_or_local_required
def api_unlink_kosync_document(doc_hash):
    """Remove the ABS book link from a KOSync document."""
    # Clear book.kosync_doc_id to prevent orphaned hash
    doc = _database_service.get_kosync_document(doc_hash)
    if doc and doc.linked_book_id:
        book = _database_service.get_book_by_id(doc.linked_book_id)
        if book and book.kosync_doc_id == doc_hash:
            book.kosync_doc_id = None
            _database_service.save_book(book)

    success = _database_service.unlink_kosync_document(doc_hash)
    if success:
        _cleanup_cache_for_hash(doc_hash)
        return jsonify({'success': True, 'message': 'Document unlinked'})
    return jsonify({'error': 'Document not found'}), 404


@kosync_admin_bp.route('/api/kosync-documents/<doc_hash>', methods=['DELETE'])
@admin_or_local_required
def api_delete_kosync_document(doc_hash):
    """Delete a KOSync document."""
    # Clear book.kosync_doc_id to prevent orphaned hash
    doc = _database_service.get_kosync_document(doc_hash)
    if doc and doc.linked_book_id:
        book = _database_service.get_book_by_id(doc.linked_book_id)
        if book and book.kosync_doc_id == doc_hash:
            book.kosync_doc_id = None
            _database_service.save_book(book)

    _cleanup_cache_for_hash(doc_hash)
    success = _database_service.delete_kosync_document(doc_hash)
    if success:
        return jsonify({'success': True, 'message': 'Document deleted'})
    return jsonify({'error': 'Document not found'}), 404


def _cleanup_cache_for_hash(doc_hash):
    """Delete cached EPUB file for a document."""
    try:
        # Identify filename from DB
        doc = _database_service.get_kosync_document(doc_hash)
        filename = doc.filename if doc else None

        # Fallback: check linked book
        if not filename and doc and doc.linked_abs_id:
            book = _database_service.get_book_by_abs_id(doc.linked_abs_id)
            if book:
                filename = book.original_ebook_filename or book.ebook_filename

        if filename:
            # Delete file if in epub_cache
            if _container:
                cache_dir = _container.data_dir() / "epub_cache"
                file_path = cache_dir / filename
                if not is_safe_path_within(file_path, cache_dir):
                    logger.warning(f"Blocked cache deletion — path escapes cache dir: '{filename}'")
                elif file_path.exists():
                    try:
                        os.remove(file_path)
                        logger.info(f"Deleted cached EPUB: {filename}")
                    except Exception as e:
                        logger.warning(f"Failed to delete cached file '{filename}': {e}")

        # Note: We don't delete the KosyncDocument record here,
        # as it may contain important progress data.
        # The filename/mtime/source fields just become stale or are cleared if unlinked.

    except Exception as e:
        logger.error(f"Error cleaning up cache for '{doc_hash}': {e}")


# ---------------- KOSync Document Management Page + New Endpoints ----------------

@kosync_admin_bp.route('/kosync-documents')
@admin_or_local_required
def kosync_documents_page():
    """Render the KoSync Document Management page."""
    docs = _database_service.get_all_kosync_documents()
    documents = []
    for doc in docs:
        linked_book = None
        if doc.linked_book_id:
            linked_book = _database_service.get_book_by_id(doc.linked_book_id)

        documents.append({
            'document_hash': doc.document_hash,
            'progress': doc.progress,
            'percentage': float(doc.percentage) if doc.percentage else 0,
            'device': doc.device,
            'device_id': doc.device_id,
            'timestamp': doc.timestamp.isoformat() if doc.timestamp else None,
            'first_seen': doc.first_seen.isoformat() if doc.first_seen else None,
            'last_updated': doc.last_updated.isoformat() if doc.last_updated else None,
            'linked_abs_id': doc.linked_abs_id,
            'linked_book_id': doc.linked_book_id,
            'linked_book_title': linked_book.title if linked_book else None,
        })

    orphaned = _kosync_service.get_orphaned_kosync_books()
    orphaned_books = [{
        'book_id': b.id,
        'abs_id': b.abs_id,
        'title': b.title,
        'kosync_doc_id': b.kosync_doc_id,
        'status': b.status,
        'sync_mode': b.sync_mode,
    } for b in orphaned]

    return render_template('kosync_documents.html',
                           documents=documents,
                           orphaned_books=orphaned_books)


@kosync_admin_bp.route('/api/kosync-documents/orphaned', methods=['GET'])
@admin_or_local_required
def api_get_orphaned_kosync_books():
    """Get books with kosync_doc_id set but no matching KosyncDocument."""
    orphaned = _kosync_service.get_orphaned_kosync_books()
    return jsonify([{
        'book_id': b.id,
        'abs_id': b.abs_id,
        'title': b.title,
        'kosync_doc_id': b.kosync_doc_id,
        'status': b.status,
        'sync_mode': b.sync_mode,
    } for b in orphaned])


@kosync_admin_bp.route('/api/kosync-documents/clear-orphan/<int:book_id>', methods=['POST'])
@admin_or_local_required
def api_clear_orphaned_hash(book_id):
    """Clear kosync_doc_id from a book to stop 502 cycle."""
    book = _kosync_service.clear_orphaned_hash(book_id)
    if book:
        return jsonify({'success': True, 'message': f'Cleared hash from {book.title}'})
    return jsonify({'error': 'Book not found'}), 404


@kosync_admin_bp.route('/api/kosync-documents/resolve-orphan/<int:book_id>', methods=['POST'])
@admin_or_local_required
def api_resolve_orphaned_hash(book_id):
    """Create a KosyncDocument for an orphaned hash and link it to a book.

    By default links to the book that owns the hash. If target_book_id is
    provided, links to that book instead (and clears the hash from the
    original book).
    """
    source_book = _database_service.get_book_by_id(book_id)
    if not source_book or not source_book.kosync_doc_id:
        return jsonify({'error': 'Book not found or has no hash'}), 404

    doc_hash = source_book.kosync_doc_id
    data = request.json or {}
    target_book_id = data.get('target_book_id')

    if target_book_id:
        target_book = _database_service.get_book_by_id(target_book_id)
        if not target_book:
            return jsonify({'error': 'Target book not found'}), 404
        # Clear hash from source, register on target
        source_book.kosync_doc_id = None
        _database_service.save_book(source_book)
        target_book.kosync_doc_id = doc_hash
        _database_service.save_book(target_book)
        _kosync_service.register_hash_for_book(doc_hash, target_book)
        return jsonify({'success': True, 'message': f'Linked hash to {target_book.title}'})

    _kosync_service.register_hash_for_book(doc_hash, source_book)
    return jsonify({'success': True, 'message': f'Linked hash to {source_book.title}'})


@kosync_admin_bp.route('/api/kosync-documents/<doc_hash>/create-book', methods=['POST'])
@admin_or_local_required
def api_create_book_from_hash(doc_hash):
    """Create an ebook-only book from an unlinked KoSync document."""
    data = request.json
    if not data or not data.get('title', '').strip():
        return jsonify({'error': 'Title is required'}), 400

    doc = _database_service.get_kosync_document(doc_hash)
    if not doc:
        return jsonify({'error': 'KoSync document not found'}), 404

    title = data['title'].strip()
    book = _kosync_service.create_ebook_only_book(doc_hash, title, doc.filename)
    return jsonify({
        'success': True,
        'message': f'Created book "{book.title}"',
        'book_id': book.id,
    })
