# Multi-User Support Plan for PageKeeper

## Context

PageKeeper is currently a single-user app. There is no `User` model, no authentication, no data isolation, most runtime configuration is effectively process-global through `os.environ`, and the DI container is built around singleton clients.

The goal is to support **trusted household-scale multi-user usage**:
- primary target: one household
- acceptable upper bound: a few small households
- not intended to become a general SaaS-style multi-tenant platform

This plan keeps the self-hosted Docker simplicity intact and keeps **SQLite** as the default database.

Key decisions:
- Scope: trusted household / small multi-household only
- Authentication: username/password via Flask-Login with session cookies
- Existing install rollout: temporary `DISABLE_AUTH=true` upgrade bypass
- External service credentials: per-user by default
- External sync/integration clients: per-user runtime instances
- ABS instant sync: per-user tokens, not a shared admin token
- SQLite remains the default persistence layer

---

## Phase -1: Make Schema Changes Safe

**Why:** The current app startup still mixes Alembic migrations with `create_all()` and runtime column patching. That is too risky for the table recreation and key changes required by multi-user support, especially on SQLite.

### Changes

1. Make Alembic the only supported schema migration path for non-empty databases
2. Remove or disable startup schema mutation for existing databases:
   - `Base.metadata.create_all(...)` as a schema safety net
   - runtime "add missing column" behavior
3. Make migration failures fatal at startup instead of non-fatal warnings
4. Keep a fresh-database path only for truly empty databases
5. Document backup expectations before migration-heavy releases

### Notes
- This phase should land before any PK/FK rewrite
- SQLite is still acceptable, but migration discipline needs to be stricter

---

## Phase 0: Decouple Book PK from ABS ID

**Why:** `Book.abs_id` as the primary key prevents multiple users from independently tracking the same ABS item. This also improves the single-user schema.

### Changes

1. Add synthetic `books.id` as the primary key
   - `id` = Integer, autoincrement primary key
   - keep `abs_id` as indexed lookup data

2. Migrate internal references from `books.abs_id` to `books.id`
   - `State.book_id`
   - `Job.book_id`
   - `ReadingJournal.book_id`
   - `HardcoverDetails.book_id`
   - `BookAlignment.book_id`
   - `KosyncDocument.linked_book_id`
   - `BookfusionHighlight.matched_book_id`
   - `BookfusionBook.matched_book_id`

3. Use Alembic batch migrations for SQLite table recreation where required

4. Update repositories and service methods to use `book.id` internally
   - retain `abs_id` for ABS API calls and user-facing route parameters where convenient

5. Update blueprints so routes may still accept `abs_id`, but resolve through the current user-scoped book record internally

### Important correction
`books.abs_id` must **not** remain globally unique once user scoping is introduced. By the end of the multi-user migration, uniqueness must be enforced by `(user_id, abs_id)`, not by `abs_id` alone.

### Risk & Mitigation
- High-risk SQLite phase due to table recreation
- Test against a copy of a real database before release
- Back up database before migration

---

## Phase 1: User Model + Authentication

**Why:** Identity needs to exist before user-scoped data can be enforced.

### Changes

1. Add `User` model:
   - `id`
   - `username` (unique)
   - `password_hash`
   - `display_name`
   - `is_admin`
   - `is_active`
   - `created_at`

2. Add Flask-Login
3. Create auth blueprint:
   - `GET/POST /login`
   - `GET /logout`
   - `GET/POST /setup` for first-run admin creation

4. Initialize `LoginManager` in `src/web_server.py`
5. Protect browser routes with `@login_required`
   - KoSync routes remain separately authenticated

6. Replace ad hoc session admin checks with `current_user`
7. Keep temporary `DISABLE_AUTH=true` for upgrade safety on existing installs

### Notes
This phase can ship while data is still globally shared, but it should be treated as transitional.

---

## Phase 2: User-Scoped Data

**Why:** This is the phase that actually introduces data isolation.

### Changes

1. Add `user_id` to `books`
   - `user_id = ForeignKey('users.id')`
   - add unique constraint on `(user_id, abs_id)`

2. Keep child tables scoped through `book_id`
   - `State`
   - `Job`
   - `ReadingJournal`
   - `HardcoverDetails`
   - `BookAlignment`

3. Add `user_id` to standalone user-owned tables
   - `ReadingGoal`
   - `KosyncDocument`
   - `PendingSuggestion`
   - `BookfusionBook`
   - `BookfusionHighlight`
   - `BookloreBook`

4. Scope Booklore cache data per user
   - Booklore metadata cannot be treated as globally shared if different users connect to different Booklore servers or libraries
   - cached metadata should follow the same user scoping as the Booklore credentials/config that produced it

5. Fix `ReadingGoal` uniqueness model
   - current global uniqueness on `year` must become per-user uniqueness on `(user_id, year)`

6. Review global uniqueness constraints on service-owned tables
   - current global unique fields are incompatible with per-user integration data
   - replace them with user-scoped uniqueness where needed
   - examples to review:
     - `BookloreBook.filename`
     - `BookfusionBook.bookfusion_id`
     - `BookfusionHighlight.highlight_id`

7. Fix `KosyncDocument` identity model
   - current `document_hash` primary key is not safe for multi-user because two users can have the same document hash
   - preferred design:
     - surrogate `id` primary key
     - unique constraint on `(user_id, document_hash)`

8. Backfill existing rows to the initial admin user during migration
   - applies to user-owned tables unless the data can be safely discarded and rebuilt from upstream
   - default backfill targets:
     - `books`
     - `reading_goals`
     - `kosync_documents`
     - `pending_suggestions`
     - `bookfusion_books`
     - `bookfusion_highlights`
     - `booklore_books`

9. Update repositories and `DatabaseService`
   - user-owned reads/writes must accept `user_id`
   - avoid hidden global reads for user-owned records

### Notes
This phase will touch a large part of the codebase because current services and blueprints assume globally unique books.

---

## Phase 3: Per-User Settings and Runtime Config Boundary

**Why:** The current app does not just store settings globally; it also reads runtime behavior directly from `os.environ` across clients, server startup, scheduling, helpers, and KoSync auth. Multi-user support requires a real runtime config boundary.

### Changes

1. Add `UserSetting` model
   - fields: `id`, `user_id`, `key`, `value`
   - unique constraint on `(user_id, key)`

2. Split configuration into two groups

**Global settings**
- app-level public/internal URLs exposed by PageKeeper itself
- logging
- sync intervals
- feature flags
- cache sizes
- job/transcription settings
- filesystem paths

**Per-user settings**
- `ABS_KEY`
- `KOSYNC_SERVER`
- `KOSYNC_USER`
- `KOSYNC_KEY`
- `STORYTELLER_API_URL`
- `STORYTELLER_USER`
- `STORYTELLER_PASSWORD`
- `BOOKLORE_SERVER`
- `BOOKLORE_USER`
- `BOOKLORE_PASSWORD`
- `BOOKLORE_LIBRARY_ID`
- `BOOKLORE_SHELF_NAME`
- `HARDCOVER_TOKEN`
- `BOOKFUSION_API_KEY`
- `BOOKFUSION_UPLOAD_API_KEY`
- `CWA_SERVER`
- `CWA_USERNAME`
- `CWA_PASSWORD`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- similar user-owned credentials and user-specific service preferences

### Enablement rule
External integrations should not rely on one global enabled/disabled switch once multi-user support lands.

Preferred behavior:
- a service is enabled for a user when that user has configured the required settings for it
- optional user-level enablement flags may exist where helpful, but should remain user-scoped
- app-level feature flags may still exist for globally disabling an integration entirely

3. Introduce a config resolver/service
   - lookup order: `UserSetting` -> global `Setting` -> `os.environ`
   - request-time behavior should use resolved config objects, not raw env reads

4. Refactor code that currently reads from `os.environ` directly at runtime
   - API clients
   - sync manager
   - KoSync auth
   - settings/test-connection code
   - listener/scheduler/runtime setup where applicable

5. Keep `os.environ` as bootstrap/default fallback, not as the primary runtime source for user-scoped behavior

### Notes
This is broader than a simple API-client constructor refactor.

### Service-scoping rule
All external integrations should be treated as **per-user by default**:
- ABS
- external KoSync servers
- Storyteller
- Booklore
- Hardcover
- BookFusion
- CWA
- Telegram

Only app-level runtime concerns should remain global:
- Flask app/session infrastructure
- local database access
- scheduler loop
- local cache directories only when contents are derived from the same upstream source for all users
- internal integrated KoSync server process itself

Even when a service could be shared in theory, the plan should assume user-specific credentials, user-specific enablement, and user-specific client instances unless there is a strong reason not to.

### KoSync mode split
The plan should treat KoSync in two different modes:

- **Integrated KoSync server**
  - global PageKeeper infrastructure component
  - still authenticates, resolves, and routes all requests per user

- **External KoSync server integration**
  - per-user endpoint and per-user credentials
  - behaves like any other per-user external integration

### Cache-sharing rule
Service-derived caches should be shared only when the upstream source is actually identical across users.

That means:
- Booklore cache data should be per-user if users can point at different servers or different libraries
- similar service caches should follow the same rule
- only local reusable artifacts that are independent of user identity and upstream account context should remain shared

---

## Phase 4: Per-User Sync

**Why:** Sync is currently built around globally configured singleton clients and one global book set.

### Design
Use a single daemon process that iterates through users sequentially. This preserves a simpler SQLite write profile and matches the small-scale household target.

### Changes

1. Introduce `UserContext`
   - `user_id`
   - resolved credentials/settings for that user

2. Refactor `SyncManager.sync_cycle()`
   - if called without a context, iterate active users
   - run each cycle in a user-scoped context

3. Change credential-bearing clients to per-cycle or factory-built instances
   - ABS
   - KoSync
   - Storyteller
   - Booklore
   - Hardcover
   - BookFusion
   - CWA
   - Telegram-related delivery clients where applicable

4. Keep stateless/shared utilities singleton where safe
   - EPUB parsing
   - polishing
   - SMIL extraction
   - transcription support where user data is not embedded

5. Keep one global sync lock
   - sequential sync is acceptable for the intended scale
   - avoids unnecessary SQLite write contention

### ABS Instant Sync
Because ABS tokens are per-user, instant sync must also become per-user:
- maintain listener/polling context per active user token
- route events to that user’s scoped sync cycle
- deduplicate by `(user_id, abs_id)` rather than global `abs_id`

### Integration rule
All sync/integration execution should run inside a user context. That includes:
- ABS sync
- external KoSync sync
- Storyteller sync
- Booklore sync
- Hardcover updates
- BookFusion import/linking flows
- CWA-backed flows

No integration should assume one global credential set for the whole app except for the internal integrated KoSync server process itself, which remains a global server component while authenticating and routing requests to individual users.

### Notes
This is the largest runtime behavior change in the plan.

---

## Phase 5: Multi-User KoSync

**Why:** KoSync currently authenticates against one global username/key and stores document state globally by hash.

### Changes

1. Resolve KoSync credentials against per-user settings
2. Identify which PageKeeper user owns the incoming KoSync credentials
3. Attach resolved `user_id` to request context
4. Scope KoSync lookups and writes by `user_id`
   - document retrieval
   - linking
   - sibling-hash resolution
   - progress updates
5. Change in-memory debounce and tracking keys from global `abs_id` to `(user_id, abs_id)`

### Notes
This phase depends on both Phase 2 and Phase 3.

---

## Phase 6: File Isolation + Admin Tools

**Why:** Some generated files and caches are currently treated globally, and admin/user management flows do not exist yet.

### Changes

1. Move user-specific generated artifacts into per-user namespaces where privacy or collision risk exists
   - transcripts
   - user-specific sync/cache artifacts
   - similar derived files tied to a user’s library state

2. Keep shared reusable caches global where safe
   - parsed EPUB cache
   - only caches derived from a truly common upstream source

3. Add admin user management UI
   - create users
   - reset passwords
   - toggle active status

4. Add user profile UI
   - change password
   - update display name

### Notes
Do not force every cache into per-user directories unless the file contents are truly user-specific.

---

## SQLite Decision

SQLite remains the correct default for this plan because:
- target scale is small and trusted
- deployment model is self-hosted and single-instance
- the app already uses SQLite successfully
- sequential user-scoped sync is compatible with SQLite

SQLite should be reconsidered only if PageKeeper later needs:
- multiple application replicas
- high write concurrency across processes
- a broader multi-tenant deployment model

---

## Phase Summary

| Phase | Description | Depends On | Relative Effort |
|-------|------------|-----------|----------------|
| -1 | Migration safety / remove startup schema mutation | None | Medium |
| 0 | Decouple Book PK from ABS ID | -1 | Large |
| 1 | User model + auth | -1 | Medium |
| 2 | User-scoped data | 0 + 1 | Large |
| 3 | Per-user settings + runtime config boundary | 1 + 2 | Large |
| 4 | Per-user sync | 2 + 3 | Large |
| 5 | Multi-user KoSync | 2 + 3 | Medium |
| 6 | File isolation + admin tools | 2 | Small-Medium |
