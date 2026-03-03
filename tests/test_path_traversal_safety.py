"""Tests for path traversal safety utilities and their integration with cleanup_mapping_resources."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.utils.path_utils import is_safe_path_within, sanitize_filename


class TestSanitizeFilename(unittest.TestCase):
    """Unit tests for sanitize_filename()."""

    def test_simple_filename_unchanged(self):
        self.assertEqual(sanitize_filename("book.epub"), "book.epub")

    def test_strips_forward_slash_traversal(self):
        self.assertEqual(sanitize_filename("../../etc/passwd"), "passwd")

    def test_strips_backslash_traversal(self):
        self.assertEqual(sanitize_filename("..\\..\\secret.txt"), "secret.txt")

    def test_strips_mixed_separators(self):
        self.assertEqual(sanitize_filename("../folder\\file.epub"), "file.epub")

    def test_absolute_path_stripped(self):
        self.assertEqual(sanitize_filename("/etc/shadow"), "shadow")

    def test_none_returns_none(self):
        self.assertIsNone(sanitize_filename(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(sanitize_filename(""))

    def test_dots_only_returns_none(self):
        self.assertIsNone(sanitize_filename(".."))
        self.assertIsNone(sanitize_filename("."))
        self.assertIsNone(sanitize_filename("..."))

    def test_hidden_file_returns_none(self):
        self.assertIsNone(sanitize_filename(".hidden"))
        self.assertIsNone(sanitize_filename(".env"))

    def test_filename_with_spaces(self):
        self.assertEqual(sanitize_filename("my book.epub"), "my book.epub")

    def test_deeply_nested_traversal(self):
        self.assertEqual(
            sanitize_filename("../../../../../../../../tmp/evil.sh"),
            "evil.sh"
        )


class TestIsSafePathWithin(unittest.TestCase):
    """Unit tests for is_safe_path_within()."""

    def test_safe_child(self):
        with tempfile.TemporaryDirectory() as parent:
            child = Path(parent) / "subdir" / "file.txt"
            child.parent.mkdir(parents=True, exist_ok=True)
            child.touch()
            self.assertTrue(is_safe_path_within(child, parent))

    def test_traversal_blocked(self):
        with tempfile.TemporaryDirectory() as parent:
            escaped = Path(parent) / ".." / "outside.txt"
            self.assertFalse(is_safe_path_within(escaped, parent))

    def test_non_existent_child_still_resolves(self):
        """Non-existent paths are resolved relative to cwd — still checked."""
        with tempfile.TemporaryDirectory() as parent:
            child = Path(parent) / "does_not_exist.txt"
            self.assertTrue(is_safe_path_within(child, parent))

    def test_exact_parent_is_safe(self):
        with tempfile.TemporaryDirectory() as parent:
            self.assertTrue(is_safe_path_within(parent, parent))

    def test_sibling_directory_blocked(self):
        with tempfile.TemporaryDirectory() as base:
            allowed = Path(base) / "allowed"
            sibling = Path(base) / "sibling"
            allowed.mkdir()
            sibling.mkdir()
            target = sibling / "secret.txt"
            target.touch()
            self.assertFalse(is_safe_path_within(target, allowed))


class TestCleanupMappingResourcesPathTraversal(unittest.TestCase):
    """Integration: cleanup_mapping_resources must NOT delete files outside allowed dirs."""

    def test_ebook_filename_traversal_does_not_delete_outside_cache(self):
        """A book with ebook_filename='../secret.txt' must not delete a file outside the cache dir."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir) / "epub_cache"
            cache_dir.mkdir()

            # Create a file outside cache that an attacker would target
            secret_file = Path(tmpdir) / "secret.txt"
            secret_file.write_text("sensitive data")

            book = SimpleNamespace(
                abs_id="test-book",
                abs_title="Test Book",
                ebook_filename="../secret.txt",
                transcript_file=None,
                kosync_doc_id=None,
                sync_mode="ebook_only",
                original_ebook_filename=None,
            )

            mock_container = MagicMock()
            mock_container.epub_cache_dir.return_value = cache_dir
            mock_container.data_dir.return_value = Path(tmpdir)
            mock_manager = MagicMock()
            mock_manager.epub_cache_dir = None
            mock_db = MagicMock()

            with patch("src.blueprints.helpers.get_container", return_value=mock_container), \
                 patch("src.blueprints.helpers.get_manager", return_value=mock_manager), \
                 patch("src.blueprints.helpers.get_database_service", return_value=mock_db), \
                 patch("src.blueprints.helpers.get_abs_service") as mock_abs:
                mock_abs.return_value.remove_from_collection.return_value = None

                from src.blueprints.helpers import cleanup_mapping_resources
                cleanup_mapping_resources(book)

            # The secret file must still exist
            self.assertTrue(secret_file.exists(), "File outside cache dir was deleted — path traversal not blocked!")
            self.assertEqual(secret_file.read_text(), "sensitive data")

    def test_transcript_file_traversal_does_not_delete_outside_transcripts(self):
        """A book with transcript_file pointing outside transcripts/ must not be deleted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            transcripts_dir = Path(tmpdir) / "transcripts"
            transcripts_dir.mkdir()

            secret_file = Path(tmpdir) / "important.json"
            secret_file.write_text("{}")

            book = SimpleNamespace(
                abs_id="test-book",
                abs_title="Test Book",
                ebook_filename=None,
                transcript_file=str(secret_file),
                kosync_doc_id=None,
                sync_mode="audiobook",
                original_ebook_filename=None,
            )

            mock_container = MagicMock()
            mock_container.data_dir.return_value = Path(tmpdir)
            mock_manager = MagicMock()
            mock_manager.epub_cache_dir = None
            mock_db = MagicMock()

            with patch("src.blueprints.helpers.get_container", return_value=mock_container), \
                 patch("src.blueprints.helpers.get_manager", return_value=mock_manager), \
                 patch("src.blueprints.helpers.get_database_service", return_value=mock_db), \
                 patch("src.blueprints.helpers.get_abs_service") as mock_abs:
                mock_abs.return_value.remove_from_collection.return_value = None

                from src.blueprints.helpers import cleanup_mapping_resources
                cleanup_mapping_resources(book)

            self.assertTrue(secret_file.exists(), "File outside transcripts dir was deleted — path traversal not blocked!")

    def test_legitimate_transcript_deletion_still_works(self):
        """A transcript file correctly inside transcripts/ should still be deleted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            transcripts_dir = Path(tmpdir) / "transcripts"
            transcripts_dir.mkdir()

            legit_transcript = transcripts_dir / "book_alignment.json"
            legit_transcript.write_text("[]")

            book = SimpleNamespace(
                abs_id="test-book",
                abs_title="Test Book",
                ebook_filename=None,
                transcript_file=str(legit_transcript),
                kosync_doc_id=None,
                sync_mode="audiobook",
                original_ebook_filename=None,
            )

            mock_container = MagicMock()
            mock_container.data_dir.return_value = Path(tmpdir)
            mock_manager = MagicMock()
            mock_manager.epub_cache_dir = None
            mock_db = MagicMock()

            with patch("src.blueprints.helpers.get_container", return_value=mock_container), \
                 patch("src.blueprints.helpers.get_manager", return_value=mock_manager), \
                 patch("src.blueprints.helpers.get_database_service", return_value=mock_db), \
                 patch("src.blueprints.helpers.get_abs_service") as mock_abs:
                mock_abs.return_value.remove_from_collection.return_value = None

                from src.blueprints.helpers import cleanup_mapping_resources
                cleanup_mapping_resources(book)

            self.assertFalse(legit_transcript.exists(), "Legitimate transcript file should have been deleted")


if __name__ == "__main__":
    unittest.main()
