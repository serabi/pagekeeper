"""
Unit tests for the database service, including migration testing.
"""

import json
import logging
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Add project root to Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Override environment variables for testing
os.environ['DATA_DIR'] = 'test_data'
os.environ['BOOKS_DIR'] = 'test_data'

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')


class TestDatabaseServiceIntegration(unittest.TestCase):
    """Unit tests for database service integration functionality."""

    def setUp(self):
        """Set up test environment before each test."""
        # Create temporary directory for test database
        self.temp_dir = tempfile.mkdtemp()
        self.test_db_path = str(Path(self.temp_dir) / 'test_database.db')

        # Import here to avoid circular imports
        from src.db.database_service import DatabaseMigrator, DatabaseService
        from src.db.models import Book, HardcoverDetails, Job, State

        self.DatabaseService = DatabaseService
        self.DatabaseMigrator = DatabaseMigrator
        self.Book = Book
        self.State = State
        self.Job = Job
        self.HardcoverDetails = HardcoverDetails

        # Create database service
        self.db_service = DatabaseService(self.test_db_path)

    def tearDown(self):
        """Clean up after each test."""
        # Close database connection to release file lock on Windows
        if hasattr(self, 'db_service') and hasattr(self.db_service, 'db_manager'):
            self.db_service.db_manager.close()

        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_database_service_initialization(self):
        """Test that database service initializes correctly."""
        self.assertIsNotNone(self.db_service)
        self.assertTrue(Path(self.test_db_path).exists())

    def test_create_book(self):
        """Test creating a book record."""
        test_abs_id = 'test-book-create'

        book = self.Book(
            abs_id=test_abs_id,
            title='Test Book Creation',
            ebook_filename='test-create.epub',
            kosync_doc_id='test-create-doc',
            status='active',
            duration=3600.0  # 1 hour test duration
        )

        saved_book = self.db_service.save_book(book)

        self.assertEqual(saved_book.abs_id, test_abs_id)
        self.assertEqual(saved_book.title, 'Test Book Creation')
        self.assertEqual(saved_book.status, 'active')

        # Verify book can be retrieved
        retrieved_book = self.db_service.get_book_by_abs_id(test_abs_id)
        self.assertIsNotNone(retrieved_book)
        self.assertEqual(retrieved_book.abs_id, test_abs_id)

    def test_delete_book(self):
        """Test deleting a book record with cascading deletes for states and hardcover details."""
        test_abs_id = 'test-book-delete'

        # Create book
        book = self.Book(
            abs_id=test_abs_id,
            title='Test Book Deletion',
            ebook_filename='test-delete.epub',
            kosync_doc_id='test-delete-doc',
            status='active',
            duration=7200.0  # 2 hour test duration
        )

        book = self.db_service.save_book(book)

        # Create multiple states for the book
        states_data = [
            ('kosync', 0.45, {'xpath': '/delete/test/xpath'}),
            ('abs', 0.42, {'timestamp': 1500.0}),
            ('storyteller', 0.40, {'xpath': '/html/body/section[1]/p[3]'}),
            ('booklore', 0.38, {'cfi': 'epubcfi(/6/6[chapter3]!/4/2/8/1:25)'})
        ]

        created_states = []
        for client_name, percentage, extra_data in states_data:
            state = self.State(
                abs_id=test_abs_id,
                book_id=book.id,
                client_name=client_name,
                last_updated=time.time(),
                percentage=percentage,
                **extra_data
            )
            saved_state = self.db_service.save_state(state)
            created_states.append(saved_state)

        # Create hardcover details for the book
        hardcover = self.HardcoverDetails(
            abs_id=test_abs_id,
            book_id=book.id,
            hardcover_book_id='hc-delete-test-123',
            hardcover_edition_id='hc-edition-delete-456',
            hardcover_pages=280,
            isbn='978-9876543210',
            asin='B08DELETETEST',
            matched_by='title'
        )

        self.db_service.save_hardcover_details(hardcover)

        # Create a job for the book
        job = self.Job(
            abs_id=test_abs_id,
            book_id=book.id,
            last_attempt=time.time(),
            retry_count=3,
            last_error='Delete test error'
        )

        self.db_service.save_job(job)

        # Verify all data exists before deletion
        retrieved_book = self.db_service.get_book_by_abs_id(test_abs_id)
        self.assertIsNotNone(retrieved_book)

        retrieved_states = self.db_service.get_states_for_book(book.id)
        self.assertEqual(len(retrieved_states), 4)

        retrieved_hardcover = self.db_service.get_hardcover_details(book.id)
        self.assertIsNotNone(retrieved_hardcover)

        retrieved_job = self.db_service.get_latest_job(book.id)
        self.assertIsNotNone(retrieved_job)

        # Delete the book - this should cascade delete all related data
        success = self.db_service.delete_book(book.id)
        self.assertTrue(success)

        # Verify book is gone
        deleted_book = self.db_service.get_book_by_abs_id(test_abs_id)
        self.assertIsNone(deleted_book)

        # Verify states are gone (cascade delete)
        deleted_states = self.db_service.get_states_for_book(book.id)
        self.assertEqual(len(deleted_states), 0)

        # Verify hardcover details are gone (cascade delete)
        deleted_hardcover = self.db_service.get_hardcover_details(book.id)
        self.assertIsNone(deleted_hardcover)

        # Verify job is gone (cascade delete)
        deleted_job = self.db_service.get_latest_job(book.id)
        self.assertIsNone(deleted_job)

    def test_create_states(self):
        """Test creating state records for multiple clients."""
        test_abs_id = 'test-book-states'

        # Create book first
        book = self.Book(
            abs_id=test_abs_id,
            title='Test Book States',
            ebook_filename='test-states.epub',
            kosync_doc_id='test-states-doc',
            status='active'
        )
        book = self.db_service.save_book(book)

        # Create states for different clients
        states_data = [
            ('kosync', 0.35, {'xpath': '/test/xpath'}),
            ('abs', 0.32, {'timestamp': 1200.5}),
            ('storyteller', 0.30, {'xpath': '/html/body/section[2]/p[5]'}),
            ('booklore', 0.28, {'cfi': 'epubcfi(/6/4[chapter2]!/4/2/6/1:15)'})
        ]

        for client_name, percentage, extra_data in states_data:
            state = self.State(
                abs_id=test_abs_id,
                book_id=book.id,
                client_name=client_name,
                last_updated=time.time(),
                percentage=percentage,
                **extra_data
            )
            saved_state = self.db_service.save_state(state)
            self.assertEqual(saved_state.client_name, client_name)
            self.assertEqual(saved_state.percentage, percentage)

        # Retrieve and verify all states
        states = self.db_service.get_states_for_book(book.id)
        self.assertEqual(len(states), len(states_data))

        # Verify each client has correct data
        state_by_client = {s.client_name: s for s in states}

        self.assertIn('kosync', state_by_client)
        self.assertEqual(state_by_client['kosync'].xpath, '/test/xpath')

        self.assertIn('abs', state_by_client)
        self.assertEqual(state_by_client['abs'].timestamp, 1200.5)

        self.assertIn('storyteller', state_by_client)
        self.assertEqual(state_by_client['storyteller'].xpath, '/html/body/section[2]/p[5]')

        self.assertIn('booklore', state_by_client)
        self.assertEqual(state_by_client['booklore'].cfi, 'epubcfi(/6/4[chapter2]!/4/2/6/1:15)')

    def test_get_books_by_status(self):
        """Test querying books by status."""
        # Create books with different statuses
        active_book = self.Book(
            abs_id='active-book',
            title='Active Book',
            ebook_filename='active.epub',
            kosync_doc_id='active-doc',
            status='active'
        )

        paused_book = self.Book(
            abs_id='paused-book',
            title='Paused Book',
            ebook_filename='paused.epub',
            kosync_doc_id='paused-doc',
            status='paused'
        )

        self.db_service.save_book(active_book)
        self.db_service.save_book(paused_book)

        # Test active books query
        active_books = self.db_service.get_books_by_status('active')
        active_ids = [book.abs_id for book in active_books]
        self.assertIn('active-book', active_ids)
        self.assertNotIn('paused-book', active_ids)

        # Test paused books query
        paused_books = self.db_service.get_books_by_status('paused')
        paused_ids = [book.abs_id for book in paused_books]
        self.assertIn('paused-book', paused_ids)
        self.assertNotIn('active-book', paused_ids)

    def test_statistics(self):
        """Test database statistics functionality."""
        initial_stats = self.db_service.get_statistics()
        initial_books = initial_stats['total_books']
        initial_states = initial_stats['total_states']

        # Add test data
        test_abs_id = 'test-stats-book'
        book = self.Book(
            abs_id=test_abs_id,
            title='Statistics Test Book',
            ebook_filename='stats.epub',
            kosync_doc_id='stats-doc',
            status='active'
        )
        book = self.db_service.save_book(book)

        # Add states
        state = self.State(
            abs_id=test_abs_id,
            book_id=book.id,
            client_name='kosync',
            last_updated=time.time(),
            percentage=0.5
        )
        self.db_service.save_state(state)

        # Check updated statistics
        updated_stats = self.db_service.get_statistics()
        self.assertEqual(updated_stats['total_books'], initial_books + 1)
        self.assertEqual(updated_stats['total_states'], initial_states + 1)

    def test_migration_should_migrate(self):
        """Test migration detection logic."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create test JSON files
            mapping_json = temp_path / "mapping.json"
            state_json = temp_path / "state.json"

            # Create empty JSON files
            mapping_json.write_text('{"mappings": []}')
            state_json.write_text('{}')

            # Create fresh database for migration test
            migration_db_path = temp_path / "migration.db"
            migration_db_service = self.DatabaseService(str(migration_db_path))

            try:
                migrator = self.DatabaseMigrator(
                    migration_db_service,
                    str(mapping_json),
                    str(state_json)
                )

                # Should migrate when database is empty and JSON files exist
                self.assertTrue(migrator.should_migrate())

                # Add a book to database
                book = self.Book(abs_id='existing-book', title='Existing Book')
                migration_db_service.save_book(book)

                # Should not migrate when database has data
                self.assertFalse(migrator.should_migrate())
            finally:
                migration_db_service.db_manager.close()

    def test_migration_mapping_json(self):
        """Test migration of mapping JSON data."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create test mapping JSON
            mapping_json_path = temp_path / "mapping.json"
            mapping_data = {
                "mappings": [
                    {
                        "abs_id": "migration-book-1",
                        "title": "Migration Test Book 1",
                        "ebook_filename": "migration1.epub",
                        "kosync_doc_id": "migration-kosync-1",
                        "transcript_file": "transcript1.json",
                        "status": "active",
                        "hardcover_book_id": "hc-123",
                        "hardcover_pages": 350,
                        "isbn": "978-1234567890",
                        "retry_count": 2,
                        "last_error": "Migration test error"
                    }
                ]
            }

            with open(mapping_json_path, 'w') as f:
                json.dump(mapping_data, f)

            # Create empty state JSON
            state_json_path = temp_path / "state.json"
            with open(state_json_path, 'w') as f:
                json.dump({}, f)

            # Create database for migration
            migration_db_path = temp_path / "migration.db"
            migration_db_service = self.DatabaseService(str(migration_db_path))

            try:
                # Perform migration
                migrator = self.DatabaseMigrator(
                    migration_db_service,
                    str(mapping_json_path),
                    str(state_json_path)
                )

                migrator.migrate()

                # Verify book was migrated
                migrated_book = migration_db_service.get_book_by_abs_id("migration-book-1")
                self.assertIsNotNone(migrated_book)
                self.assertEqual(migrated_book.title, "Migration Test Book 1")
                self.assertEqual(migrated_book.status, "active")

                # Verify hardcover details were migrated
                hardcover = migration_db_service.get_hardcover_details(migrated_book.id)
                self.assertIsNotNone(hardcover)
                self.assertEqual(hardcover.hardcover_book_id, "hc-123")
                self.assertEqual(hardcover.hardcover_pages, 350)
                self.assertEqual(hardcover.isbn, "978-1234567890")

                # Verify job data was migrated
                job = migration_db_service.get_latest_job(migrated_book.id)
                self.assertIsNotNone(job)
                self.assertEqual(job.retry_count, 2)
                self.assertIn("Migration test error", job.last_error)
            finally:
                migration_db_service.db_manager.close()

    def test_migration_state_json(self):
        """Test migration of state JSON data."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create test mapping JSON (minimal)
            mapping_json_path = temp_path / "mapping.json"
            mapping_data = {
                "mappings": [
                    {
                        "abs_id": "state-migration-book",
                        "title": "State Migration Test",
                        "status": "active"
                    }
                ]
            }

            with open(mapping_json_path, 'w') as f:
                json.dump(mapping_data, f)

            # Create test state JSON
            state_json_path = temp_path / "state.json"
            state_data = {
                "state-migration-book": {
                    "last_updated": time.time() - 3600,
                    "kosync_pct": 0.45,
                    "kosync_xpath": "/html/body/div[1]/p[12]",
                    "abs_pct": 0.42,
                    "abs_ts": 1250.5,
                    "absebook_pct": 0.46,
                    "absebook_cfi": "epubcfi(/6/10[chapter5]!/4/2/8/1:45)",
                    "storyteller_pct": 0.44,
                    "storyteller_xpath": "/html/body/section[3]/p[8]",
                    "booklore_pct": 0.43,
                    "booklore_xpath": "/html/body/article[2]/div[1]/p[15]"
                }
            }

            with open(state_json_path, 'w') as f:
                json.dump(state_data, f)

            # Create database for migration
            migration_db_path = temp_path / "migration.db"
            migration_db_service = self.DatabaseService(str(migration_db_path))

            try:
                # Perform migration
                migrator = self.DatabaseMigrator(
                    migration_db_service,
                    str(mapping_json_path),
                    str(state_json_path)
                )

                migrator.migrate()

                # Verify states were migrated
                migrated_book = migration_db_service.get_book_by_abs_id("state-migration-book")
                states = migration_db_service.get_states_for_book(migrated_book.id)
                self.assertEqual(len(states), 5)  # kosync, abs, absebook, storyteller, booklore

                state_by_client = {s.client_name: s for s in states}

                # Check kosync state
                kosync_state = state_by_client['kosync']
                self.assertEqual(kosync_state.percentage, 0.45)
                self.assertEqual(kosync_state.xpath, "/html/body/div[1]/p[12]")

                # Check ABS state
                abs_state = state_by_client['abs']
                self.assertEqual(abs_state.percentage, 0.42)
                self.assertEqual(abs_state.timestamp, 1250.5)

                # Check ABS eBook state
                absebook_state = state_by_client['absebook']
                self.assertEqual(absebook_state.percentage, 0.46)
                self.assertEqual(absebook_state.cfi, "epubcfi(/6/10[chapter5]!/4/2/8/1:45)")

                # Check Storyteller state
                storyteller_state = state_by_client['storyteller']
                self.assertEqual(storyteller_state.percentage, 0.44)
                self.assertEqual(storyteller_state.xpath, "/html/body/section[3]/p[8]")

                # Check BookLore state
                booklore_state = state_by_client['booklore']
                self.assertEqual(booklore_state.percentage, 0.43)
                self.assertEqual(booklore_state.xpath, "/html/body/article[2]/div[1]/p[15]")
            finally:
                migration_db_service.db_manager.close()

    def test_migration_idempotency(self):
        """Test that migration doesn't create duplicates when run multiple times."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create test JSON files
            mapping_json_path = temp_path / "mapping.json"
            mapping_data = {
                "mappings": [
                    {
                        "abs_id": "idempotency-test",
                        "title": "Idempotency Test Book",
                        "status": "active"
                    }
                ]
            }

            with open(mapping_json_path, 'w') as f:
                json.dump(mapping_data, f)

            state_json_path = temp_path / "state.json"
            state_data = {
                "idempotency-test": {
                    "kosync_pct": 0.5,
                    "abs_pct": 0.5
                }
            }

            with open(state_json_path, 'w') as f:
                json.dump(state_data, f)

            # Create database for migration
            migration_db_path = temp_path / "migration.db"
            migration_db_service = self.DatabaseService(str(migration_db_path))

            try:
                migrator = self.DatabaseMigrator(
                    migration_db_service,
                    str(mapping_json_path),
                    str(state_json_path)
                )

                # First migration
                self.assertTrue(migrator.should_migrate())
                migrator.migrate()

                # Check initial counts
                stats_after_first = migration_db_service.get_statistics()
                books_after_first = stats_after_first['total_books']
                states_after_first = stats_after_first['total_states']

                # Second migration should not be needed
                self.assertFalse(migrator.should_migrate())

                # Force second migration anyway
                migrator.migrate()

                # Check counts haven't changed (no duplicates)
                stats_after_second = migration_db_service.get_statistics()
                self.assertEqual(stats_after_second['total_books'], books_after_first)
                self.assertEqual(stats_after_second['total_states'], states_after_first)
            finally:
                migration_db_service.db_manager.close()

    def test_clear_stale_suggestions(self):
        """Test clearing suggestions that are not for active books."""
        from src.db.models import PendingSuggestion

        # 1. Setup Active Books
        active_id = 'active-book-id'
        book = self.Book(abs_id=active_id, title='Active Book', status='active')
        self.db_service.save_book(book)

        # 2. Setup Suggestions
        # Suggestion for the active book (should be preserved)
        s1 = PendingSuggestion(
            source_id=active_id,
            title='Active Book Title',
            author='Author A',
            matches_json='[]'
        )
        self.db_service.save_pending_suggestion(s1)

        # Suggestion for a non-existent book (should be cleared)
        stale_id = 'stale-book-id'
        s2 = PendingSuggestion(
            source_id=stale_id,
            title='Stale Book Title',
            author='Author B',
            matches_json='[]'
        )
        self.db_service.save_pending_suggestion(s2)

        # Verify initial state
        all_suggestions = self.db_service.get_all_actionable_suggestions()
        self.assertEqual(len(all_suggestions), 2)

        # 3. Clear Stale Suggestions
        cleared_count = self.db_service.clear_stale_suggestions()
        self.assertEqual(cleared_count, 1)

        # 4. Verify Final State
        remaining = self.db_service.get_all_actionable_suggestions()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].source_id, active_id)

    def test_hide_unhide_and_resolve_suggestion(self):
        """Test suggestion status transitions for hide, unhide, and resolve."""
        from src.db.models import PendingSuggestion

        suggestion = PendingSuggestion(
            source_id='suggestion-123',
            title='Test Suggestion',
            author='Author',
            matches_json='[]'
        )
        self.db_service.save_pending_suggestion(suggestion)

        actionable = self.db_service.get_all_actionable_suggestions()
        self.assertEqual(len(actionable), 1)
        self.assertEqual(actionable[0].status, 'pending')

        self.assertTrue(self.db_service.hide_suggestion('suggestion-123'))
        hidden = self.db_service.get_hidden_suggestions()
        self.assertEqual(len(hidden), 1)
        self.assertEqual(hidden[0].status, 'hidden')

        self.assertTrue(self.db_service.unhide_suggestion('suggestion-123'))
        pending = self.db_service.get_all_pending_suggestions()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].status, 'pending')

        self.assertTrue(self.db_service.resolve_suggestion('suggestion-123'))
        self.assertEqual(self.db_service.get_all_actionable_suggestions(), [])

    def test_migration_partial_data(self):
        """Test migration with partial/missing data scenarios."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create mapping with book that has no states
            mapping_json_path = temp_path / "mapping.json"
            mapping_data = {
                "mappings": [
                    {
                        "abs_id": "no-states-book",
                        "title": "Book Without States",
                        "status": "active"
                    },
                    {
                        "abs_id": "partial-states-book",
                        "title": "Book With Partial States",
                        "status": "active"
                    }
                ]
            }

            with open(mapping_json_path, 'w') as f:
                json.dump(mapping_data, f)

            # State JSON only has data for one book, and only some clients
            state_json_path = temp_path / "state.json"
            state_data = {
                "partial-states-book": {
                    "kosync_pct": 0.3,
                    "abs_pct": 0.25
                    # Missing storyteller, booklore, absebook
                }
                # Missing no-states-book entirely
            }

            with open(state_json_path, 'w') as f:
                json.dump(state_data, f)

            # Perform migration
            migration_db_path = temp_path / "migration.db"
            migration_db_service = self.DatabaseService(str(migration_db_path))

            try:
                migrator = self.DatabaseMigrator(
                    migration_db_service,
                    str(mapping_json_path),
                    str(state_json_path)
                )

                migrator.migrate()

                # Verify both books were migrated
                book1 = migration_db_service.get_book_by_abs_id("no-states-book")
                self.assertIsNotNone(book1)

                book2 = migration_db_service.get_book_by_abs_id("partial-states-book")
                self.assertIsNotNone(book2)

                # Verify states
                states1 = migration_db_service.get_states_for_book(book1.id)
                self.assertEqual(len(states1), 0)  # No states

                states2 = migration_db_service.get_states_for_book(book2.id)
                self.assertEqual(len(states2), 2)  # Only kosync and abs

                state_clients = [s.client_name for s in states2]
                self.assertIn('kosync', state_clients)
                self.assertIn('abs', state_clients)
                self.assertNotIn('storyteller', state_clients)
                self.assertNotIn('booklore', state_clients)
            finally:
                migration_db_service.db_manager.close()

    # ── T1: get_book_by_ref() resolution — all 5 branches ──

    def test_get_book_by_ref_none(self):
        """get_book_by_ref(None) returns None."""
        self.assertIsNone(self.db_service.get_book_by_ref(None))

    def test_get_book_by_ref_integer_id(self):
        """get_book_by_ref with an integer returns the book by primary key."""
        book = self.Book(abs_id='ref-int-test', title='Ref Int', status='active')
        saved = self.db_service.save_book(book)
        result = self.db_service.get_book_by_ref(saved.id)
        self.assertIsNotNone(result)
        self.assertEqual(result.id, saved.id)

    def test_get_book_by_ref_abs_id_string(self):
        """get_book_by_ref with a string abs_id returns the matching book."""
        book = self.Book(abs_id='abs-ref-test', title='Ref Abs', status='active')
        self.db_service.save_book(book)
        result = self.db_service.get_book_by_ref('abs-ref-test')
        self.assertIsNotNone(result)
        self.assertEqual(result.abs_id, 'abs-ref-test')

    def test_get_book_by_ref_numeric_string_fallthrough(self):
        """get_book_by_ref with a numeric string falls through to book_id lookup."""
        book = self.Book(abs_id='numeric-fall', title='Numeric Fallthrough', status='active')
        saved = self.db_service.save_book(book)
        # Use the numeric book ID as a string — should fall through abs_id miss to int lookup
        result = self.db_service.get_book_by_ref(str(saved.id))
        self.assertIsNotNone(result)
        self.assertEqual(result.id, saved.id)

    def test_get_book_by_ref_nonexistent_string(self):
        """get_book_by_ref with a non-numeric string that doesn't match any abs_id returns None."""
        result = self.db_service.get_book_by_ref('does-not-exist')
        self.assertIsNone(result)

    # ── T2: Book without abs_id — full lifecycle ──

    def test_book_without_abs_id_lifecycle(self):
        """Create a Book with no abs_id, verify auto-id, retrieve, attach states."""
        book = self.Book(title='Standalone Ebook', status='not_started', sync_mode='ebook_only')
        saved = self.db_service.save_book(book, is_new=True)
        self.assertIsNotNone(saved.id)
        self.assertIsNone(saved.abs_id)

        retrieved = self.db_service.get_book_by_ref(saved.id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.title, 'Standalone Ebook')

        # Attach a state
        state = self.State(
            book_id=saved.id,
            client_name='KoSync',
            timestamp=100.0,
            percentage=0.25,
            last_updated=1000.0,
        )
        self.db_service.save_state(state)
        states = self.db_service.get_states_by_book()
        self.assertIn(saved.id, states)
        self.assertEqual(len(states[saved.id]), 1)

    # ── T3: save_book() three-way upsert ──

    def test_save_book_with_id(self):
        """save_book with book.id set updates the existing record."""
        book = self.Book(abs_id='upsert-by-id', title='Original', status='active')
        saved = self.db_service.save_book(book, is_new=True)
        saved.title = 'Updated by ID'
        self.db_service.save_book(saved)
        result = self.db_service.get_book_by_ref(saved.id)
        self.assertEqual(result.title, 'Updated by ID')

    def test_save_book_with_abs_id(self):
        """save_book with book.abs_id set (no id) upserts by abs_id."""
        book = self.Book(abs_id='upsert-by-abs', title='Upserted', status='active')
        self.db_service.save_book(book, is_new=True)
        book2 = self.Book(abs_id='upsert-by-abs', title='Re-upserted', status='active')
        self.db_service.save_book(book2)
        all_books = [b for b in self.db_service.get_all_books() if b.abs_id == 'upsert-by-abs']
        self.assertEqual(len(all_books), 1)
        self.assertEqual(all_books[0].title, 'Re-upserted')

    def test_save_book_new_no_abs_id(self):
        """save_book with neither id nor abs_id creates a new book."""
        book = self.Book(title='Brand New', status='not_started')
        saved = self.db_service.save_book(book, is_new=True)
        self.assertIsNotNone(saved.id)
        self.assertIsNone(saved.abs_id)

    # ── T4: AlignmentService with book_id ──

    def test_alignment_save_retrieve_delete_by_book_id(self):
        """Save, retrieve, and delete alignment by book_id."""
        from unittest.mock import MagicMock

        from src.services.alignment_service import AlignmentService

        book = self.Book(abs_id='align-test', title='Alignment Test', status='active')
        saved = self.db_service.save_book(book)

        polisher = MagicMock()
        svc = AlignmentService(self.db_service, polisher)

        # Save alignment
        svc._save_alignment(saved.id, [{"char": 0, "ts": 0.0}, {"char": 1000, "ts": 60.0}], source='test')

        # has_alignment
        self.assertTrue(svc.has_alignment(saved.id))
        self.assertFalse(svc.has_alignment(999999))

        # get_alignment_info
        info = svc.get_alignment_info(saved.id)
        self.assertIsNotNone(info)
        self.assertEqual(info['num_points'], 2)

        # get_time_for_text
        ts = svc.get_time_for_text(saved.id, char_offset_hint=500)
        self.assertIsNotNone(ts)
        self.assertAlmostEqual(ts, 30.0, delta=1.0)

        # get_char_for_time
        char = svc.get_char_for_time(saved.id, timestamp=30.0)
        self.assertIsNotNone(char)
        self.assertAlmostEqual(char, 500, delta=10)

        # get_book_duration
        dur = svc.get_book_duration(saved.id)
        self.assertAlmostEqual(dur, 60.0, delta=0.1)

        # delete_alignment
        svc.delete_alignment(saved.id)
        self.assertFalse(svc.has_alignment(saved.id))


class TestLegacyDatabaseMigration(unittest.TestCase):
    """
    Tests that simulate a pre-Alembic (legacy) database to verify the
    baseline-stamp upgrade path works for users who skip multiple releases.

    The crash scenario:
      1. User has a database created before Alembic was introduced.
      2. The database has a 'books' table but NO 'alembic_version' table.
      3. On startup, DatabaseService calls command.upgrade("head").
      4. Alembic tries to run initial_database_schema which calls op.create_table("books").
      5. SQLite raises OperationalError: table books already exists → container crashes.

    The fix:
      Before running upgrade, detect this state and stamp the DB at the initial
      revision (76886bc89d6e), then run the remaining migrations on top. This
      preserves the original tables while still executing later schema changes.
    """

    def _make_legacy_db(self, db_path: str):
        """
        Create a bare SQLite database that mimics a pre-Alembic installation.
        Manually creates all tables that initial_database_schema (76886bc89d6e)
        would have created, but WITHOUT an alembic_version table. This is the
        exact state a legacy user's database would be in before upgrading.

        We must create all tables from that migration (books, hardcover_details,
        states, jobs) because stamping at 76886bc89d6e tells Alembic those tables
        already exist. Only creating 'books' would cause subsequent ADD COLUMN
        migrations to fail with 'no such table: hardcover_details'.
        """
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                abs_id TEXT NOT NULL UNIQUE,
                abs_title TEXT,
                ebook_filename TEXT,
                kosync_doc_id TEXT,
                transcript_file TEXT,
                status TEXT DEFAULT 'active',
                duration REAL
            );

            CREATE TABLE hardcover_details (
                abs_id TEXT PRIMARY KEY,
                hardcover_book_id TEXT,
                hardcover_edition_id TEXT,
                hardcover_pages INTEGER,
                isbn TEXT,
                asin TEXT,
                matched_by TEXT,
                FOREIGN KEY (abs_id) REFERENCES books(abs_id) ON DELETE CASCADE
            );

            CREATE TABLE states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                abs_id TEXT NOT NULL,
                client_name TEXT NOT NULL,
                last_updated REAL,
                percentage REAL,
                timestamp REAL,
                xpath TEXT,
                cfi TEXT,
                FOREIGN KEY (abs_id) REFERENCES books(abs_id) ON DELETE CASCADE
            );

            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                abs_id TEXT NOT NULL,
                last_attempt REAL,
                retry_count INTEGER DEFAULT 0,
                last_error TEXT,
                FOREIGN KEY (abs_id) REFERENCES books(abs_id) ON DELETE CASCADE
            );
        """)
        # Insert a row so we can verify data is preserved after migration
        conn.execute("""
            INSERT INTO books (abs_id, abs_title, status)
            VALUES ('legacy-book-1', 'My Legacy Book', 'active')
        """)
        conn.commit()
        conn.close()

    def test_legacy_db_does_not_crash_on_startup(self):
        """
        Scenario: legacy database with 'books' but no 'alembic_version'.
        DatabaseService.__init__ must complete without raising any exception.
        Previously this would crash with: OperationalError: table books already exists
        """
        import sqlite3

        from src.db.database_service import DatabaseService

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / 'legacy.db')
            self._make_legacy_db(db_path)

            # Verify precondition: books exists, alembic_version does not
            conn = sqlite3.connect(db_path)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            conn.close()
            self.assertIn('books', tables)
            self.assertNotIn('alembic_version', tables)

            # This must not raise — previously it would crash here
            try:
                db_service = DatabaseService(db_path)
                db_service.db_manager.close()
            except Exception as e:
                self.fail(
                    f"DatabaseService raised {type(e).__name__} on legacy database: {e}\n"
                    "This means the legacy stamp fix is not working."
                )

    def test_legacy_db_runs_remaining_migrations_after_baseline_stamp(self):
        """
        After DatabaseService starts up against a legacy database, the alembic_version
        table must exist and the database must reflect newer migrations that ran
        after the baseline stamp. This is the contract that makes skipped-version
        upgrades safe.
        """
        import sqlite3

        from src.db.database_service import DatabaseService

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / 'legacy_stamp.db')
            self._make_legacy_db(db_path)

            db_service = DatabaseService(db_path)
            db_service.db_manager.close()

            # alembic_version must now exist and hold a revision
            conn = sqlite3.connect(db_path)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            self.assertIn('alembic_version', tables, "alembic_version table was not created after stamp")

            version = conn.execute("SELECT version_num FROM alembic_version").fetchone()
            self.assertIsNotNone(version, "alembic_version table is empty after startup")
            self.assertTrue(len(version[0]) > 0, f"Unexpected empty version_num: {version[0]!r}")

            books_cols = {
                row[1]: row for row in conn.execute("PRAGMA table_info(books)").fetchall()
            }
            self.assertIn("id", books_cols, "books.id was not added by later migrations")
            self.assertIn("sync_mode", books_cols, "books.sync_mode was not added by later migrations")
            self.assertIn("storyteller_uuid", books_cols, "books.storyteller_uuid was not added by later migrations")

            reading_journal_tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            self.assertIn(
                "reading_journals",
                reading_journal_tables,
                "Later migrations did not create reading_journals on a legacy database",
            )
            self.assertIn(
                "kosync_documents",
                reading_journal_tables,
                "Later migrations did not create kosync_documents on a legacy database",
            )
            conn.close()

    def test_legacy_db_preserves_existing_data(self):
        """
        Data that existed in the legacy database before migration must survive intact.
        The stamp+upgrade process must never destroy existing rows.
        """
        import sqlite3

        from src.db.database_service import DatabaseService

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / 'legacy_data.db')
            self._make_legacy_db(db_path)  # inserts 'legacy-book-1'

            db_service = DatabaseService(db_path)

            # The pre-existing book row must still be readable via the service
            book = db_service.get_book_by_abs_id('legacy-book-1')
            db_service.db_manager.close()

            self.assertIsNotNone(book, "Pre-existing legacy book was lost after migration")
            self.assertEqual(book.title, 'My Legacy Book')
            self.assertEqual(book.status, 'active')

    def test_intermediate_revision_upgrades_to_head(self):
        """
        Scenario: a user who upgraded once (got to initial Alembic revision)
        but then skipped several releases. Their DB is stamped at 76886bc89d6e
        with the original 4-table schema. Calling upgrade("head") must produce
        the full modern schema including books.id PK and states.book_id FK.
        """
        import sqlite3

        from alembic.config import Config

        from alembic import command
        from src.db.database_service import LEGACY_BASELINE_REVISION

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "intermediate.db")

            # Create the exact schema from the initial migration (76886bc89d6e)
            # NOTE: uses abs_title (the original column name at that revision)
            conn = sqlite3.connect(db_path)
            conn.executescript("""
                CREATE TABLE books (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    abs_id TEXT NOT NULL UNIQUE,
                    abs_title TEXT,
                    ebook_filename TEXT,
                    kosync_doc_id TEXT,
                    transcript_file TEXT,
                    status TEXT DEFAULT 'active',
                    duration REAL
                );

                CREATE TABLE hardcover_details (
                    abs_id TEXT PRIMARY KEY,
                    hardcover_book_id TEXT,
                    hardcover_edition_id TEXT,
                    hardcover_pages INTEGER,
                    isbn TEXT,
                    asin TEXT,
                    matched_by TEXT,
                    FOREIGN KEY (abs_id) REFERENCES books(abs_id) ON DELETE CASCADE
                );

                CREATE TABLE states (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    abs_id TEXT NOT NULL,
                    client_name TEXT NOT NULL,
                    last_updated REAL,
                    percentage REAL,
                    timestamp REAL,
                    xpath TEXT,
                    cfi TEXT,
                    FOREIGN KEY (abs_id) REFERENCES books(abs_id) ON DELETE CASCADE
                );

                CREATE TABLE jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    abs_id TEXT NOT NULL,
                    last_attempt REAL,
                    retry_count INTEGER DEFAULT 0,
                    last_error TEXT,
                    FOREIGN KEY (abs_id) REFERENCES books(abs_id) ON DELETE CASCADE
                );
            """)
            # Insert test data to verify it survives
            conn.execute("""
                INSERT INTO books (abs_id, abs_title, status)
                VALUES ('intermediate-book', 'Intermediate Test', 'active')
            """)
            conn.execute("""
                INSERT INTO states (abs_id, client_name, percentage)
                VALUES ('intermediate-book', 'kosync', 0.5)
            """)
            conn.commit()
            conn.close()

            # Stamp at the initial revision (simulating a user who already ran
            # the initial migration but hasn't upgraded since)
            alembic_dir = Path(__file__).parent.parent / "alembic"
            alembic_ini = alembic_dir.parent / "alembic.ini"
            alembic_cfg = Config(str(alembic_ini))
            alembic_cfg.set_main_option("script_location", str(alembic_dir))
            alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
            command.stamp(alembic_cfg, LEGACY_BASELINE_REVISION)

            # Now let DatabaseService do its normal startup (which calls upgrade("head"))
            from src.db.database_service import DatabaseService

            db_service = DatabaseService(db_path)
            db_service.db_manager.close()

            # Verify the modern schema was applied
            conn = sqlite3.connect(db_path)

            # books.id should still exist (was already there)
            books_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(books)").fetchall()
            }
            self.assertIn("id", books_cols)
            self.assertIn("sync_mode", books_cols, "Later migration columns not applied")

            # states.book_id must exist (added by Phase 2)
            states_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(states)").fetchall()
            }
            self.assertIn("book_id", states_cols, "states.book_id not added by Phase 2")

            # reading_journals table must exist (created by later migration)
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            self.assertIn("reading_journals", tables)
            self.assertIn("kosync_documents", tables)

            # Verify data survived
            book = conn.execute(
                "SELECT title FROM books WHERE abs_id = 'intermediate-book'"
            ).fetchone()
            self.assertIsNotNone(book, "Test book was lost during migration")
            self.assertEqual(book[0], "Intermediate Test")

            # Verify states.book_id was populated correctly
            state = conn.execute(
                "SELECT book_id FROM states WHERE abs_id = 'intermediate-book'"
            ).fetchone()
            self.assertIsNotNone(state, "Test state was lost during migration")
            self.assertIsNotNone(state[0], "states.book_id was not populated")

            conn.close()

    def test_fresh_db_still_initializes_correctly(self):
        """
        Regression guard: a brand-new (empty) database must still initialize cleanly.
        The legacy detection must not interfere with normal first-run behaviour.
        """
        from src.db.database_service import DatabaseService

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / 'fresh.db')

            # File must not exist yet — genuine first run
            self.assertFalse(Path(db_path).exists())

            try:
                db_service = DatabaseService(db_path)
                db_service.db_manager.close()
            except Exception as e:
                self.fail(f"DatabaseService raised {type(e).__name__} on fresh database: {e}")

            self.assertTrue(Path(db_path).exists(), "Database file was not created")




class TestSuggestionSourceScoping(unittest.TestCase):
    """Tests that suggestion operations are scoped by (source_id, source)."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.test_db_path = str(Path(self.temp_dir) / 'test_database.db')
        from src.db.database_service import DatabaseService
        from src.db.models import PendingSuggestion
        self.db_service = DatabaseService(self.test_db_path)
        self.PendingSuggestion = PendingSuggestion

    def tearDown(self):
        if hasattr(self, 'db_service') and hasattr(self.db_service, 'db_manager'):
            self.db_service.db_manager.close()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_suggestion_exists_scoped_by_source(self):
        """suggestion_exists returns False when the source_id exists under a different source."""
        suggestion = self.PendingSuggestion(
            source_id='id1', title='Test', source='kosync',
        )
        self.db_service.save_pending_suggestion(suggestion)
        self.assertTrue(self.db_service.suggestion_exists('id1', source='kosync'))
        self.assertFalse(self.db_service.suggestion_exists('id1', source='abs'))

    def test_upsert_different_source_creates_two_rows(self):
        """save_pending_suggestion with same source_id but different source creates distinct rows."""
        s1 = self.PendingSuggestion(source_id='id1', title='ABS Title', source='abs')
        s2 = self.PendingSuggestion(source_id='id1', title='KOSync Title', source='kosync')
        self.db_service.save_pending_suggestion(s1)
        self.db_service.save_pending_suggestion(s2)

        abs_suggestion = self.db_service.get_suggestion('id1', source='abs')
        kosync_suggestion = self.db_service.get_suggestion('id1', source='kosync')
        self.assertIsNotNone(abs_suggestion)
        self.assertIsNotNone(kosync_suggestion)
        self.assertEqual(abs_suggestion.title, 'ABS Title')
        self.assertEqual(kosync_suggestion.title, 'KOSync Title')

    def test_resolve_scoped_by_source(self):
        """resolve_suggestion only deletes the row matching the given source."""
        s1 = self.PendingSuggestion(source_id='id1', title='ABS Title', source='abs')
        s2 = self.PendingSuggestion(source_id='id1', title='KOSync Title', source='kosync')
        self.db_service.save_pending_suggestion(s1)
        self.db_service.save_pending_suggestion(s2)

        self.db_service.resolve_suggestion('id1', source='abs')

        self.assertFalse(self.db_service.suggestion_exists('id1', source='abs'))
        self.assertTrue(self.db_service.suggestion_exists('id1', source='kosync'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
