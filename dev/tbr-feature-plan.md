# TBR (To Be Read) List Feature

## Context

Users want a dedicated TBR list to track books they intend to read. This is conceptually different from `not_started` status -- a book can be "not started" without being on the TBR (e.g., a library book the user hasn't gotten to), and a TBR book might not be owned yet (wishlist). The TBR list should optionally sync with Hardcover's "Want to Read" (status_id=1), which currently has no local mapping.

## Data Model: New `TbrItem` Table

A separate table, not a status flag on Book. `Base.metadata.create_all` handles new table creation automatically -- no manual migration needed.

```python
class TbrItem(Base):
    __tablename__ = 'tbr_items'

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(500), nullable=False)
    author = Column(String(500), nullable=True)
    cover_url = Column(String(500), nullable=True)
    notes = Column(Text, nullable=True)
    priority = Column(Integer, default=0)            # for manual ordering
    added_at = Column(DateTime, default=datetime.utcnow)

    # Hardcover link (if sourced from or synced to HC)
    hardcover_book_id = Column(Integer, nullable=True, index=True)
    hardcover_slug = Column(String(255), nullable=True)

    # Link to owned Book (set when matched/acquired)
    book_abs_id = Column(String(255), ForeignKey('books.abs_id', ondelete='SET NULL'), nullable=True, index=True)
    book = relationship("Book")
```

**Key design points:**
- `book_abs_id` is NULL until the user acquires the book (auto-linked by `hardcover_book_id` match or title+author)
- `hardcover_book_id` enables dedup on Hardcover import and bidirectional sync
- `priority` supports drag-to-reorder later (0 = unprioritized)

---

## Phase 1: Model + DB Layer

**File: `src/db/models.py`**
- Add `TbrItem` class (see above)
- Add `tbr_items` relationship on `Book` if needed

**File: `src/db/database_service.py`**
- Add TBR CRUD methods:
  - `get_tbr_items()` -- all items ordered by `added_at` desc
  - `add_tbr_item(title, author, cover_url, hardcover_book_id, hardcover_slug, notes)` -- creates TbrItem, deduplicates by `hardcover_book_id`
  - `delete_tbr_item(item_id)` -- remove from TBR
  - `link_tbr_to_book(item_id, abs_id)` -- set `book_abs_id`
  - `find_tbr_by_hardcover_id(hc_book_id)` -- for dedup

## Phase 2: Hardcover Integration

**File: `src/api/hardcover_client.py`**
- Add `get_want_to_read_books()` method -- fetches `user_books` where `status_id=1` WITH book metadata (title, cached_image, cached_contributors, slug). Note: `get_currently_reading()` (line 1065) already fetches status_id=1 but without book titles/covers -- so we need a new method that joins book details.

**File: `src/sync_clients/hardcover_sync_client.py`**
- Add `'not_started': HC_WANT_TO_READ` to `LOCAL_TO_HC_STATUS` (line 23)
- Add `HC_WANT_TO_READ: 'not_started'` to `HC_TO_LOCAL_STATUS` (line 31)
- This enables bidirectional status mapping for books that ARE tracked (i.e., in the Book table with `not_started`). TBR items that aren't yet tracked stay in the `tbr_items` table only.

**File: `dev/hardcover-sync-flow.md`** -- document new mapping

## Phase 3: API Endpoints

**File: `src/blueprints/reading_bp.py`**

New endpoints:
- `GET /reading/tbr` -- renders TBR tab data (or returns JSON for AJAX)
- `POST /reading/tbr/add` -- add single item
  - Body: `{ title, author, cover_url?, hardcover_book_id?, hardcover_slug?, notes? }`
  - Deduplicates by `hardcover_book_id` if provided
- `POST /reading/tbr/search` -- proxy Hardcover search
  - Body: `{ query: string }`
  - Uses existing `hardcover_client.search_books_with_covers(query)` (line 1008)
  - Returns `[{ book_id, title, author, cached_image, slug }]`
- `POST /reading/tbr/import-hardcover` -- bulk import all HC "Want to Read"
  - Calls new `get_want_to_read_books()`
  - Skips items already in `tbr_items` by `hardcover_book_id`
  - Auto-links to existing Books via `HardcoverDetails.hardcover_book_id` match
  - Returns `{ imported: N, skipped: N }`
- `DELETE /reading/tbr/<item_id>` -- remove item
- `POST /reading/tbr/<item_id>/start` -- create a Book from TBR item, set status to `active`, link TBR item, push to Hardcover

## Phase 4: UI -- New Tab on Reading Log

**File: `templates/reading.html`**

Add a third main tab "Want to Read" alongside "Log" and "Stats" (line 132-133):
```html
<button class="r-main-tab-btn" type="button" id="tab-tbr" role="tab"
    data-main-tab="tbr" aria-selected="false" aria-controls="panel-tbr">Want to Read</button>
```

New panel `panel-tbr` containing:
- TBR item cards in a grid (cover, title, author, date added, notes)
- "Add Book" button that opens a modal with:
  - Search input -- searches Hardcover via `/reading/tbr/search`
  - Results as clickable cover cards
  - Manual entry fallback (title + author fields)
- "Import from Hardcover" button (calls `/reading/tbr/import-hardcover`)
- Each card has:
  - "Remove" action (delete from TBR)
  - "Start Reading" action (creates Book, transitions to active)
  - Linked badge if matched to an owned Book

**File: `static/css/reading.css`**
- TBR card styles (cover-focused grid layout)
- Add-book modal styles
- TBR-specific badges and actions

## Phase 5: Auto-Linking

When new books are matched/added to the library, check if any TBR item matches by `hardcover_book_id`. If so, auto-set `book_abs_id` on the TBR item.

**File: `src/blueprints/books_bp.py`** (or wherever book matching happens)
- After a book is successfully matched, call `database_service.find_tbr_by_hardcover_id()` and link if found

---

## Files to Modify

| File | Changes |
|------|---------|
| `src/db/models.py` | New `TbrItem` model |
| `src/db/database_service.py` | TBR CRUD methods |
| `src/api/hardcover_client.py` | `get_want_to_read_books()` with book metadata |
| `src/sync_clients/hardcover_sync_client.py` | Add `not_started` <-> WTR mapping (2 lines) |
| `src/blueprints/reading_bp.py` | TBR endpoints + tab data |
| `templates/reading.html` | "Want to Read" tab + add-book modal |
| `static/css/reading.css` | TBR card + modal styles |
| `dev/hardcover-sync-flow.md` | Document new mapping |

## Verification

1. **Table creation**: Restart app -- `tbr_items` table created automatically
2. **Search + Add**: Open Reading Log -- Want to Read tab -- Add Book -- search -- click result -- verify it appears in TBR list with cover
3. **Hardcover Import**: Click "Import from Hardcover" -- verify WTR books imported, no duplicates on re-import
4. **Auto-link**: Import a TBR book from HC -- match that same book in PageKeeper -- verify TBR item shows "In Library" badge
5. **Start Reading**: Click "Start Reading" on TBR card -- verify Book created with `active` status
6. **Manual add**: Add a book with just title/author -- verify it appears in TBR
7. **Run tests**: `./run-tests.sh`
