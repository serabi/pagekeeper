"""Service for submitting books to Storyteller for narrated EPUB3 creation."""

import glob as glob_module
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import requests

logger = logging.getLogger(__name__)


class StorytellerDeferral(Exception):
    """Raised when a job should be deferred (not retried) while waiting for Storyteller processing.

    Unlike regular exceptions, this should NOT increment the retry counter —
    the book isn't failing, it's just waiting for an external process.
    """


@dataclass
class SubmissionResult:
    success: bool
    status: str = ""
    submission_dir: str = ""
    error: str = ""
    files_copied: list[str] = field(default_factory=list)


class StorytellerSubmissionService:
    """Submits books to Storyteller by copying ebook + audio to the import directory.

    Storyteller requires both an ebook (EPUB) and audio file(s) to create a
    narrated EPUB3 with media overlays. The service copies both into a subdirectory
    of Storyteller's import folder, which Storyteller watches for new submissions.
    """

    def __init__(self, storyteller_client, abs_client, database_service, import_dir: str | None = None):
        self.storyteller_client = storyteller_client
        self.abs_client = abs_client
        self.database_service = database_service
        # Store the initial value but always prefer the live env var
        self._initial_import_dir = import_dir

    @property
    def import_dir(self) -> Path | None:
        """Resolve import dir from env (hot-reloadable) or initial config."""
        raw = os.environ.get("STORYTELLER_IMPORT_DIR", "").strip() or self._initial_import_dir
        return Path(raw) if raw else None

    def is_available(self) -> bool:
        """True if import_dir is configured, exists, and is writable."""
        if not self.import_dir:
            return False
        try:
            return self.import_dir.is_dir() and os.access(self.import_dir, os.W_OK)
        except OSError as e:
            logger.warning(f"Storyteller import dir check failed: {e}")
            return False

    def submit_book(self, abs_id: str, title: str, ebook_path: Path, audio_files: list[dict]) -> SubmissionResult:
        """Copy ebook + audio files to Storyteller's import directory.

        Storyteller needs both an EPUB and audio to produce a narrated EPUB3.

        Args:
            abs_id: The book's ABS ID.
            title: Book title (used for the import directory name).
            ebook_path: Path to the ebook file (epub). Required.
            audio_files: List of dicts with 'stream_url' and 'ext' keys from ABS. Required.
        """
        if not self.is_available():
            return SubmissionResult(success=False, error="Import directory not configured or not writable")

        if not ebook_path or not ebook_path.exists():
            return SubmissionResult(success=False, error="Ebook file not found")

        if not audio_files:
            return SubmissionResult(success=False, error="Audio files are required for Storyteller submission")

        dir_name = self._sanitize_dirname(title, abs_id)
        target_dir = self.import_dir / dir_name

        # Validate path stays within import root
        try:
            resolved = target_dir.resolve()
            if self.import_dir.resolve() not in resolved.parents and resolved != self.import_dir.resolve():
                return SubmissionResult(success=False, error="Invalid submission directory path")
        except OSError:
            return SubmissionResult(success=False, error="Could not resolve submission path")

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            files_copied = []

            # Copy ebook
            dest = target_dir / ebook_path.name
            shutil.copy2(ebook_path, dest)
            files_copied.append(ebook_path.name)
            logger.info(f"Storyteller submission: copied ebook '{ebook_path.name}' for '{title}'")

            # Download and copy audio files from ABS
            for i, af in enumerate(audio_files):
                stream_url = af.get("stream_url")
                ext = af.get("ext", "mp3")
                if not stream_url:
                    logger.warning(f"Storyteller submission: audio file {i} missing stream_url, skipping")
                    continue
                audio_filename = f"{dir_name}.{ext}" if len(audio_files) == 1 else f"{dir_name}_{i + 1:02d}.{ext}"
                audio_dest = target_dir / audio_filename
                final_path = self._download_file(stream_url, audio_dest)
                if final_path:
                    files_copied.append(final_path.name)

            # Must have at least the ebook + one audio file
            if len(files_copied) < 2:
                logger.error(f"Storyteller submission incomplete for '{title}': only {files_copied}")
                # Clean up partial submission
                shutil.rmtree(target_dir, ignore_errors=True)
                return SubmissionResult(success=False, error="Failed to download audio files")

            # Update existing reservation or create new submission record
            from src.db.models import StorytellerSubmission

            book = self.database_service.get_book_by_abs_id(abs_id)
            book_id = book.id if book else None
            existing = self.database_service.get_active_storyteller_submission_by_book_id(book_id) if book_id else None
            if existing:
                submission = existing
                self.database_service.update_storyteller_submission_status(
                    existing.id,
                    "queued",
                    submission_dir=dir_name,
                )
            else:
                submission = StorytellerSubmission(
                    abs_id=abs_id,
                    book_id=book_id,
                    status="queued",
                    submission_dir=dir_name,
                )
                self.database_service.save_storyteller_submission(submission)

            logger.info(f"Storyteller submission complete: '{title}' ({len(files_copied)} files) -> {dir_name}")

            # Try to trigger Storyteller processing automatically
            storyteller_uuid = self._trigger_processing_after_import(title, submission)

            return SubmissionResult(
                success=True,
                status="queued" if not storyteller_uuid else "processing",
                submission_dir=dir_name,
                files_copied=files_copied,
            )

        except Exception as e:
            logger.error(f"Storyteller submission failed for '{title}': {e}")
            return SubmissionResult(success=False, error=str(e))

    def check_status(self, abs_id: str) -> str:
        """Check if Storyteller has finished processing a submitted book.

        Returns: 'queued', 'processing', 'ready', 'failed', or 'not_found'.
        """
        book = self.database_service.get_book_by_abs_id(abs_id)
        submission = self.database_service.get_storyteller_submission_by_book_id(book.id) if book else None
        if not submission:
            return "not_found"

        # Already in a terminal state — return immediately
        if submission.status in ("ready", "failed"):
            return submission.status

        # Timeout: if submission has been non-terminal for too long, mark failed
        max_wait = int(os.environ.get("STORYTELLER_MAX_WAIT_HOURS", "12"))
        if submission.submitted_at:
            submitted_at = submission.submitted_at
            if submitted_at.tzinfo is None:
                submitted_at = submitted_at.replace(tzinfo=UTC)
            elapsed = (datetime.now(UTC) - submitted_at).total_seconds() / 3600
            if elapsed > max_wait:
                logger.warning(
                    f"Storyteller submission timed out after {elapsed:.1f}h for abs_id={abs_id} "
                    f"(max {max_wait}h) — marking failed"
                )
                self._update_submission_status(submission, "failed")
                return "failed"

        # Fix 1: Propagate book's UUID to submission if missing
        if not submission.storyteller_uuid:
            book = self.database_service.get_book_by_abs_id(abs_id)
            if book and book.storyteller_uuid:
                submission.storyteller_uuid = book.storyteller_uuid
                self._update_submission_status(submission, submission.status)
                logger.info(
                    f"Storyteller: propagated UUID {book.storyteller_uuid[:8]}... "
                    f"from book to submission for abs_id={abs_id}"
                )

        # Look up book title once for use in filesystem fallbacks
        book_title = None
        book = self.database_service.get_book_by_abs_id(abs_id)
        if book and book.title:
            book_title = book.title

        # Check if Storyteller has produced transcription output
        assets_dir = os.environ.get("STORYTELLER_ASSETS_DIR", "").strip()
        checks_attempted = []

        # Check 1: UUID-based transcription check (independent of submission_dir)
        if assets_dir and submission.storyteller_uuid:
            checks_attempted.append("uuid")
            try:
                if self._check_transcriptions_by_uuid(submission.storyteller_uuid, title_hint=book_title):
                    logger.info(f"Storyteller UUID check found transcriptions for abs_id={abs_id}")
                    self._update_submission_status(submission, "ready")
                    return "ready"
                else:
                    logger.debug(
                        f"Storyteller UUID check: no transcriptions yet for uuid={submission.storyteller_uuid[:8]}..."
                    )
            except Exception as e:
                logger.warning(f"Storyteller UUID transcription check failed for abs_id={abs_id}: {e}")

        # Check 2: Directory-based fallback (needs submission_dir)
        if assets_dir and submission.submission_dir:
            checks_attempted.append("directory")
            try:
                assets_root = Path(assets_dir) / "assets"
                transcripts_dir = assets_root / submission.submission_dir / "transcriptions"
                if assets_root.resolve() not in transcripts_dir.resolve().parents:
                    logger.warning("Storyteller: refusing out-of-root transcript path in status check")
                    return submission.status
                if transcripts_dir.is_dir() and any(transcripts_dir.iterdir()):
                    logger.info(f"Storyteller directory check found transcriptions for abs_id={abs_id}")
                    self._update_submission_status(submission, "ready")
                    return "ready"
                else:
                    # Fuzzy match: Storyteller may have added a deduplication suffix
                    escaped = glob_module.escape(submission.submission_dir)
                    candidates = [
                        p
                        for p in assets_root.glob(f"{escaped}*/transcriptions")
                        if p.is_dir() and assets_root.resolve() in p.resolve().parents
                    ]
                    for candidate in candidates:
                        if any(candidate.iterdir()):
                            logger.info(
                                f"Storyteller fuzzy directory match found transcriptions at "
                                f"'{candidate.parent.name}' for abs_id={abs_id}"
                            )
                            self._update_submission_status(submission, "ready")
                            return "ready"

                    logger.debug(
                        f"Storyteller directory check: no transcriptions at {transcripts_dir} "
                        f"(exists={transcripts_dir.is_dir()}, fuzzy_candidates={len(candidates)})"
                    )
            except OSError as e:
                logger.warning(f"Storyteller assets directory check failed for abs_id={abs_id}: {e}")

        # Check 3: Try to discover the storyteller_uuid via API title search
        if not submission.storyteller_uuid and self.storyteller_client and self.storyteller_client.is_configured():
            checks_attempted.append("api_search")
            book = self.database_service.get_book_by_abs_id(abs_id)
            if book and book.title:
                try:
                    results = self.storyteller_client.search_books(book.title)
                    # Only accept a single exact title match to avoid misidentification
                    exact = [r for r in results if r.get("title", "").strip().lower() == book.title.strip().lower()]
                    if len(exact) == 1:
                        storyteller_uuid = exact[0].get("uuid")
                        submission.storyteller_uuid = storyteller_uuid
                        self._update_submission_status(submission, "processing")

                        # If we just discovered the UUID, try triggering processing
                        # in case Storyteller has the book but never started alignment
                        if storyteller_uuid:
                            self.storyteller_client.trigger_processing(storyteller_uuid)
                            logger.info(
                                f"Storyteller: discovered UUID {storyteller_uuid[:8]}... for abs_id={abs_id}, triggered processing"
                            )

                        return "processing"
                    else:
                        logger.debug(
                            f"Storyteller API search: {len(results)} results, {len(exact)} exact matches "
                            f"for '{book.title}' (need exactly 1)"
                        )
                except Exception as e:
                    logger.warning(f"Storyteller book search failed for abs_id={abs_id}: {e}")

        logger.debug(
            f"Storyteller check_status fallthrough to 'processing' for abs_id={abs_id} "
            f"(checks attempted: {checks_attempted or 'none'}, "
            f"has_uuid={bool(submission.storyteller_uuid)}, "
            f"has_dir={bool(submission.submission_dir)}, "
            f"has_assets_dir={bool(assets_dir)})"
        )
        self._update_submission_status(submission, "processing")
        return "processing"

    def _update_submission_status(self, submission, new_status: str):
        """Update a submission's status without creating a new record."""
        self.database_service.update_storyteller_submission_status(
            submission.id,
            new_status,
            datetime.now(UTC),
            storyteller_uuid=submission.storyteller_uuid,
        )

    def get_submission(self, abs_id: str):
        """Get the most recent submission for a book, if any."""
        book = self.database_service.get_book_by_abs_id(abs_id)
        if book:
            return self.database_service.get_storyteller_submission_by_book_id(book.id)
        return None

    def _trigger_processing_after_import(self, title: str, submission) -> str | None:
        """Wait for Storyteller to detect imported files, then trigger processing.

        Storyteller watches its import directory and creates DB records when files
        appear, but doesn't start alignment automatically. We need to:
        1. Snapshot existing books so we only match NEW ones
        2. Wait for Storyteller to detect the files (poll by title search)
        3. Call POST /api/v2/books/{uuid}/process to start alignment

        Returns the storyteller_uuid if processing was triggered, None otherwise.
        """
        if not self.storyteller_client or not self.storyteller_client.is_configured():
            logger.info("Storyteller API not configured — cannot auto-trigger processing")
            return None

        # Snapshot existing book UUIDs so we only consider newly imported books
        existing_uuids = set()
        try:
            existing_results = self.storyteller_client.search_books(title)
            existing_uuids = {r.get("uuid") for r in existing_results if r.get("uuid")}
        except Exception:
            pass

        # Poll for the book to appear in Storyteller.
        # Storyteller's import watcher may take a while to detect new files,
        # especially after large audio downloads.
        timeout_secs = int(os.environ.get("STORYTELLER_IMPORT_DETECT_TIMEOUT", "120"))
        poll_interval = 10
        max_attempts = max(timeout_secs // poll_interval, 1)
        storyteller_uuid = None
        for attempt in range(max_attempts):
            time.sleep(poll_interval)
            try:
                results = self.storyteller_client.search_books(title)
                for result in results:
                    uuid = result.get("uuid")
                    result_title = result.get("title", "")
                    # Skip books that existed before the import
                    if uuid in existing_uuids:
                        continue
                    # Require exact title match for newly appeared books
                    if result_title.strip().lower() == title.strip().lower():
                        storyteller_uuid = uuid
                        break
                if storyteller_uuid:
                    break
            except Exception as e:
                logger.debug(f"Storyteller search attempt {attempt + 1} failed: {e}")

        if not storyteller_uuid:
            logger.warning(
                f"Storyteller did not detect '{title}' within {timeout_secs}s — will retry on next status check"
            )
            return None

        # Trigger processing
        triggered = self.storyteller_client.trigger_processing(storyteller_uuid)
        if triggered:
            self._update_submission_status(submission, "processing")
            # Update the submission with the discovered UUID
            self.database_service.update_storyteller_submission_status(
                submission.id,
                "processing",
                datetime.now(UTC),
                storyteller_uuid=storyteller_uuid,
            )
            logger.info(f"Storyteller processing triggered for '{title}' (uuid: {storyteller_uuid[:8]}...)")
            return storyteller_uuid
        else:
            logger.warning(f"Failed to trigger Storyteller processing for '{title}'")
            return None

    def _check_transcriptions_by_uuid(self, book_uuid: str, title_hint: str = None) -> bool:
        """Check if Storyteller has transcription data for a book by UUID."""
        if not self.storyteller_client:
            return False
        chapters = self.storyteller_client.get_word_timeline_chapters(book_uuid, title_hint=title_hint)
        return chapters is not None and len(chapters) > 0

    def _sanitize_dirname(self, title: str, abs_id: str) -> str:
        """Create a safe, deterministic directory name from a book title."""
        clean = re.sub(r'[<>:"/\\|?*]', "", title)
        clean = clean.strip(". ")
        if not clean:
            clean = abs_id
        if len(clean) > 200:
            clean = clean[:200]
        return clean

    def _download_file(self, url: str, dest: Path) -> Path | None:
        """Download a file from a URL to a local path.

        After download, checks if the file's actual format matches the extension.
        ABS sometimes reports .mp3 for files that are actually M4A/M4B (AAC in MP4
        container). Storyteller's ffmpeg fails on the mismatch, so we fix it here.

        Returns the final Path (possibly renamed) on success, None on failure.
        """
        try:
            with requests.get(url, stream=True, timeout=300) as r:
                r.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)

            # Detect actual format from file magic bytes
            corrected = self._fix_audio_extension(dest)
            logger.debug(f"Downloaded: {corrected.name}")
            return corrected
        except Exception as e:
            logger.error(f"Failed to download {dest.name}: {e}")
            return None

    @staticmethod
    def _fix_audio_extension(filepath: Path) -> Path:
        """Rename audio file if its extension doesn't match its actual container format."""
        try:
            with open(filepath, "rb") as f:
                header = f.read(12)
        except OSError:
            return filepath

        # MP4/M4A/M4B: bytes 4-8 are 'ftyp'
        if len(header) >= 8 and header[4:8] == b"ftyp":
            current_ext = filepath.suffix.lower()
            if current_ext in (".mp3", ".ogg", ".flac", ".wav"):
                new_path = filepath.with_suffix(".m4b")
                filepath.rename(new_path)
                logger.info(f"Storyteller: renamed {filepath.name} -> {new_path.name} (actual format is MP4/M4A)")
                return new_path

        return filepath
