# Hardcover Sync Flow — Complete Reference

> Internal reference documenting every Hardcover integration path in PageKeeper.
> Generated 2026-03-08 from codebase analysis.

---

## 1. Overview

PageKeeper integrates with Hardcover.app via its GraphQL API. The integration is **bidirectional**: progress and status flow from local → Hardcover (push) and from Hardcover → local (pull). All API calls go through `HardcoverClient`, which enforces rate limiting and retries.

```
┌──────────────────────────────┐          ┌─────────────────────────┐
│          PageKeeper          │          │     Hardcover.app       │
│                              │          │                         │
│  SyncManager.sync_cycle()    │──push──▶│  user_book (status)     │
│  HardcoverSyncClient         │  progress│  user_book_read (pages) │
│                              │  status  │  reading_journal        │
│  Dashboard auto-sync         │◀──pull──│                         │
│  reading_date_service        │  dates   │                         │
│                              │  status  │                         │
│  reading_bp / books_bp       │──push──▶│  (user actions)         │
│  (user-initiated actions)    │  status  │                         │
│                              │  rating  │                         │
│                              │  dates   │                         │
└──────────────────────────────┘          └─────────────────────────┘
```

### Trigger Points

| Trigger | Frequency | Code Entry |
|---------|-----------|------------|
| Sync cycle (background) | Every N minutes (`SYNC_PERIOD_MINS`) | `SyncManager.sync_cycle()` |
| Dashboard load | On page load (5min throttle) | `dashboard_bp.index()` |
| User action (UI) | On click | `books_bp` / `reading_bp` routes |
| Instant Sync | On ABS webhook | `sync_cycle(target_abs_id=...)` |

---

## 2. Push Paths (Local → Hardcover)

### 2.1 Progress Updates (Sync Cycle)

**When:** Every sync cycle, for each active book linked to Hardcover.
**Code:** `HardcoverSyncClient.update_progress()` → `HardcoverClient.update_progress()`

Flow:
1. `SyncManager._sync_single_book()` calls `HardcoverSyncClient.update_progress()`
2. Calls `automatch_hardcover()` if no link exists yet
3. Looks up cached `user_book_id` via `_ensure_user_book()` (avoids API call if cached)
4. Selects correct edition via `_select_edition_id()` (ebook vs audio based on `sync_source`)
5. Calculates page number or progress_seconds from percentage
6. Calls `_handle_status_transition()` to auto-advance status if needed
7. Calls `HardcoverClient.update_progress()` with pages/seconds + dates
8. Records write via `record_write()` for echo suppression

**Page-based books:** `progress_pages = max(1, int(total_pages * percentage))`
**Audiobooks:** `progress_seconds = int(audio_seconds * percentage)`

Date behavior during progress push:
- `started_at` and `finished_at` from the local `Book` model are passed through
- `HardcoverClient.update_progress()` only fills missing dates on HC side (won't overwrite existing)
- The >2% threshold gates `started_at`: dates aren't set until the reader has made real progress

### 2.2 Automatic Status Transitions

**When:** During progress push, based on percentage thresholds.
**Code:** `HardcoverSyncClient._handle_status_transition()` (line 618)

| Current HC Status | Condition | New HC Status |
|-------------------|-----------|---------------|
| Want to Read (1) | progress > 2% | Currently Reading (2) |
| Currently Reading (2) | progress > 99% | Read (3) |
| Paused (4) | progress > 2% | Currently Reading (2) |
| DNF (5) | any progress | *no change* |
| Read (3) | any progress | *no change* |

After transition: calls `record_write()` and optionally `_mirror_journal_if_enabled()`.

### 2.3 User-Initiated Status Push

**When:** User clicks Pause, DNF, Resume, Mark Complete, or changes status on the Reading detail page.
**Code:** Multiple routes → `HardcoverSyncClient.push_local_status()`

| Route | Status Pushed | File |
|-------|---------------|------|
| `POST /api/pause/<id>` | `paused` → HC_PAUSED (4) | `books_bp.py:162` |
| `POST /api/dnf/<id>` | `dnf` → HC_DNF (5) | `books_bp.py:187` |
| `POST /api/resume/<id>` | `active` → HC_CURRENTLY_READING (2) | `books_bp.py:211` |
| `POST /api/mark-complete/<id>` | Pushes 100% progress (triggers HC_READ via transition) | `books_bp.py:105` |
| `POST /api/reading/book/<id>/status` | Any valid status | `reading_bp.py:858` |

`push_local_status()` flow:
1. Maps local status → HC status ID via `LOCAL_TO_HC_STATUS`
2. Selects edition via `_select_edition_id()`
3. Calls `HardcoverClient.update_status()`
4. Updates cached `hardcover_status_id`
5. Records write for echo suppression
6. Optionally mirrors journal entry

### 2.4 Date Push (Explicit "Sync to Hardcover")

**When:** User clicks "Sync to Hardcover" button on Reading detail page.
**Code:** `reading_bp.sync_dates_to_hardcover()` → `reading_date_service.push_dates_to_hardcover()`

Route: `POST /api/reading/book/<id>/dates/sync-hardcover`

Flow:
1. Fetches local `Book.started_at` / `Book.finished_at`
2. Fetches current HC read dates via `find_user_book()`
3. With `force=True`: pushes any local date that differs from HC
4. Calls `HardcoverClient.update_progress()` with `force_dates=True`
5. When `force_dates=True`, `update_progress()` overwrites existing HC dates (not just fill-missing)

Without force (called during progress pushes): only fills in missing dates on HC side.

### 2.5 Rating Push

**When:** User sets/changes rating on Reading detail page.
**Code:** `reading_bp.update_rating()` → `HardcoverSyncClient.push_local_rating()`

Route: `POST /api/reading/book/<id>/rating`

Flow:
1. Validates rating (0-5, 0.5 increments)
2. Saves locally via `update_book_reading_fields()`
3. Calls `push_local_rating()` which:
   - Ensures a `user_book` exists via `_ensure_writable_user_book()`
   - Calls `HardcoverClient.update_user_book()` with `{'rating': float}`
   - Records write for suppression

### 2.6 Book Matching (Automatch)

**When:** First sync cycle for a new book, or when `update_progress()` is called for an unmatched book.
**Code:** `HardcoverSyncClient.automatch_hardcover()`

Search strategy cascade:
1. **ISBN** — `search_by_isbn(isbn)` from ABS metadata
2. **ASIN** — `search_by_isbn(asin)` (ASIN used as ISBN-10 lookup)
3. **Title + Author** — `search_by_title_author(title, author)` with fuzzy matching (60% title, 40% author weighting, >0.5 threshold)
4. **Title only** — `search_by_title_author(title, "")` fallback

If a match is found but has no pages, falls back to checking for audiobook edition (`get_all_editions()`).

After match:
1. Creates `HardcoverDetails` record with `book_id`, `edition_id`, `pages`, `audio_seconds`, `slug`
2. Calls `_create_or_adopt_user_book()` — checks if user already has this book on HC before creating

### 2.7 Manual Match

**When:** User pastes a Hardcover URL, ID, or slug on the book detail page.
**Code:** `HardcoverSyncClient.set_manual_match()`

Accepts: URL (`hardcover.app/books/slug`), numeric ID, or slug string.
Resolves via `HardcoverClient.resolve_book_from_input()`.

### 2.8 Journal Mirroring (Optional)

**When:** Status transitions to Currently Reading (2) or Read (3), if env vars are set.
**Code:** `HardcoverSyncClient._mirror_journal_if_enabled()`

| Env Var | Event Created |
|---------|---------------|
| `HARDCOVER_JOURNAL_ON_START=true` | `started_reading` |
| `HARDCOVER_JOURNAL_ON_FINISH=true` | `finished_reading` |

Calls `HardcoverClient.create_reading_journal()` with `book_id`, `edition_id`, `event`, and the user's `HARDCOVER_JOURNAL_PRIVACY` setting (default: 3 = private).

### 2.9 Journal Note Push (Optional)

**When:** User creates a journal note on the Reading detail page, if push is enabled.
**Code:** `reading_bp.add_journal()` → `HardcoverSyncClient.push_journal_note()`

Route: `POST /api/reading/book/<id>/journal`

Flow:
1. Note is saved locally first
2. Fire-and-forget call to `push_journal_note()`
3. Checks per-book `journal_sync` override → global `HARDCOVER_JOURNAL_PUSH_NOTES` fallback
4. If enabled: calls `HardcoverClient.create_reading_journal(event='note', entry=..., privacy_setting_id=...)`
5. Logged via `log_hardcover_action()`

**Per-book override:** `hardcover_details.journal_sync` column:
- `'on'` → always push, regardless of global setting
- `'off'` → never push, regardless of global setting
- `None` → defer to global `HARDCOVER_JOURNAL_PUSH_NOTES` setting

**Privacy:** Uses `HARDCOVER_JOURNAL_PRIVACY` setting (1=public, 2=followers, 3=private). Default: private.

### 2.10 Manual Journal Push (On-Demand)

**When:** User clicks "Push to Hardcover" in the timeline menu for a note or highlight.
**Code:** `reading_bp.push_journal_to_hardcover()`

Route: `POST /api/reading/journal/<id>/push-hardcover`

Request body (optional): `{ "privacy": 1|2|3 }` — per-push privacy override.

Flow:
1. Opens a confirmation modal showing note preview and a privacy dropdown (default from `HARDCOVER_JOURNAL_PRIVACY`)
2. User selects privacy and confirms
3. Loads journal entry by ID
4. Validates entry has text and book is HC-linked
5. Uses `privacy` from request body if provided (1/2/3), otherwise falls back to global `HARDCOVER_JOURNAL_PRIVACY`
6. Calls `HardcoverClient.create_reading_journal(event='note', entry=..., privacy_setting_id=...)`
7. Returns success/error JSON

This enables pushing highlights (auto-imported from BookFusion) and retroactive notes with per-push privacy control.

---

## 3. Pull Paths (Hardcover → Local)

### 3.1 Dashboard Auto-Sync (Dates)

**When:** Every dashboard load, throttled to once per 5 minutes.
**Code:** `dashboard_bp.index()` → `sync_reading_dates()` / `auto_complete_finished_books()`

Throttle key: `dashboard_date_sync_last_run` (stored in settings table).
Cooldown: 300 seconds.

`sync_reading_dates()` iterates all books and:
1. Skips books in processing states (`pending`, `processing`, etc.)
2. For books missing `started_at` or `finished_at`: calls `pull_reading_dates()`
3. For active books with an external `finished_at`: marks completed (with re-read guard)
4. For active books at >=99% local progress: marks completed

`auto_complete_finished_books()` separately checks active books at >=99% and marks them completed.

### 3.2 Date Pulling

**When:** Called by `sync_reading_dates()`, status transitions, and date auto-fill.
**Code:** `reading_date_service.pull_reading_dates()`

Pull priority:
1. **Hardcover** — `find_user_book()` → latest `user_book_read.started_at / finished_at`
2. **ABS** — `get_progress()` → `startedAt / finishedAt` (Unix epoch ms → date)

Returns dict with `started_at` and/or `finished_at` (YYYY-MM-DD strings).

Fill-missing-only behavior:
- Only applied to books where the local field is `None`
- For active books, `started_at` requires local progress > 1% (ABS/HC auto-set dates unreliably)

### 3.3 Status Pull (Sync Cycle)

**When:** Every sync cycle, during `get_service_state()`.
**Code:** `HardcoverSyncClient._sync_status_from_hardcover()`

Flow:
1. `get_service_state()` compares cached `hardcover_status_id` with live HC `status_id`
2. If different and NOT our own write: calls `_sync_status_from_hardcover()`
3. Maps HC status → local status via `HC_TO_LOCAL_STATUS`
4. Updates `book.status` and saves
5. Creates a reading journal entry for the transition
6. Logs via `log_hardcover_action()`

### 3.4 Book Detail Metadata Pull (On-Demand)

**When:** User views the Reading detail page for a HC-linked book.
**Code:** `reading_bp.reading_detail()` (line 404)

Fetches from `HardcoverClient.get_book_metadata()`:
- Description, genres, tags, subtitle, release_year
- Only uses description/tags from **verified matches** (`matched_by` in `manual`, `cover_picker`)
- `release_year` is always shown regardless of match type

### 3.5 Match Adoption (One-Time on Link)

**When:** During automatch or manual match, if the user already has the book on Hardcover.
**Code:** `HardcoverSyncClient._create_or_adopt_user_book()`

If `get_user_book()` returns an existing `user_book`:
- Adopts existing HC status (does NOT overwrite with PageKeeper status)
- Caches `user_book_id` and `status_id` locally
- Logged as `adopt_user_book` action

If no existing `user_book`:
- Creates one with status mapped from local (`LOCAL_TO_HC_STATUS`)

---

## 4. Safety Mechanisms

### 4.1 "Fill Missing Only" Pattern

Both push and pull directions default to only filling in dates/fields when the target is empty:

- **Push:** `HardcoverClient.update_progress()` checks `existing_read.started_at` / `finished_at` before setting
- **Pull:** `sync_reading_dates()` only writes `started_at` / `finished_at` if the local field is `None`
- **Exception:** `force_dates=True` (user-initiated date edit) overwrites in both directions

### 4.2 Write Suppression (Echo Loop Prevention)

**Code:** `src/services/write_tracker.py`

Prevents: PageKeeper pushes progress → HC reflects it → PageKeeper reads it back → treats as new change.

- `record_write('Hardcover', abs_id, state)` — called after every successful push
- `is_own_write('Hardcover', abs_id, state=current_state)` — called before processing pulls
- Suppression window: **60 seconds** (`_DEFAULT_SUPPRESSION_WINDOW`)
- State comparison: matches on `pct`, `xpath`, `cfi`, `ts` (within tolerance)
- Thread-safe: uses `threading.Lock`

Used in:
- `get_service_state()` — skip progress pull if we just wrote it
- `_sync_status_from_hardcover()` — skip status pull if we just pushed it

### 4.3 Re-Read Guard

**Code:** `reading_date_service._mark_completed()` (line 227) and `sync_reading_dates()` (line 322)

Two layers:
1. **`_mark_completed()`:** If `book.finished_at` already exists, does NOT overwrite it — increments `read_count` instead
2. **`sync_reading_dates()`:** If an external source reports `finished_at` but local progress is < 99%, skips auto-complete (user is likely re-reading)

### 4.4 Rate Limiting

**Code:** `HardcoverClient._rate_limit()` and retry logic in `query()`

- **Proactive:** 1 request/second minimum interval (`_min_interval = 1.0`)
- Uses `threading.Lock` for thread safety across concurrent sync operations
- **Reactive:** On HTTP 429, retries up to 3 times with exponential backoff (5s → 10s → 20s)

### 4.5 Dashboard Throttle

**Code:** `dashboard_bp.index()` (line 42)

- Auto-complete and date sync operations limited to once per 5 minutes
- Uses `dashboard_date_sync_last_run` key in settings table
- Prevents expensive API calls on every page refresh

### 4.6 Match Adoption (Status Preservation)

**Code:** `HardcoverSyncClient._create_or_adopt_user_book()` (line 317)

When linking a book that already exists on the user's Hardcover shelf:
- Adopts the existing HC status instead of overwriting it
- Prevents a "Read" book from being reset to "Want to Read" on match

---

## 5. Date Lifecycle

Complete flow for `started_at` and `finished_at` through the system:

### `started_at`

```
Book becomes active
  │
  ├─ User clicks Resume/Start → _pull_started_at() → pull_reading_dates()
  │    ├─ HC has started_at? → use it
  │    ├─ ABS has startedAt? → use it
  │    └─ fallback → today's date
  │
  ├─ Progress push → HardcoverClient.update_progress()
  │    └─ If HC read has no started_at AND percentage > 2%:
  │         set started_at = book.started_at or today
  │
  ├─ Dashboard sync → sync_reading_dates()
  │    └─ If book.started_at is None AND status is active/paused/completed/dnf:
  │         pull from HC/ABS (only if local progress > 1%)
  │
  └─ User edits date → reading_bp.update_dates()
       └─ push_dates_to_hardcover(force=True) → overwrites HC
```

### `finished_at`

```
Book reaches completion
  │
  ├─ Auto-complete (dashboard) → _mark_completed()
  │    ├─ book.finished_at already set? → keep it (re-read: increment read_count)
  │    ├─ External source has finished_at? → use it
  │    └─ fallback → today's date
  │
  ├─ User clicks Mark Complete → books_bp.mark_complete()
  │    └─ Sets finished_at = today, pushes 100% to all clients
  │
  ├─ Progress push → HardcoverClient.update_progress()
  │    └─ If is_finished AND HC read has no finished_at:
  │         set finished_at = book.finished_at or today
  │
  ├─ Dashboard sync → sync_reading_dates()
  │    └─ If book.finished_at is None AND status is completed:
  │         pull from HC/ABS
  │
  └─ User edits date → reading_bp.update_dates()
       └─ push_dates_to_hardcover(force=True) → overwrites HC
```

---

## 6. Status Mapping Table

### Local ↔ Hardcover Status Codes

| Local Status | HC Status ID | HC Label | Push Map | Pull Map |
|-------------|-------------|----------|----------|----------|
| `active` | 2 | Currently Reading | `LOCAL_TO_HC_STATUS['active']` = 2 | `HC_TO_LOCAL_STATUS[2]` = `'active'` |
| `completed` | 3 | Read | `LOCAL_TO_HC_STATUS['completed']` = 3 | `HC_TO_LOCAL_STATUS[3]` = `'completed'` |
| `paused` | 4 | Paused | `LOCAL_TO_HC_STATUS['paused']` = 4 | `HC_TO_LOCAL_STATUS[4]` = `'paused'` |
| `dnf` | 5 | Did Not Finish | `LOCAL_TO_HC_STATUS['dnf']` = 5 | `HC_TO_LOCAL_STATUS[5]` = `'dnf'` |
| — | 1 | Want to Read | Used as default when creating new user_book | Not pulled (no local equivalent) |

### Journal Event Mapping (HC Transition → Local Journal)

| HC Status ID | Local Journal Event |
|-------------|-------------------|
| 2 (Currently Reading) | `resumed` |
| 3 (Read) | `finished` |
| 4 (Paused) | `paused` |
| 5 (DNF) | `dnf` |

### Journal Mirror Events (Optional Push)

| HC Status ID | HC Journal Event | Env Gate |
|-------------|-----------------|----------|
| 2 (Currently Reading) | `started_reading` | `HARDCOVER_JOURNAL_ON_START` |
| 3 (Read) | `finished_reading` | `HARDCOVER_JOURNAL_ON_FINISH` |

---

## 7. Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HARDCOVER_TOKEN` | *(none)* | API bearer token from hardcover.app/account/api |
| `HARDCOVER_ENABLED` | `false` | Master switch — set to `false` to disable even with token present |
| `HARDCOVER_JOURNAL_ON_START` | `false` | Mirror "started reading" journal entries to HC |
| `HARDCOVER_JOURNAL_ON_FINISH` | `false` | Mirror "finished reading" journal entries to HC |
| `HARDCOVER_JOURNAL_PUSH_NOTES` | `false` | Auto-push journal notes to HC on creation (global default) |
| `HARDCOVER_JOURNAL_PRIVACY` | `3` | Privacy for all HC journal entries: 1=public, 2=followers, 3=private |
| `HARDCOVER_WEB_URL` | *(hardcoded)* | Base URL for generating HC book links |
| `HARDCOVER_WEB_URL_EXTERNAL` | *(none)* | External-facing URL override for HC links |

### Key Files

| File | Lines | Role |
|------|-------|------|
| `src/api/hardcover_client.py` | ~1164 | GraphQL API client, rate limiting, search, mutations |
| `src/sync_clients/hardcover_sync_client.py` | ~815 | Sync orchestration, status transitions, matching, progress push |
| `src/services/reading_date_service.py` | ~365 | Date pulling/pushing, auto-completion, re-read guard |
| `src/sync_manager.py` | ~950 | Core sync cycle orchestrator (calls HC sync client) |
| `src/blueprints/reading_bp.py` | ~929 | User-facing date/status/rating/journal routes |
| `src/blueprints/books.py` | ~784 | Dashboard book actions (pause/dnf/resume/mark-complete) |
| `src/blueprints/dashboard.py` | ~354 | Auto-sync trigger (5min throttle) |
| `src/services/write_tracker.py` | ~76 | Echo loop prevention |

### Database Tables

| Table / Model | Relevant Fields |
|--------------|----------------|
| `Book` | `status`, `started_at`, `finished_at`, `rating`, `read_count`, `sync_source` |
| `HardcoverDetails` | `hardcover_book_id`, `hardcover_edition_id`, `hardcover_user_book_id`, `hardcover_user_book_read_id`, `hardcover_status_id`, `hardcover_pages`, `hardcover_audio_seconds`, `hardcover_audio_edition_id`, `hardcover_slug`, `hardcover_cover_url`, `isbn`, `asin`, `matched_by`, `journal_sync` |
| `State` | `abs_id`, `client_name`, `percentage`, `timestamp` |
| `ReadingJournal` | `abs_id`, `event`, `entry`, `percentage`, `created_at` |
| `HardcoverSyncLog` | Action logging for push/pull operations |
