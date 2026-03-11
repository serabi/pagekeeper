# Decision: TBR "Mark as Read" — Deferred to Future Feature

## Context

With 64+ TBR books, some may have already been read outside PageKeeper. Users want a way to move these from TBR to "completed" without entering dates or going through the full Start Reading → active → complete flow. This was evaluated as a potential addition to the TBR feature.

## Decision: Defer

**This is deferred to a future feature**, likely part of issue #18 (Reading Stats & Goals) or a standalone "Reading History" shelf.

### Why not now

- **Linked TBR items** (with `book_abs_id`): Technically straightforward — skip `active`, go straight to `completed` on the Book record. But this is a narrow case since most TBR items aren't linked.
- **Unlinked TBR items** (no library book): The Book model requires a library record to track completion. Adding a status to TBR itself creates a parallel tracking system that would need to be reconciled later.
- The proper solution is a standalone reading history that can record "I read this" without requiring the book to exist in the Audiobookshelf/library pipeline. That's a broader feature.

### Design notes for future implementation

When this is picked up, the key architectural paths are:

1. **`ReadingService.update_status()`** (`src/services/reading_service.py:47-125`) — handles status transitions, auto-sets `finished_at = today` if missing, creates `finished` journal entry, pushes to Hardcover
2. **`mark_complete_with_sync()`** (`src/services/reading_service.py:127-181`) — comprehensive completion that pushes 100% to all sync clients
3. **Hardcover mapping**: `completed` → HC status 3 (`HC_READ`) via `push_local_status()` in `hardcover_sync_client.py:218-268`
4. **Date sync**: `push_dates_to_hardcover()` in `reading_date_service.py:110-214` — pushes `started_at`/`finished_at` separately
5. **Journal events**: `finished` event with `percentage=1.0` (`reading_repository.py:8`)

For the linked case, a future endpoint could mirror `tbr_bp.py:287-324` (the `/start` endpoint) but call `update_status(abs_id, 'completed')` instead of setting status to `active`.

For the unlinked case, a new lightweight "reading history" model or a TBR status field would be needed.
