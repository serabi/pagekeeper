"""Service for submitting books to Storyteller for narrated EPUB3 creation."""

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
                if self._download_file(stream_url, audio_dest):
                    files_copied.append(audio_filename)

            # Must have at least the ebook + one audio file
            if len(files_copied) < 2:
                logger.error(f"Storyteller submission incomplete for '{title}': only {files_copied}")
                # Clean up partial submission
                shutil.rmtree(target_dir, ignore_errors=True)
                return SubmissionResult(success=False, error="Failed to download audio files")

            # Persist submission record
            from src.db.models import StorytellerSubmission

            submission = StorytellerSubmission(
                abs_id=abs_id,
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
        submission = self.database_service.get_storyteller_submission(abs_id)
        if not submission:
            return "not_found"

        # Already in a terminal state — return immediately
        if submission.status in ("ready", "failed"):
            return submission.status

        # Check if Storyteller has produced transcription output
        assets_dir = os.environ.get("STORYTELLER_ASSETS_DIR", "").strip()
        if assets_dir and submission.submission_dir:
            # Try resolving via the stored storyteller_uuid first
            if submission.storyteller_uuid:
                try:
                    if self._check_transcriptions_by_uuid(submission.storyteller_uuid):
                        self._update_submission_status(submission, "ready")
                        return "ready"
                except Exception as e:
                    logger.warning(f"Storyteller UUID transcription check failed: {e}")

            # Fallback: check by directory name in assets
            try:
                assets_root = Path(assets_dir) / "assets"
                transcripts_dir = assets_root / submission.submission_dir / "transcriptions"
                if transcripts_dir.is_dir() and any(transcripts_dir.iterdir()):
                    self._update_submission_status(submission, "ready")
                    return "ready"
            except OSError as e:
                logger.warning(f"Storyteller assets directory check failed: {e}")

        # Try to discover the storyteller_uuid via API title search
        if not submission.storyteller_uuid and self.storyteller_client and self.storyteller_client.is_configured():
            book = self.database_service.get_book(abs_id)
            if book and book.abs_title:
                try:
                    results = self.storyteller_client.search_books(book.abs_title)
                    if len(results) == 1:
                        storyteller_uuid = results[0].get("uuid")
                        submission.storyteller_uuid = storyteller_uuid
                        self._update_submission_status(submission, "processing")

                        # If we just discovered the UUID, try triggering processing
                        # in case Storyteller has the book but never started alignment
                        if storyteller_uuid:
                            self.storyteller_client.trigger_processing(storyteller_uuid)

                        return "processing"
                except Exception as e:
                    logger.warning(f"Storyteller book search failed: {e}")

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
        return self.database_service.get_storyteller_submission(abs_id)

    def _trigger_processing_after_import(self, title: str, submission) -> str | None:
        """Wait for Storyteller to detect imported files, then trigger processing.

        Storyteller watches its import directory and creates DB records when files
        appear, but doesn't start alignment automatically. We need to:
        1. Wait for Storyteller to detect the files (poll by title search)
        2. Call POST /api/v2/books/{uuid}/process to start alignment

        Returns the storyteller_uuid if processing was triggered, None otherwise.
        """
        if not self.storyteller_client or not self.storyteller_client.is_configured():
            logger.info("Storyteller API not configured — cannot auto-trigger processing")
            return None

        # Poll for the book to appear in Storyteller (up to ~30 seconds)
        storyteller_uuid = None
        for attempt in range(6):
            time.sleep(5)
            try:
                results = self.storyteller_client.search_books(title)
                if results:
                    storyteller_uuid = results[0].get("uuid")
                    if storyteller_uuid:
                        break
            except Exception as e:
                logger.debug(f"Storyteller search attempt {attempt + 1} failed: {e}")

        if not storyteller_uuid:
            logger.warning(f"Storyteller did not detect '{title}' within 30s — processing must be triggered manually")
            return None

        # Trigger processing
        triggered = self.storyteller_client.trigger_processing(storyteller_uuid)
        if triggered:
            self._update_submission_status(submission, "processing")
            # Update the submission with the discovered UUID
            self.database_service.update_storyteller_submission_status(
                submission.id, "processing", datetime.now(UTC),
                storyteller_uuid=storyteller_uuid,
            )
            logger.info(f"Storyteller processing triggered for '{title}' (uuid: {storyteller_uuid[:8]}...)")
            return storyteller_uuid
        else:
            logger.warning(f"Failed to trigger Storyteller processing for '{title}'")
            return None

    def _check_transcriptions_by_uuid(self, book_uuid: str) -> bool:
        """Check if Storyteller has transcription data for a book by UUID."""
        if not self.storyteller_client:
            return False
        chapters = self.storyteller_client.get_word_timeline_chapters(book_uuid)
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

    def _download_file(self, url: str, dest: Path) -> bool:
        """Download a file from a URL to a local path."""
        try:
            with requests.get(url, stream=True, timeout=300) as r:
                r.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            logger.debug(f"Downloaded: {dest.name}")
            return True
        except Exception as e:
            logger.error(f"Failed to download {dest.name}: {e}")
            return False
