"""Tests for StorytellerSubmissionService."""

import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.storyteller_submission_service import StorytellerDeferral, StorytellerSubmissionService


class TestStorytellerSubmission(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.import_dir = Path(self.temp_dir) / "import"
        self.import_dir.mkdir()

        self.mock_storyteller = Mock()
        self.mock_storyteller.is_configured.return_value = False  # avoid 120s polling loop
        self.mock_abs = Mock()
        self.mock_db = Mock()

        # Default: no existing reservation (submit_book creates a new record)
        self.mock_db.get_active_storyteller_submission_by_book_id.return_value = None

        # Service resolves book by abs_id before submission lookups
        mock_book = Mock()
        mock_book.id = 1
        self.mock_db.get_book_by_abs_id.return_value = mock_book

        self.service = StorytellerSubmissionService(
            storyteller_client=self.mock_storyteller,
            abs_client=self.mock_abs,
            database_service=self.mock_db,
            import_dir=str(self.import_dir),
        )

        # Create a fake ebook file
        self.ebook_path = Path(self.temp_dir) / "test-book.epub"
        self.ebook_path.write_text("fake epub content")

        self.audio_files = [
            {"stream_url": "http://abs/audio/ch1.mp3", "ext": "mp3"},
        ]

    def tearDown(self):
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # ── is_available ──

    def test_is_available_when_dir_exists_and_writable(self):
        assert self.service.is_available() is True

    def test_is_available_false_when_no_import_dir(self):
        service = StorytellerSubmissionService(
            storyteller_client=self.mock_storyteller,
            abs_client=self.mock_abs,
            database_service=self.mock_db,
            import_dir=None,
        )
        assert service.is_available() is False

    def test_is_available_false_when_dir_missing(self):
        service = StorytellerSubmissionService(
            storyteller_client=self.mock_storyteller,
            abs_client=self.mock_abs,
            database_service=self.mock_db,
            import_dir="/nonexistent/path",
        )
        assert service.is_available() is False

    # ── submit_book: success ──

    @patch.object(StorytellerSubmissionService, "_download_file", side_effect=lambda url, dest: dest)
    def test_submit_copies_files_to_import_dir(self, mock_download):
        result = self.service.submit_book(
            abs_id="book-123",
            title="Test Book",
            ebook_path=self.ebook_path,
            audio_files=self.audio_files,
        )
        assert result.success is True
        assert result.status == "queued"
        assert "test-book.epub" in result.files_copied
        assert len(result.files_copied) == 2  # ebook + 1 audio

        # Ebook should be copied
        target_dir = self.import_dir / "Test Book"
        assert (target_dir / "test-book.epub").exists()

        # Audio download should have been called
        mock_download.assert_called_once()

    @patch.object(StorytellerSubmissionService, "_download_file", side_effect=lambda url, dest: dest)
    def test_submit_creates_correct_directory_structure(self, mock_download):
        result = self.service.submit_book(
            abs_id="book-123",
            title="My Great Book",
            ebook_path=self.ebook_path,
            audio_files=self.audio_files,
        )
        assert result.success is True
        assert result.submission_dir == "My Great Book"
        assert (self.import_dir / "My Great Book").is_dir()

    @patch.object(StorytellerSubmissionService, "_download_file", side_effect=lambda url, dest: dest)
    def test_submit_persists_submission_record_only_on_success(self, mock_download):
        # No existing reservation — should create a new submission record
        self.mock_db.get_active_storyteller_submission_by_book_id.return_value = None
        result = self.service.submit_book(
            abs_id="book-123",
            title="Test Book",
            ebook_path=self.ebook_path,
            audio_files=self.audio_files,
        )
        assert result.success is True
        self.mock_db.save_storyteller_submission.assert_called_once()
        submission_arg = self.mock_db.save_storyteller_submission.call_args[0][0]
        assert submission_arg.abs_id == "book-123"
        assert submission_arg.status == "queued"

    @patch.object(StorytellerSubmissionService, "_download_file", side_effect=lambda url, dest: dest)
    def test_submit_multi_audio_names_files_sequentially(self, mock_download):
        audio_files = [
            {"stream_url": "http://abs/audio/ch1.mp3", "ext": "mp3"},
            {"stream_url": "http://abs/audio/ch2.mp3", "ext": "mp3"},
        ]
        result = self.service.submit_book(
            abs_id="book-123",
            title="Test Book",
            ebook_path=self.ebook_path,
            audio_files=audio_files,
        )
        assert result.success is True
        assert len(result.files_copied) == 3  # ebook + 2 audio
        # Multi-audio uses sequential naming
        assert "Test Book_01.mp3" in result.files_copied
        assert "Test Book_02.mp3" in result.files_copied

    # ── submit_book: failure cases ──

    def test_submit_fails_gracefully_when_no_import_dir(self):
        service = StorytellerSubmissionService(
            storyteller_client=self.mock_storyteller,
            abs_client=self.mock_abs,
            database_service=self.mock_db,
            import_dir=None,
        )
        result = service.submit_book(
            abs_id="book-123",
            title="Test Book",
            ebook_path=self.ebook_path,
            audio_files=self.audio_files,
        )
        assert result.success is False
        assert "not configured" in result.error
        self.mock_db.save_storyteller_submission.assert_not_called()

    def test_submit_fails_when_ebook_missing(self):
        result = self.service.submit_book(
            abs_id="book-123",
            title="Test Book",
            ebook_path=Path("/nonexistent/book.epub"),
            audio_files=self.audio_files,
        )
        assert result.success is False
        assert "not found" in result.error.lower()
        self.mock_db.save_storyteller_submission.assert_not_called()

    def test_submit_fails_when_no_audio_files(self):
        result = self.service.submit_book(
            abs_id="book-123",
            title="Test Book",
            ebook_path=self.ebook_path,
            audio_files=[],
        )
        assert result.success is False
        assert "Audio files are required" in result.error
        self.mock_db.save_storyteller_submission.assert_not_called()

    @patch.object(StorytellerSubmissionService, "_download_file", return_value=False)
    def test_submit_cleans_up_on_audio_download_failure(self, mock_download):
        result = self.service.submit_book(
            abs_id="book-123",
            title="Failed Download Book",
            ebook_path=self.ebook_path,
            audio_files=self.audio_files,
        )
        assert result.success is False
        assert "Failed to download" in result.error
        # Partial directory should be cleaned up
        assert not (self.import_dir / "Failed Download Book").exists()
        self.mock_db.save_storyteller_submission.assert_not_called()

    # ── check_status ──

    def test_check_status_returns_not_found_when_no_submission(self):
        self.mock_db.get_storyteller_submission_by_book_id.return_value = None
        assert self.service.check_status("book-123") == "not_found"

    def test_check_status_returns_ready_when_already_ready(self):
        submission = Mock()
        submission.status = "ready"
        self.mock_db.get_storyteller_submission_by_book_id.return_value = submission
        assert self.service.check_status("book-123") == "ready"

    def test_check_status_returns_failed_when_already_failed(self):
        submission = Mock()
        submission.status = "failed"
        self.mock_db.get_storyteller_submission_by_book_id.return_value = submission
        assert self.service.check_status("book-123") == "failed"

    @patch.dict(os.environ, {"STORYTELLER_ASSETS_DIR": ""})
    def test_check_status_returns_processing_when_no_transcriptions(self):
        submission = Mock()
        submission.status = "queued"
        submission.submission_dir = "Test Book"
        submission.storyteller_uuid = None
        submission.submitted_at = datetime.utcnow()
        self.mock_db.get_storyteller_submission_by_book_id.return_value = submission
        self.mock_storyteller.is_configured.return_value = False

        book = Mock()
        book.storyteller_uuid = None
        book.title = "Test Book"
        self.mock_db.get_book_by_abs_id.return_value = book

        assert self.service.check_status("book-123") == "processing"
        self.mock_db.update_storyteller_submission_status.assert_called()

    def test_check_status_returns_ready_when_transcriptions_exist(self):
        submission = Mock()
        submission.status = "queued"
        submission.submission_dir = "Test Book"
        submission.storyteller_uuid = None
        submission.submitted_at = datetime.utcnow()
        self.mock_db.get_storyteller_submission_by_book_id.return_value = submission

        book = Mock()
        book.storyteller_uuid = None
        book.title = "Test Book"
        self.mock_db.get_book_by_abs_id.return_value = book

        # Create fake transcription directory
        with tempfile.TemporaryDirectory() as assets_dir:
            transcripts = Path(assets_dir) / "assets" / "Test Book" / "transcriptions"
            transcripts.mkdir(parents=True)
            (transcripts / "00001-00001.json").write_text("{}")

            with patch.dict(os.environ, {"STORYTELLER_ASSETS_DIR": assets_dir}):
                result = self.service.check_status("book-123")

        assert result == "ready"
        self.mock_db.update_storyteller_submission_status.assert_called()
        call_args = self.mock_db.update_storyteller_submission_status.call_args
        assert call_args[0][1] == "ready"

    def test_check_status_returns_ready_via_uuid(self):
        submission = Mock()
        submission.status = "queued"
        submission.submission_dir = "Test Book"
        submission.storyteller_uuid = "st-uuid-123"
        submission.submitted_at = datetime.utcnow()
        self.mock_db.get_storyteller_submission_by_book_id.return_value = submission

        self.mock_storyteller.get_word_timeline_chapters.return_value = [{"words": []}]

        with tempfile.TemporaryDirectory() as assets_dir:
            with patch.dict(os.environ, {"STORYTELLER_ASSETS_DIR": assets_dir}):
                result = self.service.check_status("book-123")

        assert result == "ready"

    def test_check_status_times_out_after_max_wait(self):
        from datetime import timedelta

        submission = Mock()
        submission.status = "processing"
        submission.submission_dir = "Test Book"
        submission.storyteller_uuid = "st-uuid-123"
        submission.submitted_at = datetime.utcnow() - timedelta(hours=13)
        self.mock_db.get_storyteller_submission_by_book_id.return_value = submission

        with patch.dict(os.environ, {"STORYTELLER_MAX_WAIT_HOURS": "12"}):
            result = self.service.check_status("book-123")

        assert result == "failed"
        call_args = self.mock_db.update_storyteller_submission_status.call_args
        assert call_args[0][1] == "failed"

    def test_check_status_uuid_check_works_without_submission_dir(self):
        """UUID-based check should work even when submission_dir is None (Bug 1 fix)."""
        submission = Mock()
        submission.status = "queued"
        submission.submission_dir = None
        submission.storyteller_uuid = "st-uuid-456"
        submission.submitted_at = datetime.utcnow()
        self.mock_db.get_storyteller_submission_by_book_id.return_value = submission

        self.mock_storyteller.get_word_timeline_chapters.return_value = [{"words": []}]

        with tempfile.TemporaryDirectory() as assets_dir:
            with patch.dict(os.environ, {"STORYTELLER_ASSETS_DIR": assets_dir}):
                result = self.service.check_status("book-123")

        assert result == "ready"

    # ── Fix 1: UUID propagation from book to submission ──

    @patch.dict(os.environ, {"STORYTELLER_ASSETS_DIR": ""})
    def test_check_status_propagates_uuid_from_book(self):
        """If submission has no UUID but the book does, propagate it."""
        submission = Mock()
        submission.status = "processing"
        submission.submission_dir = "Test Book"
        submission.storyteller_uuid = None
        submission.submitted_at = datetime.utcnow()
        self.mock_db.get_storyteller_submission_by_book_id.return_value = submission

        book = Mock()
        book.storyteller_uuid = "book-uuid-abc"
        book.title = "Test Book"
        self.mock_db.get_book_by_abs_id.return_value = book
        self.mock_storyteller.is_configured.return_value = False

        self.service.check_status("book-123")

        # UUID should have been propagated
        assert submission.storyteller_uuid == "book-uuid-abc"

    @patch.dict(os.environ, {"STORYTELLER_ASSETS_DIR": ""})
    def test_check_status_no_propagation_when_book_has_no_uuid(self):
        """If neither submission nor book has UUID, don't crash."""
        submission = Mock()
        submission.status = "processing"
        submission.submission_dir = "Test Book"
        submission.storyteller_uuid = None
        submission.submitted_at = datetime.utcnow()
        self.mock_db.get_storyteller_submission_by_book_id.return_value = submission

        book = Mock()
        book.storyteller_uuid = None
        book.title = "Test Book"
        self.mock_db.get_book_by_abs_id.return_value = book
        self.mock_storyteller.is_configured.return_value = False

        result = self.service.check_status("book-123")
        assert result == "processing"
        assert submission.storyteller_uuid is None

    # ── Fix 3: Fuzzy directory matching ──

    def test_check_status_fuzzy_dir_match_with_suffix(self):
        """Directory with deduplication suffix should still be detected."""
        submission = Mock()
        submission.status = "processing"
        submission.submission_dir = "Bury Our Bones in the Midnight Soil"
        submission.storyteller_uuid = None
        submission.submitted_at = datetime.utcnow()
        self.mock_db.get_storyteller_submission_by_book_id.return_value = submission

        book = Mock()
        book.storyteller_uuid = None
        book.title = "Bury Our Bones in the Midnight Soil"
        self.mock_db.get_book_by_abs_id.return_value = book
        self.mock_storyteller.is_configured.return_value = False

        with tempfile.TemporaryDirectory() as assets_dir:
            # Simulate Storyteller's suffixed directory
            suffixed_dir = Path(assets_dir) / "assets" / "Bury Our Bones in the Midnight Soil [Ru93Xoc2]" / "transcriptions"
            suffixed_dir.mkdir(parents=True)
            (suffixed_dir / "00001-00001.json").write_text("{}")

            with patch.dict(os.environ, {"STORYTELLER_ASSETS_DIR": assets_dir}):
                result = self.service.check_status("book-123")

        assert result == "ready"

    def test_check_status_fuzzy_dir_no_false_positive(self):
        """Fuzzy match should not match if suffixed dir has no transcription files."""
        submission = Mock()
        submission.status = "processing"
        submission.submission_dir = "Test Book"
        submission.storyteller_uuid = None
        submission.submitted_at = datetime.utcnow()
        self.mock_db.get_storyteller_submission_by_book_id.return_value = submission

        book = Mock()
        book.storyteller_uuid = None
        book.title = "Test Book"
        self.mock_db.get_book_by_abs_id.return_value = book
        self.mock_storyteller.is_configured.return_value = False

        with tempfile.TemporaryDirectory() as assets_dir:
            # Empty transcriptions dir (processing not complete)
            suffixed_dir = Path(assets_dir) / "assets" / "Test Book [abc123]" / "transcriptions"
            suffixed_dir.mkdir(parents=True)

            with patch.dict(os.environ, {"STORYTELLER_ASSETS_DIR": assets_dir}):
                result = self.service.check_status("book-123")

        assert result == "processing"

    def test_check_status_fuzzy_dir_glob_special_chars(self):
        """Titles with glob-special characters should be escaped properly."""
        submission = Mock()
        submission.status = "processing"
        submission.submission_dir = "What If [Revised]"
        submission.storyteller_uuid = None
        submission.submitted_at = datetime.utcnow()
        self.mock_db.get_storyteller_submission_by_book_id.return_value = submission

        book = Mock()
        book.storyteller_uuid = None
        book.title = "What If [Revised]"
        self.mock_db.get_book_by_abs_id.return_value = book
        self.mock_storyteller.is_configured.return_value = False

        with tempfile.TemporaryDirectory() as assets_dir:
            # Exact match with the bracket title + suffix
            suffixed_dir = Path(assets_dir) / "assets" / "What If [Revised] [xY12z]" / "transcriptions"
            suffixed_dir.mkdir(parents=True)
            (suffixed_dir / "00001-00001.json").write_text("{}")

            with patch.dict(os.environ, {"STORYTELLER_ASSETS_DIR": assets_dir}):
                result = self.service.check_status("book-123")

        assert result == "ready"

    # ── _sanitize_dirname ──

    def test_sanitize_dirname_removes_unsafe_chars(self):
        result = self.service._sanitize_dirname('Book: "The <Best> One?"', "fallback-id")
        assert ":" not in result
        assert '"' not in result
        assert "<" not in result
        assert ">" not in result
        assert "?" not in result

    def test_sanitize_dirname_uses_abs_id_when_title_empty(self):
        result = self.service._sanitize_dirname("", "book-abc")
        assert result == "book-abc"

    def test_sanitize_dirname_truncates_long_titles(self):
        long_title = "A" * 300
        result = self.service._sanitize_dirname(long_title, "fallback")
        assert len(result) <= 200

    # ── StorytellerDeferral exception ──

    def test_storyteller_deferral_is_exception(self):
        """StorytellerDeferral must be an Exception subclass for the handler to catch it."""
        err = StorytellerDeferral("waiting for processing")
        assert isinstance(err, Exception)
        assert str(err) == "waiting for processing"

    def test_storyteller_deferral_not_caught_by_non_exception_handlers(self):
        """StorytellerDeferral should be distinguishable from generic Exception."""
        with self.assertRaises(StorytellerDeferral):
            raise StorytellerDeferral("test deferral")

    # ── check_status with terminal states ──

    def test_check_status_returns_ready_without_extra_queries(self):
        """When submission is already ready, no filesystem/API checks should run."""
        submission = Mock()
        submission.status = "ready"
        self.mock_db.get_storyteller_submission_by_book_id.return_value = submission
        assert self.service.check_status("book-123") == "ready"
        # Should NOT have called update_storyteller_submission_status
        self.mock_db.update_storyteller_submission_status.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
