# BookFusion Client Unit Tests Plan

## Overview

Add comprehensive unit tests for `src/api/bookfusion_client.py` — the only file in the BookFusion integration with zero test coverage. The existing `test_bookfusion_routes.py` only tests blueprint routes with mocked clients.

## Test File: `tests/test_bookfusion_client.py`

### Test Classes (75+ tests)

#### 1. `TestBuildMultipart` (12 tests)
Tests the Qt-compatible multipart/form-data builder — the most fragile code in the entire integration.

- `test_single_text_field` — basic text field with correct boundary
- `test_single_file_field` — file field with filename
- `test_multiple_text_fields` — multiple text fields separated correctly
- `test_mixed_text_and_file_fields` — text + file in same body
- `test_no_content_type_on_text_fields` — Qt format omits Content-Type
- `test_no_content_type_on_file_fields` — file parts also get no Content-Type
- `test_crlf_line_endings` — must use \r\n not \n
- `test_closing_boundary` — proper `--boundary--` terminator
- `test_binary_file_data` — all 256 byte values pass through
- `test_empty_text_value` — empty string field handled
- `test_unicode_text_value` — UTF-8 encoding preserved
- `test_deduplication_of_boundaries` — exactly N boundaries for N fields

#### 2. `TestCalibreDigest` (7 tests)
Tests SHA-256 digest matching the Calibre plugin's `calculate_digest`.

- `test_known_digest` — sha256(len + null + content)
- `test_empty_bytes` — edge case for zero-length file
- `test_large_file_chunks` — 100KB file (>64k chunk boundary)
- `test_exactly_64k_boundary` — exactly 65536 bytes
- `test_binary_data` — null bytes and high bytes
- `test_different_data_different_digests` — uniqueness
- `test_same_data_same_digest` — determinism

#### 3. `TestCalibreAuthHeader` (3 tests)
- `test_format` — `Basic base64(key:)`
- `test_empty_key` — `Basic base64(:)`
- `test_key_with_special_chars` — URL-safe chars in key

#### 4. `TestCalibreHeaders` (3 tests)
- `test_required_headers` — User-Agent, Authorization, Accept
- `test_extra_headers_merged` — Content-Type passthrough
- `test_extra_does_not_override_auth` — auth header always from key

#### 5. `TestParseFrontmatterTitle` (8 tests)
- `test_simple_title`, `test_quoted_title`, `test_single_quoted_title`
- `test_multiline_frontmatter`, `test_none_input`, `test_empty_input`
- `test_no_title_field`, `test_title_with_extra_whitespace`

#### 6. `TestParseFrontmatter` (6 tests)
- `test_all_fields` — title, author, tags, series
- `test_none_input`, `test_empty_input`
- `test_authors_plural` — "authors:" vs "author:"
- `test_missing_fields`, `test_quoted_values`

#### 7. `TestParseHighlightDate` (6 tests)
- `test_valid_date` — standard format
- `test_date_with_extra_content` — embedded in highlight markdown
- `test_no_date`, `test_invalid_date_format`, `test_empty_string`
- `test_extra_whitespace`

#### 8. `TestParseHighlightQuote` (6 tests)
- `test_single_quote`, `test_multiple_quote_lines`
- `test_mixed_content` — quote among other markdown
- `test_no_quote`, `test_empty_quote_line`, `test_empty_string`

#### 9. `TestBookFusionClientConfig` (6 tests)
- `test_is_configured_false_when_disabled` — BOOKFUSION_ENABLED=false
- `test_is_configured_true_with_highlights_key`
- `test_is_configured_true_with_upload_key`
- `test_is_configured_true_with_both_keys`
- `test_is_configured_ignores_enabled_false_with_keys`
- `test_is_configured_no_env_vars`

#### 10. `TestBookFusionClientConnection` (6 tests)
- `test_check_connection_success` — HTTP 200
- `test_check_connection_http_error` — HTTP 401
- `test_check_connection_no_key` — missing key
- `test_check_connection_network_error` — ConnectionError
- `test_check_upload_connection_success`
- `test_check_upload_connection_no_key`

#### 11. `TestBookFusionClientUpload` (7 tests)
Full 3-step upload flow coverage:

- `test_upload_book_no_api_key` — early return None
- `test_upload_book_already_exists` — check_exists returns data, skips upload
- `test_upload_book_init_fails` — POST /uploads/init returns 500
- `test_upload_book_init_success_no_s3_url` — init returns null URL
- `test_upload_book_full_flow` — init → S3 → finalize, all 200s
- `test_upload_book_s3_upload_fails` — S3 returns 500
- `test_upload_book_finalize_fails` — finalize returns 400

#### 12. `TestBookFusionClientLibrary` (5 tests)
- `test_fetch_library_no_api_key` — returns []
- `test_fetch_library_single_page` — <100 books, single request
- `test_fetch_library_pagination` — 101 books, 2 requests
- `test_fetch_library_http_error` — returns []
- `test_fetch_library_network_error` — returns []

#### 13. `TestBookFusionClientHighlights` (8 tests)
- `test_fetch_highlights_no_key_raises` — ValueError
- `test_fetch_highlights_success` — proper response structure
- `test_fetch_highlights_http_error` — raises HTTPError
- `test_sync_all_highlights_basic` — single page, saves highlights + books
- `test_sync_all_highlights_pagination` — multi-page sync
- `test_sync_all_highlights_pagination_loop_detection` — stops on same cursor
- `test_sync_all_highlights_skips_non_book_pages` — filters by type
- `test_sync_all_highlights_library_fetch_failure` — sync succeeds even if library fetch fails

#### 14. `TestBookFusionClientCheckExists` (4 tests)
- `test_no_api_key_returns_none`
- `test_existing_book` — HTTP 200 with data
- `test_nonexistent_book` — HTTP 404
- `test_network_error_returns_none`

## Testing Approach

- **Real client instances**: Uses `BookFusionClient()` directly, not Mock, so actual logic is exercised
- **Mocked network**: `requests.Session` patched at class level
- **Env var isolation**: `patch.dict(os.environ, ...)` for each test
- **Follows project patterns**: Matches `test_grimmory_client.py` and `test_storyteller_api_client.py` conventions

## What This Catches

1. Multipart encoding regressions that would break all book uploads
2. Digest calculation errors causing false "already exists" matches
3. Auth header format changes
4. Frontmatter parsing edge cases (quoted titles, missing fields)
5. Highlight date/quote extraction failures
6. Pagination loops in sync
7. All error paths in the 3-step upload flow
8. Network failure resilience
