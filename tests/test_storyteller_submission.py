"""Tests for StorytellerSubmissionService."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.services.storyteller_submission_service import StorytellerSubmissionService


class TestStorytellerSubmission(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.import_dir = Path(self.temp_dir) / 'import'
        self.import_dir.mkdir()

        self.mock_storyteller = Mock()
        self.mock_abs = Mock()
        self.mock_db = Mock()

        self.service = StorytellerSubmissionService(
            storyteller_client=self.mock_storyteller,
            abs_client=self.mock_abs,
            database_service=self.mock_db,
            import_dir=str(self.import_dir),
        )

        # Create a fake ebook file
        self.ebook_path = Path(self.temp_dir) / 'test-book.epub'
        self.ebook_path.write_text('fake epub content')

        self.audio_files = [
            {'stream_url': 'http://abs/audio/ch1.mp3', 'ext': 'mp3'},
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
            import_dir='/nonexistent/path',
        )
        assert service.is_available() is False

    # ── submit_book: success ──

    @patch.object(StorytellerSubmissionService, '_download_file', return_value=True)
    def test_submit_copies_files_to_import_dir(self, mock_download):
        result = self.service.submit_book(
            abs_id='book-123',
            title='Test Book',
            ebook_path=self.ebook_path,
            audio_files=self.audio_files,
        )
        assert result.success is True
        assert result.status == 'queued'
        assert 'test-book.epub' in result.files_copied
        assert len(result.files_copied) == 2  # ebook + 1 audio

        # Ebook should be copied
        target_dir = self.import_dir / 'Test Book'
        assert (target_dir / 'test-book.epub').exists()

        # Audio download should have been called
        mock_download.assert_called_once()

    @patch.object(StorytellerSubmissionService, '_download_file', return_value=True)
    def test_submit_creates_correct_directory_structure(self, mock_download):
        result = self.service.submit_book(
            abs_id='book-123',
            title='My Great Book',
            ebook_path=self.ebook_path,
            audio_files=self.audio_files,
        )
        assert result.success is True
        assert result.submission_dir == 'My Great Book'
        assert (self.import_dir / 'My Great Book').is_dir()

    @patch.object(StorytellerSubmissionService, '_download_file', return_value=True)
    def test_submit_persists_submission_record_only_on_success(self, mock_download):
        result = self.service.submit_book(
            abs_id='book-123',
            title='Test Book',
            ebook_path=self.ebook_path,
            audio_files=self.audio_files,
        )
        assert result.success is True
        self.mock_db.save_storyteller_submission.assert_called_once()
        submission_arg = self.mock_db.save_storyteller_submission.call_args[0][0]
        assert submission_arg.abs_id == 'book-123'
        assert submission_arg.status == 'queued'

    @patch.object(StorytellerSubmissionService, '_download_file', return_value=True)
    def test_submit_multi_audio_names_files_sequentially(self, mock_download):
        audio_files = [
            {'stream_url': 'http://abs/audio/ch1.mp3', 'ext': 'mp3'},
            {'stream_url': 'http://abs/audio/ch2.mp3', 'ext': 'mp3'},
        ]
        result = self.service.submit_book(
            abs_id='book-123',
            title='Test Book',
            ebook_path=self.ebook_path,
            audio_files=audio_files,
        )
        assert result.success is True
        assert len(result.files_copied) == 3  # ebook + 2 audio
        # Multi-audio uses sequential naming
        assert 'Test Book_01.mp3' in result.files_copied
        assert 'Test Book_02.mp3' in result.files_copied

    # ── submit_book: failure cases ──

    def test_submit_fails_gracefully_when_no_import_dir(self):
        service = StorytellerSubmissionService(
            storyteller_client=self.mock_storyteller,
            abs_client=self.mock_abs,
            database_service=self.mock_db,
            import_dir=None,
        )
        result = service.submit_book(
            abs_id='book-123',
            title='Test Book',
            ebook_path=self.ebook_path,
            audio_files=self.audio_files,
        )
        assert result.success is False
        assert 'not configured' in result.error
        self.mock_db.save_storyteller_submission.assert_not_called()

    def test_submit_fails_when_ebook_missing(self):
        result = self.service.submit_book(
            abs_id='book-123',
            title='Test Book',
            ebook_path=Path('/nonexistent/book.epub'),
            audio_files=self.audio_files,
        )
        assert result.success is False
        assert 'not found' in result.error.lower()
        self.mock_db.save_storyteller_submission.assert_not_called()

    def test_submit_fails_when_no_audio_files(self):
        result = self.service.submit_book(
            abs_id='book-123',
            title='Test Book',
            ebook_path=self.ebook_path,
            audio_files=[],
        )
        assert result.success is False
        assert 'Audio files are required' in result.error
        self.mock_db.save_storyteller_submission.assert_not_called()

    @patch.object(StorytellerSubmissionService, '_download_file', return_value=False)
    def test_submit_cleans_up_on_audio_download_failure(self, mock_download):
        result = self.service.submit_book(
            abs_id='book-123',
            title='Failed Download Book',
            ebook_path=self.ebook_path,
            audio_files=self.audio_files,
        )
        assert result.success is False
        assert 'Failed to download' in result.error
        # Partial directory should be cleaned up
        assert not (self.import_dir / 'Failed Download Book').exists()
        self.mock_db.save_storyteller_submission.assert_not_called()

    # ── check_status ──

    def test_check_status_returns_not_found_when_no_submission(self):
        self.mock_db.get_active_storyteller_submission.return_value = None
        assert self.service.check_status('book-123') == 'not_found'

    def test_check_status_returns_ready_when_already_ready(self):
        submission = Mock()
        submission.status = 'ready'
        self.mock_db.get_active_storyteller_submission.return_value = submission
        assert self.service.check_status('book-123') == 'ready'

    def test_check_status_returns_failed_when_already_failed(self):
        submission = Mock()
        submission.status = 'failed'
        self.mock_db.get_active_storyteller_submission.return_value = submission
        assert self.service.check_status('book-123') == 'failed'

    @patch.dict(os.environ, {'STORYTELLER_ASSETS_DIR': ''})
    def test_check_status_returns_processing_when_no_transcriptions(self):
        submission = Mock()
        submission.status = 'queued'
        submission.submission_dir = 'Test Book'
        submission.storyteller_uuid = None
        self.mock_db.get_active_storyteller_submission.return_value = submission
        self.mock_storyteller.is_configured.return_value = False

        assert self.service.check_status('book-123') == 'processing'
        # Status should have been updated to 'processing'
        assert submission.status == 'processing'
        self.mock_db.save_storyteller_submission.assert_called()

    def test_check_status_returns_ready_when_transcriptions_exist(self):
        submission = Mock()
        submission.status = 'queued'
        submission.submission_dir = 'Test Book'
        submission.storyteller_uuid = None
        self.mock_db.get_active_storyteller_submission.return_value = submission

        # Create fake transcription directory
        with tempfile.TemporaryDirectory() as assets_dir:
            transcripts = Path(assets_dir) / 'assets' / 'Test Book' / 'transcriptions'
            transcripts.mkdir(parents=True)
            (transcripts / '00001-00001.json').write_text('{}')

            with patch.dict(os.environ, {'STORYTELLER_ASSETS_DIR': assets_dir}):
                result = self.service.check_status('book-123')

        assert result == 'ready'
        assert submission.status == 'ready'

    def test_check_status_returns_ready_via_uuid(self):
        submission = Mock()
        submission.status = 'queued'
        submission.submission_dir = 'Test Book'
        submission.storyteller_uuid = 'st-uuid-123'
        self.mock_db.get_active_storyteller_submission.return_value = submission

        self.mock_storyteller.get_word_timeline_chapters.return_value = [{'words': []}]

        with tempfile.TemporaryDirectory() as assets_dir:
            with patch.dict(os.environ, {'STORYTELLER_ASSETS_DIR': assets_dir}):
                result = self.service.check_status('book-123')

        assert result == 'ready'

    # ── _sanitize_dirname ──

    def test_sanitize_dirname_removes_unsafe_chars(self):
        result = self.service._sanitize_dirname('Book: "The <Best> One?"', 'fallback-id')
        assert ':' not in result
        assert '"' not in result
        assert '<' not in result
        assert '>' not in result
        assert '?' not in result

    def test_sanitize_dirname_uses_abs_id_when_title_empty(self):
        result = self.service._sanitize_dirname('', 'book-abc')
        assert result == 'book-abc'

    def test_sanitize_dirname_truncates_long_titles(self):
        long_title = 'A' * 300
        result = self.service._sanitize_dirname(long_title, 'fallback')
        assert len(result) <= 200


if __name__ == '__main__':
    unittest.main(verbosity=2)
