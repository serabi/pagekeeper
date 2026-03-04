# Local-First Reading Tracker with Hardcover Sync

## Context

Book Sync is currently a sync engine — it moves reading progress between platforms but doesn't own the reading experience itself. The goal is to evolve it into a **local-first reading tracker** where all reading data (status, dates, journals, goals, stats) lives in SQLite, with Hardcover as an optional bidirectional sync target for users who want social features.

**Why local-first:** Self-hosters value data privacy. A reading tracker that requires an external service contradicts the self-hosting ethos. By making everything work locally and treating Hardcover as optional, we serve both privacy-focused users and Hardcover users with a single architecture.

**Scope:** Only synced books (books already managed by Book Sync) get reading tracking. No standalone book import/discovery.

**Key insight:** `Book.status` already stores reading states (`active`=reading, `paused`, `dnf`, `completed`) alongside sync states (`processing`, `failed_*`). The foundation is there — we need to formalize it with dates, journals, and a dedicated UI.

---

## Phase 1: Local Reading Data Model

> Add reading-specific data to the database. Everything else builds on this.

### 1A. Extend Book model with reading fields

**File:** `src/db/models.py` (class `Book`, line 65)

Add columns:
```
started_at          String(10), nullable   — YYYY-MM-DD, when user started reading
finished_at         String(10), nullable   — YYYY-MM-DD, when user finished
rating              Float, nullable        — 0-5 stars (half-star increments)
read_count          Integer, default 1     — number of times read (for re-reads)
```

These are **local** reading fields — they exist regardless of whether Hardcover is enabled. The existing `status` field (`active`/`paused`/`dnf`/`completed`) already handles reading status.

### 1B. ReadingJournal model

**File:** `src/db/models.py` — new model

```
ReadingJournal:
  id                Integer PK autoincrement
  abs_id            String FK → books.abs_id, CASCADE
  event             String(20)              — "started" | "progress" | "finished" | "note" | "paused" | "resumed" | "dnf"
  entry             Text, nullable          — freeform text note
  percentage        Float, nullable         — progress at time of entry
  created_at        DateTime, default now   — when entry was created
```

Lightweight journal — no metadata blobs or privacy settings (that's Hardcover's concern). The `event` field allows both auto-generated entries (started/finished/paused) and user-written notes.

### 1C. ReadingGoal model

**File:** `src/db/models.py` — new model

```
ReadingGoal:
  id                Integer PK autoincrement
  year              Integer, unique         — e.g. 2026
  target_books      Integer                 — goal number of books to finish
```

Simple yearly book count goal. Pages/time goals can be added later.

### 1D. Migration

**File:** `alembic/versions/add_reading_tracker.py`

- ALTER TABLE `books`: add `started_at`, `finished_at`, `rating`, `read_count`
- CREATE TABLE `reading_journals`
- CREATE TABLE `reading_goals`

### 1E. DatabaseService methods

**File:** `src/db/database_service.py`

Add CRUD methods following existing patterns:
- `get_reading_journals(abs_id)` → list of journal entries, ordered by created_at desc
- `add_reading_journal(abs_id, event, entry, percentage)` → creates entry
- `delete_reading_journal(journal_id)`
- `get_reading_goal(year)` / `save_reading_goal(year, target)`
- `get_reading_stats(year)` → dict with books_finished, total for goal tracking
- `update_book_reading_fields(abs_id, started_at, finished_at, rating, read_count)`

### 1F. Auto-journal on status transitions

**File:** `src/sync_manager.py` (or wherever status transitions happen — `books.py` blueprint for manual, sync_manager for auto)

When a book transitions status, auto-create a journal entry:
- `active` (new) → journal: `event="started"`, set `started_at` if not set
- `active` → `completed` → journal: `event="finished"`, set `finished_at`
- `active` → `paused` → journal: `event="paused"`
- `paused` → `active` → journal: `event="resumed"`
- `active` → `dnf` → journal: `event="dnf"`

These are local operations — zero API calls.

---

## Phase 2: Reading Tab UI

> New navigation destination focused on reading tracking, separate from the sync dashboard.

### 2A. New blueprint and route

**File:** `src/blueprints/reading_bp.py` — new file

```python
@reading_bp.route("/reading")    → main reading tracker page
@reading_bp.route("/reading/book/<abs_id>")  → book detail with journal
```

Register in `web_server.py` alongside existing blueprints.

### 2B. Navigation update

**File:** `templates/partials/navbar.html`

Add "Reading" link between the service icons and Settings. Same styling as existing nav links.

### 2C. Reading tab main page — `templates/reading.html`

**Layout (top to bottom):**

1. **Stats bar** — horizontal strip at top:
   - Books finished this year: `X / Y goal` with mini progress bar
   - Currently reading: count
   - Total books tracked: count

2. **Status filter tabs** — horizontal pills:
   - All | Reading | Finished | Paused | DNF | Want to Read
   - Click to filter the grid below

3. **Book grid** — reuse existing card macro pattern, but with reading-focused data:
   - Cover image (reuse existing cover proxy)
   - Title + author
   - Progress bar with percentage
   - Status badge (color-coded)
   - Started/finished dates
   - Rating (star display, clickable to set)
   - Click card → opens book detail

4. **Reading Goal widget** — bottom or sidebar:
   - Current year goal with progress ring/bar
   - "Set Goal" button if no goal exists
   - Shows `X of Y books` finished

### 2D. Book detail view — `templates/reading_detail.html`

**Accessed via:** clicking a book card on the reading tab, or `/reading/book/<abs_id>`

**Layout:**
- **Header:** Cover + title + author + status badge + rating stars
- **Quick actions:** Status dropdown (Reading/Finished/Paused/DNF), date pickers for started/finished
- **Progress:** Current unified progress bar
- **Journal section:**
  - Timeline of journal entries (auto-generated + user notes)
  - Each entry shows: event icon, date/time, text, percentage at the time
  - "Add Note" textarea + submit button at top
  - Auto-entries (started, finished, paused) shown with distinct styling
- **Sync info** (collapsed by default): per-client progress breakdown (reuse from dashboard card)

### 2E. API endpoints for reading tab

**File:** `src/blueprints/reading_bp.py`

```
GET  /api/reading/books              → all books with reading data
GET  /api/reading/book/<abs_id>      → single book detail + journals
POST /api/reading/book/<abs_id>/status   → update reading status
POST /api/reading/book/<abs_id>/rating   → set rating (0-5)
POST /api/reading/book/<abs_id>/dates    → update started_at/finished_at
POST /api/reading/book/<abs_id>/journal  → add journal note
DELETE /api/reading/journal/<id>         → delete journal entry
GET  /api/reading/stats/<year>       → reading stats for year
POST /api/reading/goal/<year>        → set/update yearly goal
```

### 2F. JavaScript

**File:** `static/js/reading.js` — new file

Following existing vanilla JS patterns (no frameworks):
- Status dropdown handler → POST to status endpoint → update badge
- Rating star click handler → POST to rating endpoint
- Journal form submission → POST to journal endpoint → prepend to timeline
- Goal setting modal
- Filter tabs (client-side filtering like existing dashboard)

### 2G. CSS

**File:** `static/css/reading.css` — new file

Reading-specific styles: journal timeline, rating stars, goal progress ring, status filter pills. Use existing CSS variables from `variables.css`.

---

## Phase 3: Hardcover Sync Improvements (Backend)

> Make the Hardcover sync client smart: caching, bidirectional, rate-limited. This phase is invisible to users but essential for Phase 4.

### 3A. Extend HardcoverDetails model

**File:** `src/db/models.py` (class `HardcoverDetails`, line 111)

Add columns for API call caching:
```
hardcover_user_book_id      Integer, nullable
hardcover_user_book_read_id Integer, nullable
hardcover_status_id         Integer, nullable
hardcover_audio_edition_id  String(255), nullable
```

Note: `started_at`/`finished_at` are now on the `Book` model (Phase 1A), not duplicated here.

**Migration:** Combined with Phase 1D migration if implemented together, or separate alembic version.

### 3B. Rate limiter

**File:** `src/api/hardcover_client.py` — add to `query()` method

Minimum-interval limiter (1 req/sec = 60/min ceiling). Essential for 50+ book libraries.

### 3C. Cached user_book and read IDs

**File:** `src/sync_clients/hardcover_sync_client.py` — refactor `update_progress()`

Add `_ensure_user_book()` and `_ensure_read_id()` helpers. Reduces steady-state from 3 API calls → 1 per book per cycle.

Handle **re-reads**: if latest read has `finished_at` set and new progress starts low (< 50%), create a new `insert_user_book_read`.

### 3D. Smarter status transitions

**File:** `src/sync_clients/hardcover_sync_client.py` — replace `_handle_status_transition()`

- Paused (4) + progress > 2% → Currently Reading (2) — resume detection
- DNF (5) → never auto-promote
- Read (3) → never auto-change
- Sync local `Book.status` changes TO Hardcover status (local is source of truth)

### 3E. Write tracker integration

**File:** `src/sync_clients/hardcover_sync_client.py`

Call `record_write('Hardcover', book.abs_id)` after successful progress updates. Prevents feedback loops in 3F.

### 3F. Bidirectional: implement `get_service_state()`

**File:** `src/sync_clients/hardcover_sync_client.py` — replace the `return None` at line 43

Read progress from Hardcover's `user_book_reads`. Calculate percentage from pages or seconds. Skip if `is_own_write()`. `can_be_leader()` stays `False` — Hardcover participates in delta detection but never leads (can't provide text for alignment).

### 3G. Bulk state fetching

**File:** `src/api/hardcover_client.py` — add `get_currently_reading()`

Single GraphQL query fetches all user_books with status 1 or 2, with nested reads. Returns dict keyed by `hardcover_book_id`.

**File:** `src/sync_clients/hardcover_sync_client.py` — implement `fetch_bulk_state()`

1 API call instead of N for reading state. API budget: ~11 calls/cycle for 50 books.

### 3H. Edition-aware sync

Store both page-based and audio edition IDs during automatch. Select correct edition based on `book.sync_mode` when updating progress. Refactor `get_default_edition()` to return all editions.

### 3I. Add Hardcover to client poller

**File:** `src/services/client_poller.py`

Add `('Hardcover', 'HARDCOVER')` to `_POLLABLE`. Controlled by `HARDCOVER_POLL_SECONDS` env var.

---

## Phase 4: Hardcover ↔ Local Reading Data Sync

> Bidirectional sync of reading metadata (status, dates, journals) between local tracker and Hardcover.

### 4A. Local → Hardcover: status sync

When user changes reading status on the Reading Tab (Phase 2):
- Update local `Book.status` (immediate)
- If Hardcover is enabled and book is matched: push status change to Hardcover via `update_status()`
- Map local statuses to Hardcover status IDs:
  - `active` → 2 (Currently Reading)
  - `completed` → 3 (Read)
  - `paused` → 4 (Paused)
  - `dnf` → 5 (Did Not Finish)

**File:** `src/blueprints/reading_bp.py` — in the status update endpoint

### 4B. Hardcover → Local: status sync

When bidirectional read (Phase 3F) detects a status change on Hardcover:
- Update local `Book.status` to match
- Auto-create local journal entry for the transition
- Map Hardcover status IDs → local statuses (reverse of 4A)

**File:** `src/sync_clients/hardcover_sync_client.py` — in `get_service_state()` or a new `sync_reading_metadata()` method

### 4C. Local → Hardcover: date sync

When `started_at` or `finished_at` changes locally:
- Push to Hardcover's `user_book_reads` (update existing read or create new one)
- Respect existing Hardcover dates — only push if local date is newer

### 4D. Optional Hardcover journal mirroring

**Settings** (via settings page):
- `HARDCOVER_JOURNAL_ON_START` (default: false)
- `HARDCOVER_JOURNAL_ON_FINISH` (default: false)

When enabled, auto-create Hardcover reading journal entries on start/finish:

**File:** `src/api/hardcover_client.py` — add `create_reading_journal()`

Uses `ReadingJournalCreateType`:
```
mutation: insert_reading_journal
fields: book_id, edition_id, event ("started" | "finished"),
        action_at (date), privacy_setting_id (1), tags ([])
```

Triggered from status transitions in `_handle_status_transition()` when settings are enabled.

### 4E. Hardcover status on dashboard

**File:** `src/blueprints/dashboard.py`, `templates/index.html`

Show Hardcover status badge (color-coded) on the sync dashboard book cards for matched books. Lightweight — just surfaces `hardcover_status_id` from cached data.

---

## Phase 5: Reading Stats and Goals

> Analytics and yearly goal tracking on the reading tab.

### 5A. Stats computation

**File:** `src/blueprints/reading_bp.py` — `GET /api/reading/stats/<year>`

Query books with `status='completed'` and `finished_at` in the given year:
- Books finished this year (count)
- Books finished per month (array of 12 counts, for a chart)
- Average rating of finished books
- Currently reading count
- Total pages read (if page counts available from Hardcover editions)

All computed from local SQLite data — no external API calls.

### 5B. Goal progress

**File:** `src/blueprints/reading_bp.py` — goal endpoints

- `GET /api/reading/goal/<year>` → `{target: N, completed: M, percentage: P}`
- `POST /api/reading/goal/<year>` → `{target_books: N}`

### 5C. Stats display on reading tab

**File:** `templates/reading.html`

Stats bar at top of reading tab (already described in 2C):
- Yearly goal progress: `X / Y books` with progress bar
- Monthly breakdown: simple bar chart (12 bars, CSS-only or lightweight canvas)
- Stat cards: books finished, average rating, currently reading count

---

## Phase Dependencies

```
Phase 1 (data model) ← foundation, implement first
  ├── Phase 2 (reading tab UI) ← depends on Phase 1
  │     └── Phase 5 (stats & goals) ← depends on Phase 2 for UI
  └── Phase 3 (Hardcover backend) ← depends on Phase 1 for Book model changes
        └── Phase 4 (Hardcover ↔ local sync) ← depends on Phases 2 + 3
```

**Recommended implementation order:** 1 → 2 → 3 → 4 → 5

Phase 2 delivers value immediately (reading tab works fully local). Phase 3+4 layer on Hardcover sync. Phase 5 is polish.

---

## Key Files to Create

| File | Purpose |
|------|---------|
| `src/blueprints/reading_bp.py` | Reading tab routes and API |
| `templates/reading.html` | Reading tab main page |
| `templates/reading_detail.html` | Book detail with journal |
| `static/js/reading.js` | Reading tab interactivity |
| `static/css/reading.css` | Reading tab styles |
| `alembic/versions/add_reading_tracker.py` | DB migration |

## Key Files to Modify

| File | Changes |
|------|---------|
| `src/db/models.py` | Add reading fields to Book, new ReadingJournal + ReadingGoal models, extend HardcoverDetails |
| `src/db/database_service.py` | CRUD for journals, goals, reading stats, book reading fields |
| `src/web_server.py` | Register reading_bp blueprint |
| `templates/partials/navbar.html` | Add "Reading" nav link |
| `src/sync_clients/hardcover_sync_client.py` | Cached IDs, status logic, bidirectional sync, edition handling |
| `src/api/hardcover_client.py` | Rate limiter, bulk fetch, journal creation |
| `src/services/client_poller.py` | Add Hardcover to pollable list |
| `src/blueprints/dashboard.py` | Surface Hardcover status badge |
| `templates/index.html` | Hardcover status badges on cards |
| `templates/settings.html` | Hardcover journal toggles |
| `src/blueprints/settings_bp.py` | Journal settings |

## Existing Code to Reuse

- `Book.status` transitions — already in `src/blueprints/books.py` (pause, resume, dnf, complete endpoints)
- `write_tracker` — `src/services/write_tracker.py`
- `SyncClient` interface — `src/sync_clients/sync_client_interface.py`
- `ClientPoller._POLLABLE` — `src/services/client_poller.py`
- `HardcoverClient.query()` — existing GraphQL transport
- `HardcoverClient.find_user_book()` — already fetches reads
- Dashboard card macro — `templates/index.html` (adapt for reading tab)
- Settings tab pattern — `templates/settings.html`
- Cover proxy — `src/blueprints/covers.py`
- CSS design system — `static/css/variables.css`

## Verification

1. **Phase 1:** `./run-tests.sh` — add tests for new models, journal auto-creation on status transitions
2. **Phase 2:** Manual testing — navigate to /reading, verify book grid, status changes, journal entries, goal setting all work locally with no Hardcover configured
3. **Phase 3:** `./run-tests.sh tests/test_hardcover_sync_client.py` — test cached IDs, status transitions, bidirectional state reading, bulk fetch
4. **Phase 4:** Enable Hardcover, change status on reading tab → verify push to Hardcover. Change status on Hardcover website → verify pull to local on next sync cycle
5. **Phase 5:** Set yearly goal, mark books complete with dates, verify stats computation and display
6. **Rate limit:** With 50+ books and Hardcover enabled, monitor API call count — should stay well under 60 req/min with bulk fetch + caching
