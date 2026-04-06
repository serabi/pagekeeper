"""KoSync business logic extracted from kosync_server.py.

Handles EPUB discovery, hash-to-book linking, auto-discovery, and
document management. Route handlers in kosync_server.py delegate here.
"""

import calendar
import json
import logging
import os
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from src.db.models import Book, KosyncDocument
from src.utils.constants import INTERNAL_DEVICE_NAMES
from src.utils.logging_utils import sanitize_log_data
from src.utils.path_utils import is_safe_path_within

logger = logging.getLogger(__name__)

# Auto-discovery concurrency cap
_MAX_ACTIVE_SCANS = 5


def _normalize_title(s):
    """Strip punctuation and lowercase for fuzzy title matching."""
    return re.sub(r"[^\w\s]", "", s.lower())


def ensure_kosync_document(book, database_service):
    """Create a KosyncDocument for a book's kosync_doc_id if one doesn't exist.

    Call after saving a book with a kosync_doc_id to prevent orphaned hashes
    that cause 502 errors every sync cycle.
    """
    if not book or not book.kosync_doc_id or not book.id:
        return
    try:
        existing = database_service.get_kosync_document(book.kosync_doc_id)
        if existing:
            if not existing.linked_book_id:
                database_service.link_kosync_document(book.kosync_doc_id, book.id, book.abs_id)
                logger.info(f"KOSync: Linked existing document {book.kosync_doc_id[:8]}... to '{book.title}'")
            if not existing.filename and book.ebook_filename:
                existing.filename = book.ebook_filename
                database_service.save_kosync_document(existing)
        else:
            doc = KosyncDocument(
                document_hash=book.kosync_doc_id,
                linked_book_id=book.id,
                linked_abs_id=book.abs_id,
                filename=book.ebook_filename,
            )
            database_service.save_kosync_document(doc)
            logger.debug(f"KOSync: Created document {book.kosync_doc_id[:8]}... for '{book.title}'")
    except Exception:
        logger.warning(
            f"KOSync: Failed to ensure document for book {book.id} "
            f"(hash {book.kosync_doc_id[:8]}...) — will retry on next sync cycle"
        )


class KosyncService:
    """Business logic for KoSync document management and EPUB discovery."""

    def __init__(self, database_service, container, manager=None, ebook_dir=None):
        self._db = database_service
        self._container = container
        self._manager = manager
        self._ebook_dir = ebook_dir
        self._active_scans = set()
        self._active_scans_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    #  Progress serialization (was duplicated 3x in kosync_server.py)
    # ------------------------------------------------------------------ #

    @staticmethod
    def serialize_progress(doc, doc_id=None, device_default="pagekeeper"):
        """Build a KoSync-protocol progress dict from a KosyncDocument."""
        return {
            "device": doc.device or device_default,
            "device_id": doc.device_id or device_default,
            "document": doc_id or doc.document_hash,
            "percentage": float(doc.percentage) if doc.percentage else 0,
            "progress": doc.progress or "",
            "timestamp": int(doc.timestamp.timestamp()) if doc.timestamp else 0,
        }

    # ------------------------------------------------------------------ #
    #  Book resolution helpers
    # ------------------------------------------------------------------ #

    def resolve_book_by_sibling_hash(self, doc_id, existing_doc=None):
        """Try to resolve an unknown hash to a book via sibling filename matches."""
        doc = existing_doc or self._db.get_kosync_document(doc_id)
        if doc and doc.filename:
            # Find sibling document with same filename that's linked
            sibling = self._db.get_kosync_doc_by_filename(doc.filename)
            if sibling and (sibling.linked_book_id or sibling.linked_abs_id) and sibling.document_hash != doc_id:
                book = (
                    self._db.get_book_by_id(sibling.linked_book_id)
                    if sibling.linked_book_id
                    else self._db.get_book_by_abs_id(sibling.linked_abs_id)
                )
                if book:
                    logger.info(f"KOSync: Resolved {doc_id[:8]}... to '{book.title}' via filename sibling")
                    return book

            # Check if filename matches a book's ebook_filename directly
            book = self._db.get_book_by_ebook_filename(doc.filename)
            if book:
                logger.info(f"KOSync: Resolved {doc_id[:8]}... to '{book.title}' via ebook filename match")
                return book

        return None

    def register_hash_for_book(self, doc_id, book):
        """Register a new hash and link it to an existing book."""
        existing = self._db.get_kosync_document(doc_id)
        if existing:
            if not existing.linked_book_id:
                self._db.link_kosync_document(doc_id, book.id, book.abs_id)
                logger.info(f"KOSync: Linked existing document {doc_id[:8]}... to '{book.title}'")
        else:
            doc = KosyncDocument(
                document_hash=doc_id,
                linked_book_id=book.id,
                linked_abs_id=book.abs_id,
                filename=book.ebook_filename,
            )
            self._db.save_kosync_document(doc)
            logger.info(f"KOSync: Created and linked new document {doc_id[:8]}... to '{book.title}'")

    # ------------------------------------------------------------------ #
    #  EPUB discovery — decomposed from _try_find_epub_by_hash (151 lines)
    # ------------------------------------------------------------------ #

    def find_epub_by_hash(self, doc_hash):
        """Try to find matching EPUB file for a KoSync document hash.

        Searches in order: DB cache → filesystem → Grimmory API.
        Returns the epub filename on match, or None.
        """
        try:
            result = self._find_epub_in_db(doc_hash)
            if result:
                return result

            result = self._find_epub_in_filesystem(doc_hash)
            if result:
                return result

            result = self._find_epub_in_grimmory(doc_hash)
            if result:
                return result

        except Exception as e:
            logger.error(f"Error in EPUB auto-discovery: {e}")
            return None

        logger.info("Auto-discovery finished. No match found")
        return None

    def _find_epub_in_db(self, doc_hash):
        """Check DB for cached filename or linked book's original filename."""
        doc = self._db.get_kosync_document(doc_hash)
        if doc and doc.filename:
            try:
                self._container.ebook_parser().resolve_book_path(doc.filename)
                logger.info(f"Matched EPUB via DB: {doc.filename}")
                return doc.filename
            except FileNotFoundError:
                logger.debug(f"DB suggested '{doc.filename}' but file is missing — Re-scanning")

        if doc and (doc.linked_book_id or doc.linked_abs_id):
            book = (
                self._db.get_book_by_id(doc.linked_book_id)
                if doc.linked_book_id
                else self._db.get_book_by_abs_id(doc.linked_abs_id)
            )
            if book and book.original_ebook_filename:
                try:
                    self._container.ebook_parser().resolve_book_path(book.original_ebook_filename)
                    logger.info(f"Matched EPUB via Linked Book Original Filename: {book.original_ebook_filename}")
                    return book.original_ebook_filename
                except Exception as e:
                    logger.debug(f"Failed to resolve original filename for {doc.linked_abs_id}: {e}")

        return None

    def _find_epub_in_filesystem(self, doc_hash):
        """Scan configured ebook directory for matching hash."""
        if not self._ebook_dir or not self._ebook_dir.exists():
            return None

        logger.info(f"Starting filesystem search in {self._ebook_dir} for hash {doc_hash[:8]}...")
        count = 0
        for epub_path in self._ebook_dir.rglob("*.epub"):
            count += 1
            if count % 100 == 0:
                logger.debug(f"Checked {count} local EPUBs...")

            # Optimization: check DB cache by filename first
            cached_doc = self._db.get_kosync_doc_by_filename(epub_path.name)
            if cached_doc:
                current_mtime = epub_path.stat().st_mtime
                if cached_doc.mtime == current_mtime:
                    if cached_doc.document_hash == doc_hash:
                        logger.info(f"Matched EPUB via DB filename lookup: {epub_path.name}")
                        return epub_path.name
                    continue

            try:
                computed_hash = self._container.ebook_parser().get_kosync_id(epub_path)

                # Store/update in DB — never mutate document_hash (primary key)
                if cached_doc and cached_doc.document_hash != computed_hash:
                    self._db.delete_kosync_document(cached_doc.document_hash)
                    cached_doc = None  # force create below
                if cached_doc:
                    cached_doc.mtime = epub_path.stat().st_mtime
                    cached_doc.source = "filesystem"
                    self._db.save_kosync_document(cached_doc)
                else:
                    self._upsert_kosync_metadata(
                        computed_hash, epub_path.name, "filesystem", mtime=epub_path.stat().st_mtime
                    )

                if computed_hash == doc_hash:
                    logger.info(f"Matched EPUB via filesystem: {epub_path.name}")
                    return epub_path.name
            except Exception as e:
                logger.debug(f"Error checking file {epub_path.name}: {e}")

        logger.info(f"Filesystem search finished. Checked {count} files. No match found")
        return None

    def _find_epub_in_grimmory(self, doc_hash):
        """Search Grimmory API for matching EPUB hash."""
        bl_group = self._container.grimmory_client_group()
        if not bl_group.is_configured():
            return None

        logger.info("Starting Grimmory API search...")

        try:
            books = self._db.get_all_grimmory_books()
            if not books:
                logger.info("Grimmory cache in DB is empty. Syncing library...")
                bl_group.get_all_books()
                books = self._db.get_all_grimmory_books()

            logger.info(f"Scanning {len(books)} books from Grimmory DB cache...")

            for book in books:
                raw_id = book.raw_metadata_dict.get("id") if getattr(book, "raw_metadata_dict", None) else None
                book_id = str(raw_id) if raw_id is not None else None
                if not book_id:
                    try:
                        meta = json.loads(book.raw_metadata)
                        fallback_id = meta.get("id")
                        book_id = str(fallback_id) if fallback_id is not None else None
                    except (json.JSONDecodeError, AttributeError, TypeError) as e:
                        logger.debug(f"Failed to parse raw_metadata JSON: {e}")
                        continue

                if not book_id:
                    continue

                qualified_id = f"{book.server_id}:{book_id}"

                # Check if we have a KosyncDocument for this Grimmory ID
                cached_doc = self._db.get_kosync_doc_by_grimmory_id(qualified_id)
                if cached_doc:
                    if cached_doc.document_hash == doc_hash:
                        logger.info(f"Matched EPUB via Grimmory ID in DB: {cached_doc.filename}")
                        return cached_doc.filename

                try:
                    book_content = bl_group.download_book(qualified_id)
                    if book_content:
                        computed_hash = self._container.ebook_parser().get_kosync_id_from_bytes(
                            book.filename, book_content
                        )

                        if computed_hash == doc_hash:
                            safe_title = f"{book.server_id}_{Path(book.filename).name}"
                            cache_dir = self._container.data_dir() / "epub_cache"
                            cache_dir.mkdir(parents=True, exist_ok=True)
                            cache_path = cache_dir / safe_title
                            if not is_safe_path_within(cache_path, cache_dir):
                                logger.warning(f"Blocked cache write — path escapes cache dir: '{safe_title}'")
                            else:
                                with open(cache_path, "wb") as f:
                                    f.write(book_content)
                                logger.info(f"Persisted Grimmory book to cache: {safe_title}")

                            self._upsert_kosync_metadata(
                                computed_hash, safe_title, "grimmory", grimmory_id=qualified_id
                            )

                            logger.info(f"Matched EPUB via Grimmory download: {safe_title}")
                            return safe_title
                except Exception as e:
                    logger.warning(f"Failed to check Grimmory book '{sanitize_log_data(book.title)}': {e}")

            logger.info(f"Grimmory search finished. Checked {len(books)} books. No match found")

        except Exception as e:
            logger.debug(f"Error querying Grimmory for EPUB matching: {e}")

        return None

    def _upsert_kosync_metadata(self, document_hash, filename, source, mtime=None, grimmory_id=None):
        """Cache hash metadata without overwriting any existing progress data."""
        existing = self._db.get_kosync_document(document_hash)
        if existing:
            existing.filename = filename
            existing.source = source
            if mtime is not None:
                existing.mtime = mtime
            if grimmory_id is not None:
                existing.grimmory_id = grimmory_id
            self._db.save_kosync_document(existing)
        else:
            doc = KosyncDocument(
                document_hash=document_hash,
                filename=filename,
                source=source,
                mtime=mtime,
                grimmory_id=grimmory_id,
            )
            self._db.save_kosync_document(doc)

    # ------------------------------------------------------------------ #
    #  Auto-discovery (background threads)
    # ------------------------------------------------------------------ #

    def start_discovery_if_available(self, doc_hash):
        """Acquire a discovery slot and return True if started, False if skipped."""
        with self._active_scans_lock:
            if doc_hash in self._active_scans or len(self._active_scans) >= _MAX_ACTIVE_SCANS:
                return False
            self._active_scans.add(doc_hash)
            return True

    def finish_discovery(self, doc_hash):
        """Release a discovery slot."""
        with self._active_scans_lock:
            self._active_scans.discard(doc_hash)

    def run_get_auto_discovery(self, doc_id):
        """Background discovery for GET: find epub and link to existing book."""
        try:
            logger.info(f"KOSync: Background discovery (GET) for {doc_id[:8]}...")
            epub_filename = self.find_epub_by_hash(doc_id)

            if not epub_filename:
                logger.info(f"KOSync: GET-discovery found no epub for {doc_id[:8]}...")
                return

            # Update stub with filename
            doc = self._db.get_kosync_document(doc_id)
            if doc and not doc.filename:
                doc.filename = epub_filename
                self._db.save_kosync_document(doc)

            # Try to find an existing book that uses this epub
            book = self._db.get_book_by_ebook_filename(epub_filename)
            if book:
                self._db.link_kosync_document(doc_id, book.id, book.abs_id)
                logger.info(f"KOSync: GET-discovery linked {doc_id[:8]}... to '{book.title}'")
                return

            logger.info(f"KOSync: GET-discovery found epub '{epub_filename}' but no matching book")
        except Exception as e:
            logger.error(f"Error in GET auto-discovery: {e}")
        finally:
            self.finish_discovery(doc_id)

    def run_put_auto_discovery(self, doc_hash):
        """Background discovery for PUT: find epub, match audiobook, create suggestion or book."""
        try:
            logger.info(f"KOSync: Scheduled auto-discovery for unmapped document {doc_hash[:8]}...")
            epub_filename = self.find_epub_by_hash(doc_hash)

            if not epub_filename:
                logger.debug(f"Could not auto-match EPUB for KOSync document '{doc_hash[:8]}'")
                return

            # Derive title from filename — strip server_id prefix from Grimmory-cached files
            stem = Path(epub_filename).stem
            # Grimmory cache files are named "{server_id}_{original}" — strip numeric prefix
            if "_" in stem:
                prefix, candidate = stem.split("_", 1)
                if prefix.isdigit() and candidate:
                    stem = candidate
            title = stem

            # Step 1: Search ABS for matching audiobooks
            audiobook_matches = self._search_abs_audiobooks(title)

            # Step 2: If matches found, auto-create for single exact match or create suggestion
            if audiobook_matches:
                exact_matches = [m for m in audiobook_matches if m.get("confidence") == "exact"]

                if len(exact_matches) == 1:
                    # High confidence single match — auto-create book
                    match = exact_matches[0]
                    book = Book(
                        abs_id=match["abs_id"],
                        title=match["title"],
                        ebook_filename=epub_filename,
                        kosync_doc_id=doc_hash,
                        transcript_file=None,
                        status="active",
                        duration=match.get("duration"),
                        sync_mode="audiobook",
                    )
                    self._db.save_book(book, is_new=True)
                    self._db.link_kosync_document(doc_hash, book.id, book.abs_id)
                    self._db.resolve_detected_book(doc_hash, source="kosync")
                    self._db.resolve_suggestion(doc_hash)
                    logger.info(
                        f"Auto-created book '{match['title']}' from exact title match (abs_id={match['abs_id']})"
                    )
                    if self._manager:
                        self._manager.sync_cycle(target_book_id=book.id)
                    return

                # Multiple exact or only fuzzy matches — delegate to suggestion service
                try:
                    suggestion_svc = self._container.suggestion_service()
                    suggestion_svc.queue_kosync_suggestion(doc_hash, filename=epub_filename)
                except Exception as e:
                    logger.warning(f"KoSync auto-discovery: suggestion creation failed for {doc_hash[:8]}...: {e}")
                return

            # Step 3: No audiobook found — create ebook-only book
            self.create_ebook_only_book(doc_hash, title, epub_filename)

        except Exception as e:
            logger.error(f"Error in auto-discovery background task: {e}")
        finally:
            self.finish_discovery(doc_hash)

    def _search_abs_audiobooks(self, search_term):
        """Search AudiobookShelf for audiobooks matching a title. Returns match list."""
        if not self._container.abs_client().is_configured():
            return []

        matches = []
        try:
            audiobooks = self._container.abs_client().get_all_audiobooks()
            logger.debug(
                f"Auto-discovery: Searching for audiobook matching '{search_term}' in {len(audiobooks)} audiobooks"
            )
            search_norm = _normalize_title(search_term)

            for ab in audiobooks:
                media = ab.get("media", {})
                metadata = media.get("metadata", {})
                ab_title = metadata.get("title") or ab.get("name", "")
                ab_author = metadata.get("authorName", "")
                title_norm = _normalize_title(ab_title)

                if not (search_norm and title_norm):
                    continue
                if not (search_norm in title_norm or title_norm in search_norm):
                    continue

                # Skip books with high progress (>75%)
                duration = media.get("duration", 0)
                if duration > 0:
                    try:
                        ab_progress = self._container.abs_client().get_progress(ab["id"])
                        if ab_progress and ab_progress.get("progress", 0) * 100 > 75:
                            logger.debug(f"Auto-discovery: Skipping '{ab_title}' - already >75% complete")
                            continue
                    except Exception as e:
                        logger.debug(f"Failed to get ABS progress during auto-discovery: {e}")

                confidence = "exact" if search_norm == title_norm else "high"
                logger.debug(f"Auto-discovery: Matched '{ab_title}' by {ab_author} (confidence: {confidence})")
                matches.append(
                    {
                        "source": "abs",
                        "abs_id": ab["id"],
                        "title": ab_title,
                        "author": ab_author,
                        "duration": duration,
                        "confidence": confidence,
                    }
                )

        except Exception as e:
            logger.warning(f"Error searching ABS for audiobooks: {e}")

        return matches

    # ------------------------------------------------------------------ #
    #  Book creation and management
    # ------------------------------------------------------------------ #

    def create_ebook_only_book(self, doc_hash, title, epub_filename=None):
        """Create a new ebook-only Book and link the KosyncDocument to it."""
        book = Book(
            abs_id=None,
            title=title,
            ebook_filename=epub_filename,
            kosync_doc_id=doc_hash,
            transcript_file=None,
            status="active",
            duration=None,
            sync_mode="ebook_only",
        )
        self._db.save_book(book, is_new=True)
        self._db.link_kosync_document(doc_hash, book.id, book.abs_id)
        self._db.resolve_detected_book(doc_hash, source="kosync")
        self._db.resolve_suggestion(doc_hash)
        logger.info(f"Created ebook-only book: {book.id} '{title}'" + (f" -> {epub_filename}" if epub_filename else ""))

        if self._manager:
            self._manager.sync_cycle(target_book_id=book.id)

        return book

    def get_orphaned_kosync_books(self):
        """Get books with kosync_doc_id set but no matching KosyncDocument."""
        return self._db.get_orphaned_kosync_books()

    def clear_orphaned_hash(self, book_id):
        """Clear kosync_doc_id from a book to stop 502 cycle."""
        book = self._db.get_book_by_id(book_id)
        if not book:
            return None
        old_hash = book.kosync_doc_id
        book.kosync_doc_id = None
        self._db.save_book(book)
        logger.info(f"Cleared orphaned KoSync hash from '{book.title}' (was: {old_hash})")
        return book

    # ------------------------------------------------------------------ #
    #  HTTP handler logic (moved from kosync_server.py route handlers)
    # ------------------------------------------------------------------ #

    def handle_put_progress(self, data, remote_addr, debounce_manager=None):
        """Process a KoSync PUT progress request. Returns (response_dict, status_code)."""

        if not data:
            logger.warning(f"KOSync: PUT progress with no JSON data from {remote_addr}")
            return {"error": "No data"}, 400

        doc_hash = data.get("document")
        if not doc_hash or not isinstance(doc_hash, str):
            logger.warning(f"KOSync: PUT progress with no document ID from {remote_addr}")
            return {"error": "Missing document ID"}, 400
        if len(doc_hash) > 64:
            return {"error": "Document hash too long"}, 400

        percentage = data.get("percentage", 0)
        try:
            percentage = float(percentage)
        except (TypeError, ValueError):
            return {"error": "Invalid percentage value"}, 400
        if percentage < 0.0 or percentage > 1.0:
            return {"error": "Percentage must be between 0.0 and 1.0"}, 400

        logger.info(
            f"KOSync: PUT progress request for doc {doc_hash[:8]}... from {remote_addr} (device: {data.get('device', 'unknown')})"
        )

        progress = str(data.get("progress", ""))[:512]
        device = str(data.get("device", ""))[:128]
        device_id = str(data.get("device_id", ""))[:64]

        now = datetime.now(UTC)

        kosync_doc = self._db.get_kosync_document(doc_hash)

        # Optional "furthest wins" protection
        furthest_wins = os.environ.get("KOSYNC_FURTHEST_WINS", "true").lower() == "true"
        force_update = data.get("force", False)
        same_device = kosync_doc and kosync_doc.device_id == device_id

        if furthest_wins and kosync_doc and kosync_doc.percentage and not force_update and not same_device:
            existing_pct = float(kosync_doc.percentage)
            new_pct = float(percentage)
            if new_pct < existing_pct - 0.0001:
                logger.info(
                    f"KOSync: Ignored progress from '{device}' for doc {doc_hash[:8]}... (server has higher: {existing_pct:.2f}% vs new {new_pct:.2f}%)"
                )
                return {
                    "document": doc_hash,
                    "timestamp": int(kosync_doc.timestamp.timestamp())
                    if kosync_doc.timestamp
                    else int(now.timestamp()),
                }, 200

        if kosync_doc is None:
            kosync_doc = KosyncDocument(
                document_hash=doc_hash,
                progress=progress,
                percentage=percentage,
                device=device,
                device_id=device_id,
                timestamp=now,
            )
            logger.info(f"KOSync: New document tracked: {doc_hash[:8]}... from device '{device}'")
        else:
            logger.info(
                f"KOSync: Received progress from '{device}' for doc {doc_hash[:8]}... -> {float(percentage):.2f}% (Updated from {float(kosync_doc.percentage) if kosync_doc.percentage else 0:.2f}%)"
            )
            kosync_doc.progress = progress
            kosync_doc.percentage = percentage
            kosync_doc.device = device
            kosync_doc.device_id = device_id
            kosync_doc.timestamp = now

        self._db.save_kosync_document(kosync_doc)

        if 0.01 <= percentage <= 0.70:
            try:
                suggestion_svc = self._container.suggestion_service()
                suggestion_svc.queue_kosync_suggestion(
                    doc_hash,
                    filename=kosync_doc.filename,
                    device=device,
                )
                detected = self._db.get_detected_book(doc_hash, source="kosync")
                if detected:
                    detected.progress_percentage = float(percentage)
                    self._db.save_detected_book(detected)
            except Exception as e:
                logger.debug(f"KOSync detected-book update failed for {doc_hash[:8]}...: {e}")

        # Update linked book if exists
        linked_book = None
        if kosync_doc.linked_book_id:
            linked_book = self._db.get_book_by_id(kosync_doc.linked_book_id)
        elif kosync_doc.linked_abs_id:
            linked_book = self._db.get_book_by_abs_id(kosync_doc.linked_abs_id)
        else:
            linked_book = self._db.get_book_by_kosync_id(doc_hash)
            if linked_book:
                self._db.link_kosync_document(doc_hash, linked_book.id, linked_book.abs_id)

        # AUTO-DISCOVERY + SUGGESTION
        if not linked_book:
            auto_create = os.environ.get("AUTO_CREATE_EBOOK_MAPPING", "true").lower() == "true"
            discovery_started = auto_create and self.start_discovery_if_available(doc_hash)
            if discovery_started:
                threading.Thread(target=self.run_put_auto_discovery, args=(doc_hash,), daemon=True).start()
            else:
                # Auto-discovery disabled or slots full — try suggestion via title matching
                try:
                    suggestion_svc = self._container.suggestion_service()
                    suggestion_svc.queue_kosync_suggestion(
                        doc_hash,
                        filename=kosync_doc.filename,
                        device=device,
                    )
                except Exception as e:
                    logger.debug(f"KoSync suggestion attempt failed for {doc_hash[:8]}...: {e}")

        if linked_book:
            # Flag activity on paused/DNF books
            if linked_book.status in ("paused", "dnf", "not_started") and not linked_book.activity_flag:
                linked_book.activity_flag = True
                self._db.save_book(linked_book)
                logger.info(f"KOSync PUT: Activity detected on {linked_book.status} book '{linked_book.title}'")

            logger.debug(f"KOSync: Updated linked book '{linked_book.title}' to {percentage:.2%}")

            # Debounce sync trigger
            is_internal = device and device.lower() in INTERNAL_DEVICE_NAMES
            instant_sync_enabled = os.environ.get("INSTANT_SYNC_ENABLED", "true").lower() != "false"
            if linked_book.status == "active" and self._manager and not is_internal and instant_sync_enabled:
                if debounce_manager:
                    logger.debug(f"KOSync PUT: Progress event recorded for '{linked_book.title}'")
                    debounce_manager.record_event(linked_book.id, linked_book.title)

        response_timestamp = now.isoformat() + "Z"
        if device and device.lower() == "booknexus":
            response_timestamp = int(calendar.timegm(now.timetuple()))

        return {"document": doc_hash, "timestamp": response_timestamp}, 200

    def handle_get_progress(self, doc_id, remote_addr):
        """Process a KoSync GET progress request. Returns (response_dict, status_code)."""

        if len(doc_id) > 64:
            return {"error": "Document ID too long"}, 400

        logger.info(f"KOSync: GET progress for doc {doc_id[:8]}... from {remote_addr}")

        # Step 1: Direct hash lookup
        kosync_doc = self._db.get_kosync_document(doc_id)
        if kosync_doc:
            if kosync_doc.linked_book_id:
                book = self._db.get_book_by_id(kosync_doc.linked_book_id)
                if book:
                    return self.resolve_best_progress(doc_id, book)
            elif kosync_doc.linked_abs_id:
                book = self._db.get_book_by_abs_id(kosync_doc.linked_abs_id)
                if book:
                    return self.resolve_best_progress(doc_id, book)

            has_progress = kosync_doc.percentage and float(kosync_doc.percentage) > 0
            if has_progress:
                return self.serialize_progress(kosync_doc, device_default=""), 200

        # Step 2: Book lookup by kosync_doc_id
        book = self._db.get_book_by_kosync_id(doc_id)
        if book:
            return self.resolve_best_progress(doc_id, book)

        # Step 3: Sibling hash resolution
        resolved_book = self.resolve_book_by_sibling_hash(doc_id, existing_doc=kosync_doc)
        if resolved_book:
            self.register_hash_for_book(doc_id, resolved_book)
            return self.resolve_best_progress(doc_id, resolved_book)

        # Step 4: Unknown hash — register stub and start background discovery
        auto_create = os.environ.get("AUTO_CREATE_EBOOK_MAPPING", "true").lower() == "true"
        if auto_create and self.start_discovery_if_available(doc_id):
            stub = KosyncDocument(document_hash=doc_id)
            self._db.save_kosync_document(stub)
            logger.info(f"KOSync: Created stub for unknown hash {doc_id[:8]}..., starting background discovery")
            threading.Thread(target=self.run_get_auto_discovery, args=(doc_id,), daemon=True).start()

        logger.warning(f"KOSync: Document not found: {doc_id[:8]}... (GET from {remote_addr})")
        return {"message": "Document not found on server"}, 502

    def resolve_best_progress(self, doc_id, book):
        """Find the best progress data for a book across sibling docs and states.

        Returns (response_dict, status_code).
        """

        states = self._db.get_states_for_book(book.id)

        sibling_docs = self._db.get_kosync_documents_for_book_by_book_id(book.id)
        now_ts = time.time()
        docs_with_progress = [
            d
            for d in sibling_docs
            if d.percentage
            and float(d.percentage) > 0
            and d.timestamp
            and (now_ts - d.timestamp.timestamp()) < 30 * 86400
        ]
        if not docs_with_progress:
            docs_with_progress = [d for d in sibling_docs if d.percentage and float(d.percentage) > 0 and d.timestamp]
        if docs_with_progress:
            best_doc = max(docs_with_progress, key=lambda d: float(d.percentage))
            logger.info(
                f"KOSync: Resolved {doc_id[:8]}... to '{book.title}' via sibling hash {best_doc.document_hash[:8]}... ({float(best_doc.percentage):.2%})"
            )
            return self.serialize_progress(best_doc, doc_id), 200

        if not states:
            return {"message": "Document not found on server"}, 502

        kosync_state = next((s for s in states if s.client_name.lower() == "kosync"), None)
        latest_state = kosync_state or max(states, key=lambda s: s.last_updated or datetime.min)

        return {
            "device": "pagekeeper",
            "device_id": "pagekeeper",
            "document": doc_id,
            "percentage": float(latest_state.percentage) if latest_state.percentage else 0,
            "progress": (latest_state.xpath or latest_state.cfi) if hasattr(latest_state, "xpath") else "",
            "timestamp": int(latest_state.last_updated) if latest_state.last_updated else 0,
        }, 200
