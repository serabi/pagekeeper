# Refactoring Plan

Assessed 2026-03-11 after extracting `ReadingService`, `cover_resolver`, and `book_metadata_service` from `reading_bp.py`.

---

## Critical: Architectural Violation

**`src/services/book_metadata_service.py`** imports from `src/blueprints/helpers` — services should never depend on blueprints. Three functions need to move to a utility module:

- `get_service_web_url()` → `src/utils/service_url_helper.py`
- `get_hardcover_book_url()` → `src/utils/service_url_helper.py`
- `get_booklore_client()` → should be injected or moved to a non-blueprint utility

This was introduced during the reading_bp refactoring and should be fixed first.

---

## Tier 1 — High Value, Low Risk

### 1. Split `api/hardcover_client.py` (1333 lines)

The largest file in the codebase. Clear decomposition boundaries:

| Extract to | Responsibility |
|-----------|---------------|
| `HardcoverGraphQLClient` | Raw GraphQL execution, query building |
| `HardcoverBookMatcher` | ISBN/title search, matching logic |
| `HardcoverReadOperations` | Create/finish/update reads |
| `HardcoverListOperations` | List queries, user book lookups |

Estimated reduction: 1333 → ~450 lines (66%).

### 2. Extract service URL helpers from `blueprints/helpers.py` (506 lines)

Fixes the architectural violation above and shrinks the kitchen-sink helpers file:

| Extract to | What moves |
|-----------|-----------|
| `utils/service_url_helper.py` | `get_service_web_url()`, `get_hardcover_book_url()`, URL resolution (~60 lines) |
| `services/ebook_search_service.py` | `find_ebook_file()`, `get_kosync_id_for_ebook()`, KosyncID computation (~110 lines) |
| `services/book_mapping_manager.py` | `cleanup_mapping_resources()`, mapping CRUD |

Estimated reduction: 506 → ~300 lines (41%).

### 3. Extract `BaseAPIClient` abstract class

8 API clients (ABS, KoSync, BookFusion, Booklore, CWA, Storyteller, OpenLibrary, Hardcover) duplicate:

- `is_configured()` — all 8 clients
- `check_connection()` — 6 clients
- `requests.Session()` setup and header management — 7 clients
- TTL-based dict caching — 3+ clients
- Try/except HTTP error handling patterns

Extract to `src/api/base_client.py` with shared templates. Saves ~50 lines per client.

### 4. Consolidate Hardcover status mapping

Status ID ↔ label maps are duplicated in:

- `sync_clients/hardcover_sync_client.py` (lines 14-45)
- `services/book_metadata_service.py` (line 61)
- Scattered in blueprints

Extract to `src/utils/hardcover_status_mapper.py` — single source of truth.

---

## Tier 2 — Medium Value, Medium Risk

### 5. Split `utils/ebook_utils.py` (1140 lines)

| Extract to | Responsibility |
|-----------|---------------|
| `EPUBNavigator` | XPath/CFI/percentage conversion |
| `TextExtractor` | `text_at_percentage`, `resolve_xpath` |
| `KosyncHasher` | Hash computation methods |
| `SegmentParser` | Structural tag handling |
| `utils/lru_cache.py` | Generic LRU cache (currently inlined) |

Estimated reduction: 1140 → ~600 lines (47%).

### 6. Split `api/kosync_server.py` (1040 lines)

| Extract to | Responsibility |
|-----------|---------------|
| `KosyncStorageService` | CRUD logic |
| `KosyncSyncLogic` | Conflict resolution |
| Routes stay | Thin HTTP handlers |

### 7. Split `sync_clients/hardcover_sync_client.py` (887 lines)

| Extract to | Responsibility |
|-----------|---------------|
| `HardcoverPushStrategy` | All push operations |
| `HardcoverPullStrategy` | All pull operations |
| `HardcoverDateManager` | Date lifecycle logic |

### 8. Extract Storyteller logic from `blueprints/matching_bp.py` (788 lines)

Storyteller submission logic (`_create_storyteller_reservation`, `_submit_to_storyteller_async`) is mixed with matching routes. Extract to a `StorytellerSubmissionService`.

### 9. Continue slimming `blueprints/reading_bp.py` (806 lines)

Still has 9 private helpers that could become a `ReadingDataBuilder` service:

- `_build_book_reading_data()` — 65 lines of data enrichment
- `_synthetic_journal()` — creates mock objects
- `_is_genuinely_reading()` — heuristic logic
- Timeline construction helpers

---

## Tier 3 — High Value, Higher Risk

### 10. Decompose `db/database_service.py` (725 lines, 81 methods)

God object that facades 7 repositories but also contains business logic. Split by domain:

- `BookDataService` — book CRUD, queries
- `StateService` — sync state management
- `SettingsService` — app settings
- Domain-specific query helpers (Hardcover, BookFusion)

### 11. Refactor `sync_manager.py` (966 lines)

Split orchestration from execution:

- `SyncOrchestrator` — scheduling, coordination
- `SyncExecutor` — actual sync cycle logic
- `SyncStateBuilder` — delta detection, aggregation

---

## Simplification Opportunities

Places where code is **too complex** for what it does — unnecessary abstractions, dead fields, duplicate code, and over-defensive patterns.

### Dead Code / Unused Fields

1. **`ServiceState.is_configured` field** — `sync_clients/sync_client_interface.py:14`
   - Set by every sync client, never read by any consumer. The `is_configured()` check happens on the client *before* `get_service_state()` is called, so by the time a `ServiceState` exists, it's always true.
   - **Fix:** Remove the field from the dataclass and all sync client constructors.

2. **Duplicate `_reconcile_socket_listener()` function** — `web_server.py:40-90` and `138-199`
   - Identical 50-line function defined twice. Line 122 calls the second definition; the first is dead code.
   - **Fix:** Delete lines 40-90.

3. **`get_syncable_books()` pass-through** — `services/library_service.py:31-36`
   - One-liner that just calls `self.database_service.get_all_books()` with no filtering or transformation.
   - **Fix:** Call `database_service.get_all_books()` directly, or add the filtering logic that the name implies.

### Trivial Wrappers / Unnecessary Indirection

4. **~60 trivial repository wrappers in DatabaseService** — `db/database_service.py:190-554`
   - Many methods are 1-to-1 pass-throughs to repositories (e.g., `get_setting()`, `set_setting()`, `get_book()`, `get_all_books()`). No added logic, just delegation.
   - **Fix:** As part of the Tier 3 DatabaseService decomposition (#10), identify which methods add real orchestration value vs. pure pass-throughs. Callers of pass-throughs can use repositories directly.

5. **`secrets_compare()` wrapping `hmac.compare_digest()`** — `blueprints/settings_bp.py:61-64`
   - One-liner that adds no value over calling `hmac.compare_digest()` directly.
   - **Fix:** Inline.

### Over-Defensive Patterns

6. **First-run marker file with bare `except`** — `api/api_clients.py:63-74`
   - `ABSClient.check_connection()` creates a `/data/.first_run_done` marker file with a try/except that silently swallows all exceptions from `os.path.exists()` and file creation. The `os.path.exists()` call can't actually throw for a valid path.
   - **Fix:** Remove the inner try/except or limit to specific exceptions.

7. **Over-complex Booklore cache stale-data pruning** — `api/booklore_client.py:353-428`
   - 75 lines of stale data detection with 3 verification strategies (ID validity, filename mismatch, title mismatch) inside a full cache refresh. If the cache is being refreshed from scratch, stale detection is redundant.
   - **Fix:** Simplify to a single cache-replace strategy.

8. **`_upsert()` race-condition handling for SQLite** — `db/base_repository.py:72-102`
   - Insert-then-catch-IntegrityError-then-requery pattern. SQLite is single-writer; true concurrent conflicts are impossible. A simple query-then-insert/update is clearer.
   - **Fix:** Replace with query-first pattern.

### Over-Parameterized Constructors *(lower priority — verbose but not buggy)*

9. **`BackgroundJobService.__init__` takes 12 parameters** — `services/background_job_service.py:19-45`
   - All parameters are always provided by the DI container, never partially. Consider passing the container itself or grouping related dependencies.

10. **`SuggestionService.__init__` takes 8 parameters** — `services/suggestion_service.py:21-35`
    - Same pattern. All provided by DI container.

11. **`TbrRepository.add_tbr_item()` takes 11 parameters** — `db/tbr_repository.py:28-71`
    - Could accept a `TbrItem` object or dict instead of 11 scattered keyword arguments.

### Redundant Patterns Across Modules

12. **Token refresh + retry-on-401 pattern duplicated** — `api/booklore_client.py:159-187`, `api/storyteller_api.py:49-88`
    - Multiple API clients reimplement the same ~30-line token caching and refresh-on-401 retry pattern.
    - **Fix:** Addressed by the BaseAPIClient extraction (existing plan item #3).

---

## Code Smells to Address Incrementally

### Bare exception catching (~288 occurrences)

`except Exception as e:` everywhere — masks bugs. Replace with specific exceptions (`RequestException`, `SQLAlchemyError`, etc.) as files are touched.

### Magic numbers

| Value | Meaning | Used in |
|-------|---------|---------|
| `0.99` | "finished" threshold | reading_date_service, sync clients |
| `0.01` | "has real progress" | reading_date_service |
| `3600` | seconds per hour | book_metadata_service, multiple |
| `80` | fuzzy match threshold | suggestion_service, matching |

Extract to named constants in relevant modules.

### Scattered configuration

`os.environ.get()` called directly in every client, service, and blueprint. Consider centralizing env var validation at startup.

---

## Completed

- [x] Extract `ReadingService` from `reading_bp.py` and `books.py` (2026-03-11)
- [x] Extract `cover_resolver.py` from `reading_bp.py` (2026-03-11)
- [x] Extract `book_metadata_service.py` from `reading_bp.py` (2026-03-11)
- [x] Promote `_push_completion_to_clients` / `_push_booklore_read_status` to public API (2026-03-11)
- [x] Consolidate `max_progress` into `ReadingService.max_progress()` (2026-03-11)
- [x] Fix architectural violation: extract `service_url_helper.py` from blueprint helpers, remove blueprint imports from services (2026-03-11)
