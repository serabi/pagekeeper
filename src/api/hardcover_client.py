"""
Hardcover.app GraphQL API Client

Handles book tracking, progress updates, and reading dates for Hardcover.app integration.

Key features:
- Auto-sets started_at when creating a new read
- Auto-sets finished_at when marking as finished (>99% progress)
- Supports ISBN and title/author search for book matching


"""

import logging
import os
import threading
import time
from datetime import date

from src.api.http_client_base import JsonHttpClientBase
from src.utils.string_utils import calculate_similarity, clean_book_title

logger = logging.getLogger(__name__)


class HardcoverClient(JsonHttpClientBase):
    def __init__(self):
        self.api_url = "https://api.hardcover.app/v1/graphql"
        self.user_id = None

        # Rate limiter: 1 req/sec gives comfortable headroom under 60 req/min limit
        self._last_request_time = 0.0
        self._rate_lock = threading.Lock()
        self._min_interval = 1.0

    @property
    def token(self) -> str | None:
        raw = os.environ.get("HARDCOVER_TOKEN", "").strip()
        if raw.lower().startswith("bearer "):
            raw = raw[7:].strip()
        return raw or None

    @property
    def headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "PageKeeper/1.0",
        }

    def is_configured(self):
        enabled_val = os.environ.get("HARDCOVER_ENABLED", "").lower()
        if enabled_val == "false":
            return False
        return bool(self.token)

    def check_connection(self):
        """Test connection to Hardcover API by trying to get user ID."""
        if not self.is_configured():
            raise Exception("Hardcover not configured - HARDCOVER_TOKEN not set")

        user_id = self.get_user_id()
        if not user_id:
            raise Exception("Failed to fetch user ID from Hardcover API")

        logger.info(f"Hardcover client connection verified, user id: {user_id}")
        return True

    def _rate_limit(self):
        """Enforce minimum interval between API requests."""
        wait = 0.0
        with self._rate_lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self._min_interval:
                wait = self._min_interval - elapsed
                self._last_request_time = now + wait
            else:
                self._last_request_time = now
        if wait > 0:
            time.sleep(wait)

    def query(self, query: str, variables: dict | None = None) -> dict | None:
        if not self.token:
            return None

        self._rate_limit()

        try:
            payload = {"query": query, "variables": variables or {}}

            def _mark_retry(_attempt, _response):
                with self._rate_lock:
                    self._last_request_time = time.monotonic()

            r = self.post_json_with_retries(
                self.api_url,
                json_body=payload,
                headers=self.headers,
                timeout=20,
                max_retries=3,
                retry_statuses={429},
                backoff_seconds=5,
                retry_label="Hardcover request",
                on_retry=_mark_retry,
            )

            if r.status_code == 429:
                logger.error("Hardcover rate limit persisted after 3 retries, giving up")
                return None

            if r.status_code == 200:
                data = r.json()
                if data.get("data"):
                    return data["data"]
                elif data.get("errors"):
                    logger.error(f"GraphQL errors: {data['errors']}")
            else:
                logger.error(f"HTTP {r.status_code}: {r.text}")
        except Exception as e:
            logger.error(f"Hardcover query failed: {e}")

        return None

    def get_user_id(self) -> int | None:
        if self.user_id:
            return self.user_id

        result = self.query("{ me { id } }")
        if result and result.get("me"):
            self.user_id = result["me"][0]["id"]
        return self.user_id

    def get_user_book(self, book_id):
        """Fetch the user's specific entry (UserBook) for a generic book_id."""
        # FIX: Prevent crash if book_id is None
        if not book_id:
            return None

        # Ensure we query for the current user as well as the book
        user_id = self.get_user_id()
        if not user_id:
            return None

        query = """
        query GetUserBook($book_id: Int!, $user_id: Int!) {
            user_books(where: {book_id: {_eq: $book_id}, user_id: {_eq: $user_id}}, distinct_on: book_id) {
                id
                status_id
            }
        }
        """
        try:
            response = self.query(query, {"book_id": int(book_id), "user_id": int(user_id)})

            if response and "user_books" in response:
                books = response["user_books"]
                if books:
                    return books[0]

        except Exception as e:
            logger.error(f"Error fetching user book: {e}")

        return None

    def _extract_cover_url(self, cached_image) -> str | None:
        """Extract a cover image URL from the cached_image jsonb field.
        The field is a JSON object like {"url": "https://...", "color": "#hex"} or similar.
        """
        if not cached_image:
            return None
        if isinstance(cached_image, dict):
            return cached_image.get("url")
        if isinstance(cached_image, str):
            return cached_image
        return None

    def _extract_cover_color(self, cached_image) -> str | None:
        """Extract the dominant color from cached_image (hex string like '#1a2b3c')."""
        if isinstance(cached_image, dict):
            return cached_image.get("color")
        return None

    def _normalize_book(self, book: dict) -> dict:
        """Extract standard book metadata from a Hardcover book object.

        Returns dict with book_id, title, author, cached_image, slug,
        and optional enrichment fields (pages, rating, release_year).
        """
        authors = self._extract_authors_from_cached(book.get("cached_contributors"))

        raw_rating = book.get("rating")
        try:
            parsed_rating = round(float(raw_rating), 2) if raw_rating else None
        except (TypeError, ValueError):
            parsed_rating = None

        return {
            "book_id": book.get("id"),
            "title": book.get("title", ""),
            "author": authors[0] if authors else "",
            "cached_image": self._extract_cover_url(book.get("cached_image")),
            "cover_color": self._extract_cover_color(book.get("cached_image")),
            "slug": book.get("slug"),
            "pages": book.get("pages"),
            "rating": parsed_rating,
            "release_year": book.get("release_year"),
        }

    def _extract_authors_from_cached(self, cached_contributors) -> list[str]:
        """
        Parses the JSON list of contributors from Hardcover API.
        Handles both formats: {'author': {'name': '...'}} or {'name': '...'}
        """
        if not cached_contributors or not isinstance(cached_contributors, list):
            return []

        authors = []
        for item in cached_contributors:
            if not isinstance(item, dict):
                continue

            # Case 1: {'author': {'name': 'Author Name'}}
            if "author" in item and isinstance(item["author"], dict):
                name = item["author"].get("name")
                if name:
                    authors.append(name)
            # Case 2: {'name': 'Author Name'}
            elif "name" in item:
                authors.append(item["name"])

        return authors

    def search_by_isbn(self, isbn: str) -> dict | None:
        """Search by ISBN-13 or ISBN-10."""
        isbn_key = "isbn_13" if len(str(isbn)) == 13 else "isbn_10"

        query = f"""
        query ($isbn: String!) {{
            editions(where: {{ {isbn_key}: {{ _eq: $isbn }} }}) {{
                id
                pages
                book {{
                    id
                    title
                    slug
                    cached_image
                }}
            }}
        }}
        """

        result = self.query(query, {"isbn": str(isbn)})
        if result and result.get("editions") and len(result["editions"]) > 0:
            edition = result["editions"][0]
            return {
                "book_id": edition["book"]["id"],
                "slug": edition["book"].get("slug"),
                "edition_id": edition["id"],
                "pages": edition["pages"],
                "title": edition["book"]["title"],
                "cached_image": self._extract_cover_url(edition["book"].get("cached_image")),
            }
        return None

    def search_by_asin(self, asin: str) -> dict | None:
        """Search by ASIN using the dedicated editions.asin field."""
        query = """
        query ($asin: String!) {
            editions(where: { asin: { _eq: $asin } }) {
                id
                pages
                book {
                    id
                    title
                    slug
                    cached_image
                }
            }
        }
        """

        result = self.query(query, {"asin": str(asin)})
        if result and result.get("editions") and len(result["editions"]) > 0:
            edition = result["editions"][0]
            return {
                "book_id": edition["book"]["id"],
                "slug": edition["book"].get("slug"),
                "edition_id": edition["id"],
                "pages": edition["pages"],
                "title": edition["book"]["title"],
                "cached_image": self._extract_cover_url(edition["book"].get("cached_image")),
            }
        return None

    def search_by_title_author(self, title: str, author: str | None = None) -> dict | None:
        """Search by title and author, returning the best fuzzy match."""
        # Clean the input title for better matching comparison
        clean_input_title = clean_book_title(title)
        clean_input_author = author.lower().strip() if author else ""

        # Construct search query
        search_query = f"{clean_input_title} {author or ''}".strip()

        query = """
        query ($query: String!) {
            search(
                query: $query,
                per_page: 10,
                page: 1,
                query_type: "Book"
            ) {
                ids
            }
        }
        """

        result = self.query(query, {"query": search_query})
        if not result or not result.get("search") or not result["search"].get("ids"):
            return None

        book_ids = result["search"]["ids"]
        if not book_ids:
            return None

        # Fetch details for up to 10 books to compare
        book_query = """
        query ($ids: [Int!]) {
            books(where: { id: { _in: $ids }}) {
                id
                title
                slug
                cached_image
                cached_contributors
            }
        }
        """

        book_result = self.query(book_query, {"ids": book_ids})
        if not book_result or not book_result.get("books"):
            return None

        candidates = book_result["books"]
        best_match = None
        best_score = 0.0

        for book in candidates:
            # Score match
            candidate_title = clean_book_title(book["title"])
            title_score = calculate_similarity(clean_input_title, candidate_title)

            # Author Score
            author_score = 0.0
            if clean_input_author:
                # Get all authors for this book from cached_contributors
                authors = [
                    a.lower().strip() for a in self._extract_authors_from_cached(book.get("cached_contributors"))
                ]
                if authors:
                    # Find best similarity among all authors
                    author_score = max(calculate_similarity(clean_input_author, a) for a in authors)
                else:
                    # If book has no authors and we provided one, penalize?
                    # For now, let's keep it 0.0
                    author_score = 0.0
            else:
                # If no author provided, author matching shouldn't hurt or help disproportionally
                author_score = 1.0

            # Combined Score logic:
            # Title is primary, but author acts as a strong multiplier/filter.
            # If author matches well (>0.8), we trust the match more.
            # If author is way off (<0.4), it's likely a different book with same title.

            if clean_input_author:
                # Weights: 60% Title, 40% Author
                score = (title_score * 0.6) + (author_score * 0.4)

                # Boost if author is an excellent match
                if author_score > 0.9:
                    score += 0.1
            else:
                score = title_score

            logger.debug(
                f"Matches for '{title}' by '{author}': '{book['title']}' (Score: {score:.2f}, Title: {title_score:.2f}, Author: {author_score:.2f})"
            )

            if score > best_score:
                best_score = score
                best_match = book

        # Threshold check
        if best_match and best_score > 0.5:
            logger.info(f"Selected best match: '{best_match['title']}' (Score: {best_score:.2f})")

            edition = self.get_default_edition(best_match["id"])

            return {
                "book_id": best_match["id"],
                "slug": best_match.get("slug"),
                "edition_id": edition.get("id") if edition else None,
                "pages": edition.get("pages") if edition else None,
                "title": best_match["title"],
                "cached_image": self._extract_cover_url(best_match.get("cached_image")),
            }

        return None

    def get_default_edition(self, book_id: int) -> dict | None:
        """Get default edition for a book. Tries ebook, physical, then audiobook."""
        query = """
        query ($bookId: Int!) {
            books_by_pk(id: $bookId) {
                default_ebook_edition {
                    id
                    pages
                }
                default_physical_edition {
                    id
                    pages
                }
                default_audio_edition {
                    id
                    audio_seconds
                }
            }
        }
        """

        result = self.query(query, {"bookId": book_id})
        if result and result.get("books_by_pk"):
            book = result["books_by_pk"]
            if book.get("default_ebook_edition"):
                return book["default_ebook_edition"]
            elif book.get("default_physical_edition"):
                return book["default_physical_edition"]
            elif book.get("default_audio_edition"):
                return book["default_audio_edition"]

        return None

    def get_book_author(self, book_id: int) -> str | None:
        """Fetch the primary author name for a book."""
        query = """
        query ($bookId: Int!) {
            books_by_pk(id: $bookId) {
                cached_contributors
            }
        }
        """
        result = self.query(query, {"bookId": book_id})
        if result and result.get("books_by_pk"):
            cached_contributors = result["books_by_pk"].get("cached_contributors", [])
            authors = self._extract_authors_from_cached(cached_contributors)
            if authors:
                return authors[0]
        return None

    READING_FORMAT_LABELS = {1: "Physical", 2: "Audiobook", 3: "Physical + Audio", 4: "eBook"}

    def get_book_editions(self, book_id: int) -> list:
        """Fetch all editions for a book with format, pages, duration, and year."""
        query = """
        query ($bookId: Int!) {
            editions(where: { book_id: { _eq: $bookId } }) {
                id
                pages
                audio_seconds
                reading_format_id
                edition_format
                physical_format
                release_date
            }
        }
        """
        result = self.query(query, {"bookId": book_id})
        if result and result.get("editions"):
            editions = []
            for ed in result["editions"]:
                format_label = self.READING_FORMAT_LABELS.get(ed.get("reading_format_id"))
                if not format_label:
                    format_label = ed.get("edition_format") or ed.get("physical_format")
                if not format_label:
                    if ed.get("audio_seconds") and ed.get("audio_seconds") > 0:
                        format_label = "Audiobook"
                    elif ed.get("pages") and ed.get("pages") > 0:
                        format_label = "Book"
                    else:
                        format_label = "Unknown"
                elif format_label not in self.READING_FORMAT_LABELS.values():
                    format_lower = format_label.lower()
                    if format_lower == "ebook":
                        format_label = "eBook"
                    else:
                        format_label = format_label.capitalize()
                # Extract year from release_date (format: "YYYY-MM-DD")
                release_date = ed.get("release_date")
                year = int(release_date[:4]) if release_date and len(release_date) >= 4 else None

                editions.append(
                    {
                        "id": ed.get("id"),
                        "format": format_label,
                        "pages": ed.get("pages"),
                        "audio_seconds": ed.get("audio_seconds"),
                        "year": year,
                    }
                )
            return editions
        return []

    def resolve_book_from_input(self, input_str: str) -> dict | None:
        """
        Resolve a Hardcover book from a URL, numeric ID, or slug.
        Returns dict: { 'book_id', 'edition_id', 'pages', 'title' } or None.
        """
        if not input_str:
            return None

        from urllib.parse import urlparse

        s = input_str.strip()
        # If it's a URL, try to extract the last segment of the path
        try:
            parsed = urlparse(s)
            if parsed.scheme and parsed.netloc and parsed.path:
                path = parsed.path.rstrip("/")
                if "/" in path:
                    s = path.split("/")[-1]
                else:
                    s = path
        except Exception:
            pass

        # If it looks numeric, treat as book ID
        book = None
        if s.isdigit():
            try:
                book_id = int(s)
                query = """
                query ($id: Int!) {
                    books_by_pk(id: $id) {
                        id
                        title
                        slug
                        cached_image
                        default_ebook_edition {
                            id
                            pages
                        }
                        default_physical_edition {
                            id
                            pages
                        }
                        default_audio_edition {
                            id
                            audio_seconds
                        }
                    }
                }
                """
                result = self.query(query, {"id": book_id})
                if result and result.get("books_by_pk"):
                    book = result["books_by_pk"]
                else:
                    return None
            except Exception as e:
                logger.error(f"resolve_book_from_input error (id): {e}")
                return None
        else:
            # Treat as slug
            slug = s
            query = """
            query ($slug: String!) {
                books(where: { slug: { _eq: $slug }}, limit: 1) {
                    id
                    title
                    slug
                    cached_image
                    default_ebook_edition {
                        id
                        pages
                    }
                    default_physical_edition {
                        id
                        pages
                    }
                    default_audio_edition {
                        id
                        audio_seconds
                    }
                }
            }
            """
            result = self.query(query, {"slug": slug})
            if result and result.get("books") and len(result["books"]) > 0:
                book = result["books"][0]
            else:
                return None

        edition = None
        audio_seconds = None
        if book.get("default_ebook_edition"):
            edition = book["default_ebook_edition"]
        elif book.get("default_physical_edition"):
            edition = book["default_physical_edition"]
        elif book.get("default_audio_edition"):
            edition = book["default_audio_edition"]
            audio_seconds = edition.get("audio_seconds")

        return {
            "book_id": book.get("id"),
            "slug": book.get("slug"),
            "edition_id": edition.get("id") if edition else None,
            "pages": edition.get("pages") if edition else None,
            "audio_seconds": audio_seconds,
            "title": book.get("title"),
            "cached_image": self._extract_cover_url(book.get("cached_image")),
        }

    def find_user_book(self, book_id: int) -> dict | None:
        """Find existing user_book with read info."""
        query = """
        query ($bookId: Int!, $userId: Int!) {
            user_books(where: { book_id: { _eq: $bookId }, user_id: { _eq: $userId }}, distinct_on: book_id) {
                id
                status_id
                edition_id
                user_book_reads(order_by: {id: desc}, limit: 1) {
                    id
                    started_at
                    finished_at
                    progress_pages
                    progress_seconds
                }
            }
        }
        """

        result = self.query(query, {"bookId": book_id, "userId": self.get_user_id()})
        if result and result.get("user_books") and len(result["user_books"]) > 0:
            return result["user_books"][0]
        return None

    def update_status(self, book_id: int, status_id: int, edition_id: int | None = None) -> dict | None:
        """
        Create/update user_book status.

        Status IDs:
        - 1: Want to Read
        - 2: Currently Reading
        - 3: Read (Finished)
        - 4: Paused
        - 5: Did Not Finish
        """
        query = """
        mutation ($object: UserBookCreateInput!) {
            insert_user_book(object: $object) {
                error
                user_book {
                    id
                    status_id
                    edition_id
                }
            }
        }
        """

        update_args = {
            "book_id": int(book_id),
            "status_id": status_id,
            "privacy_setting_id": 1,
        }

        if edition_id:
            update_args["edition_id"] = int(edition_id)

        result = self.query(query, {"object": update_args})
        if result and result.get("insert_user_book"):
            error = result["insert_user_book"].get("error")
            if error:
                logger.error(f"Hardcover update_status error: {error}")
            return result["insert_user_book"].get("user_book")
        return None

    def update_user_book(self, user_book_id: int, updates: dict) -> dict | None:
        """Update user_book metadata such as rating or status."""
        query = """
        mutation ($id: Int!, $object: UserBookUpdateInput!) {
            update_user_book(id: $id, object: $object) {
                error
                user_book {
                    id
                    rating
                    review
                    review_has_spoilers
                    status_id
                    read_count
                }
            }
        }
        """

        result = self.query(query, {"id": int(user_book_id), "object": updates})
        if result and result.get("update_user_book"):
            error = result["update_user_book"].get("error")
            if error:
                logger.error(f"Hardcover update_user_book error: {error}")
                return None
            return result["update_user_book"].get("user_book")
        return None

    def _get_today_date(self) -> str:
        """Get today's date in YYYY-MM-DD format for Hardcover API."""
        return date.today().isoformat()

    def update_read_dates(self, user_book_read_id: int, started_at: str = None, finished_at: str = None) -> bool:
        """Update only the dates on an existing user_book_read. No progress fields touched."""
        query = """
        mutation UpdateReadDates($id: Int!, $startedAt: date, $finishedAt: date) {
            update_user_book_read(id: $id, object: {
                started_at: $startedAt,
                finished_at: $finishedAt
            }) {
                error
                user_book_read { id started_at finished_at }
            }
        }
        """
        variables = {"id": user_book_read_id, "startedAt": started_at, "finishedAt": finished_at}
        result = self.query(query, variables)
        if result and result.get("update_user_book_read"):
            if result["update_user_book_read"].get("error"):
                logger.error(f"Hardcover update_read_dates error: {result['update_user_book_read']['error']}")
                return False
            return True
        return False

    def create_read_with_dates(
        self, user_book_id: int, started_at: str = None, finished_at: str = None, edition_id: int = None
    ) -> int | None:
        """Create a new user_book_read with dates only (no progress).
        Returns the new read ID, or None on failure."""
        query = """
        mutation InsertUserBookRead($id: Int!, $editionId: Int, $startedAt: date, $finishedAt: date) {
            insert_user_book_read(user_book_id: $id, user_book_read: {
                progress_pages: null,
                progress_seconds: null,
                edition_id: $editionId,
                started_at: $startedAt,
                finished_at: $finishedAt
            }) {
                error
                user_book_read { id }
            }
        }
        """
        variables = {
            "id": user_book_id,
            "editionId": int(edition_id) if edition_id else None,
            "startedAt": started_at,
            "finishedAt": finished_at,
        }
        result = self.query(query, variables)
        if result and result.get("insert_user_book_read"):
            if result["insert_user_book_read"].get("error"):
                logger.error(f"HC create_read_with_dates error: {result['insert_user_book_read']['error']}")
                return None
            read = result["insert_user_book_read"].get("user_book_read")
            return read["id"] if read else None
        return None

    def update_progress(
        self,
        user_book_id: int,
        page: int,
        edition_id: int = None,
        is_finished: bool = False,
        current_percentage: float = 0.0,
        audio_seconds: int = None,
        started_at: str = None,
        finished_at: str = None,
        cached_read_id: int = None,
    ) -> dict | None:
        """
        Update reading progress. Returns {"success": bool, "read_id": int} or None.

        Uses current_percentage > 0.02 (2%) to decide when to set 'started_at'.
        For audiobook editions, pass audio_seconds to use progress_seconds instead of progress_pages.

        Optional started_at/finished_at (YYYY-MM-DD strings) override the default
        of using today's date when filling missing dates on the Hardcover read.

        If cached_read_id is provided, skips the fetch query for existing reads.
        """
        today = self._get_today_date()
        should_start = current_percentage > 0.02

        # Use cached read ID if available, otherwise fetch
        existing_read = None
        if cached_read_id:
            existing_read = {"id": cached_read_id, "started_at": "cached", "finished_at": "cached"}
        else:
            read_query = """
            query ($userBookId: Int!) {
                user_book_reads(where: { user_book_id: { _eq: $userBookId }}, order_by: {id: desc}, limit: 1) {
                    id
                    started_at
                    finished_at
                }
            }
            """
            read_result = self.query(read_query, {"userBookId": user_book_id})
            if read_result and read_result.get("user_book_reads") and len(read_result["user_book_reads"]) > 0:
                existing_read = read_result["user_book_reads"][0]

        if existing_read:
            # --- UPDATE EXISTING READ ---
            read_id = existing_read["id"]

            started_at_val = existing_read.get("started_at")
            finished_at_val = existing_read.get("finished_at")

            # If no start date exists, and we passed 2%, fill it in
            if not started_at_val and should_start:
                started_at_val = started_at or today
                logger.info(f"Hardcover: Setting started_at to '{started_at_val}' (Progress: {current_percentage:.1%})")

            if is_finished and not finished_at_val:
                finished_at_val = finished_at or today
                logger.info(f"Hardcover: Setting finished_at to '{finished_at_val}'")

            # Use progress_seconds for audiobooks, progress_pages for page-based editions
            if audio_seconds and audio_seconds > 0:
                progress_seconds = int(audio_seconds * current_percentage)
                query = """
                mutation UpdateBookProgress($id: Int!, $seconds: Int, $editionId: Int, $startedAt: date, $finishedAt: date) {
                    update_user_book_read(id: $id, object: {
                        progress_seconds: $seconds,
                        progress_pages: null,
                        edition_id: $editionId,
                        started_at: $startedAt,
                        finished_at: $finishedAt
                    }) {
                        error
                        user_book_read { id }
                    }
                }
                """
                variables = {
                    "id": read_id,
                    "seconds": progress_seconds,
                    "editionId": int(edition_id) if edition_id else None,
                    "startedAt": started_at_val,
                    "finishedAt": finished_at_val,
                }
            else:
                query = """
                mutation UpdateBookProgress($id: Int!, $pages: Int, $editionId: Int, $startedAt: date, $finishedAt: date) {
                    update_user_book_read(id: $id, object: {
                        progress_pages: $pages,
                        progress_seconds: null,
                        edition_id: $editionId,
                        started_at: $startedAt,
                        finished_at: $finishedAt
                    }) {
                        error
                        user_book_read { id }
                    }
                }
                """
                variables = {
                    "id": read_id,
                    "pages": page,
                    "editionId": int(edition_id) if edition_id else None,
                    "startedAt": started_at_val,
                    "finishedAt": finished_at_val,
                }

            result = self.query(query, variables)

            if result and result.get("update_user_book_read"):
                if result["update_user_book_read"].get("error"):
                    return None
                return {"success": True, "read_id": read_id}
            return None

        else:
            # --- CREATE NEW READ ---
            # Apply logic to new reads too
            started_at_val = (started_at or today) if should_start else None
            finished_at_val = (finished_at or today) if is_finished else None

            # Use progress_seconds for audiobooks, progress_pages for page-based editions
            if audio_seconds and audio_seconds > 0:
                progress_seconds = int(audio_seconds * current_percentage)
                query = """
                mutation InsertUserBookRead($id: Int!, $seconds: Int, $editionId: Int, $startedAt: date, $finishedAt: date) {
                    insert_user_book_read(user_book_id: $id, user_book_read: {
                        progress_seconds: $seconds,
                        progress_pages: null,
                        edition_id: $editionId,
                        started_at: $startedAt,
                        finished_at: $finishedAt
                    }) {
                        error
                        user_book_read { id }
                    }
                }
                """
                variables = {
                    "id": user_book_id,
                    "seconds": progress_seconds,
                    "editionId": int(edition_id) if edition_id else None,
                    "startedAt": started_at_val,
                    "finishedAt": finished_at_val,
                }
            else:
                query = """
                mutation InsertUserBookRead($id: Int!, $pages: Int, $editionId: Int, $startedAt: date, $finishedAt: date) {
                    insert_user_book_read(user_book_id: $id, user_book_read: {
                        progress_pages: $pages,
                        progress_seconds: null,
                        edition_id: $editionId,
                        started_at: $startedAt,
                        finished_at: $finishedAt
                    }) {
                        error
                        user_book_read { id }
                    }
                }
                """
                variables = {
                    "id": user_book_id,
                    "pages": page,
                    "editionId": int(edition_id) if edition_id else None,
                    "startedAt": started_at_val,
                    "finishedAt": finished_at_val,
                }

            result = self.query(query, variables)

            if result and result.get("insert_user_book_read"):
                if result["insert_user_book_read"].get("error"):
                    return None
                read = result["insert_user_book_read"].get("user_book_read")
                new_read_id = read["id"] if read else None
                return {"success": True, "read_id": new_read_id}
            return None

    def get_book_metadata(self, book_id: int) -> dict | None:
        """Fetch enrichment metadata for a book.

        Returns description, genres, tags, release_year, subtitle, pages, rating, ratings_count.
        """
        query = """
        query ($bookId: Int!) {
            books_by_pk(id: $bookId) {
                description
                cached_tags
                release_year
                subtitle
                pages
                rating
                ratings_count
            }
        }
        """
        result = self.query(query, {"bookId": book_id})
        if not result or not result.get("books_by_pk"):
            return None
        book = result["books_by_pk"]

        def _normalize_tag_name(value):
            if isinstance(value, str):
                return value.strip()
            return ""

        ignored_category_labels = {
            "genre",
            "genres",
            "mood",
            "moods",
            "content warning",
            "content warnings",
            "tag",
            "tags",
        }

        def _normalize_category_label(value):
            normalized = _normalize_tag_name(value).lower()
            singular_map = {
                "genres": "genre",
                "moods": "mood",
                "content warnings": "content warning",
                "tags": "tag",
            }
            return singular_map.get(normalized, normalized)

        def _normalize_category(raw_tag):
            if not isinstance(raw_tag, dict):
                return ""

            category = raw_tag.get("tag_category")
            if isinstance(category, dict):
                return _normalize_category_label(
                    category.get("slug") or category.get("category") or category.get("name") or ""
                )

            if isinstance(category, str):
                return _normalize_category_label(category)

            return _normalize_category_label(
                raw_tag.get("category")
                or raw_tag.get("tag_category_slug")
                or raw_tag.get("tag_category_name")
                or raw_tag.get("type")
                or ""
            )

        genres = []
        tags = []
        source_tags = book.get("cached_tags") or []

        def _append_tag(name, category=""):
            clean_name = _normalize_tag_name(name)
            if not clean_name:
                return
            if clean_name.lower() in ignored_category_labels:
                return
            if _normalize_category_label(category) == "genre":
                genres.append(clean_name)
            else:
                tags.append(clean_name)

        if isinstance(source_tags, dict):
            for raw_category, values in source_tags.items():
                category = _normalize_category_label(raw_category)
                if isinstance(values, list):
                    for value in values:
                        if isinstance(value, dict):
                            _append_tag(
                                value.get("tag") or value.get("name") or value.get("label") or value.get("value"),
                                category,
                            )
                        else:
                            _append_tag(value, category)
                elif isinstance(values, dict):
                    _append_tag(
                        values.get("tag") or values.get("name") or values.get("label") or values.get("value"),
                        category,
                    )
                else:
                    _append_tag(values, category)
        else:
            for t in source_tags:
                if isinstance(t, dict):
                    name = _normalize_tag_name(t.get("tag") or t.get("name") or t.get("label") or t.get("value"))
                    category = _normalize_category(t)
                    if not name:
                        continue
                    if name.strip().lower() in ignored_category_labels:
                        continue
                    if _normalize_category_label(category) == "genre":
                        genres.append(name)
                    else:
                        tags.append(name)
                elif isinstance(t, str):
                    name = t.strip()
                    if name and name.lower() not in ignored_category_labels:
                        tags.append(name)

        genres = list(dict.fromkeys(t for t in genres if t))
        tags = list(dict.fromkeys(t for t in tags if t))

        # Parse rating to float — HC returns it as numeric/string
        raw_rating = book.get("rating")
        try:
            parsed_rating = round(float(raw_rating), 2) if raw_rating else None
        except (TypeError, ValueError):
            parsed_rating = None

        return {
            "description": book.get("description"),
            "genres": genres,
            "tags": tags,
            "release_year": book.get("release_year"),
            "subtitle": book.get("subtitle"),
            "pages": book.get("pages"),
            "rating": parsed_rating,
            "ratings_count": book.get("ratings_count"),
        }

    def search_books_with_covers(self, query_str: str, limit: int = 5) -> list[dict]:
        """Search for books and return results with cover images (for cover picker)."""
        search_query = """
        query ($query: String!, $per_page: Int!) {
            search(
                query: $query,
                per_page: $per_page,
                page: 1,
                query_type: "Book"
            ) {
                ids
            }
        }
        """

        result = self.query(search_query, {"query": query_str, "per_page": max(limit, 10)})
        if not result or not result.get("search") or not result["search"].get("ids"):
            return []

        book_ids = [int(bid) for bid in result["search"]["ids"][:limit]]
        if not book_ids:
            return []

        book_query = """
        query ($ids: [Int!]) {
            books(where: { id: { _in: $ids }}) {
                id
                title
                slug
                cached_image
                cached_contributors
                pages
                rating
                release_year
            }
        }
        """

        book_result = self.query(book_query, {"ids": book_ids})
        if not book_result or not book_result.get("books"):
            return []

        # Create lookup for quick access
        books_by_id = {book["id"]: book for book in book_result["books"]}

        return [self._normalize_book(books_by_id[bid]) for bid in book_ids if bid in books_by_id]

    def get_all_editions(self, book_id: int) -> dict:
        """Fetch all default editions for a book, keyed by format type.

        Returns dict like {'ebook': {...}, 'audio': {...}, 'physical': {...}}.
        """
        query = """
        query ($bookId: Int!) {
            books_by_pk(id: $bookId) {
                default_ebook_edition {
                    id
                    pages
                }
                default_physical_edition {
                    id
                    pages
                }
                default_audio_edition {
                    id
                    audio_seconds
                }
            }
        }
        """
        result = self.query(query, {"bookId": book_id})
        editions = {}
        if result and result.get("books_by_pk"):
            book = result["books_by_pk"]
            if book.get("default_ebook_edition"):
                editions["ebook"] = book["default_ebook_edition"]
            if book.get("default_physical_edition"):
                editions["physical"] = book["default_physical_edition"]
            if book.get("default_audio_edition"):
                editions["audio"] = book["default_audio_edition"]
        return editions

    def get_book_series(self, book_id: int) -> list[dict]:
        """Fetch series info for a book (excluding compilations)."""
        query = """
        query ($bookId: Int!) {
            books_by_pk(id: $bookId) {
                book_series(where: {compilation: {_eq: false}}) {
                    position
                    details
                    series {
                        id
                        name
                        slug
                        books_count
                    }
                }
            }
        }
        """
        result = self.query(query, {"bookId": book_id})
        if not result or not result.get("books_by_pk"):
            return []

        entries = result["books_by_pk"].get("book_series", [])
        return [
            {
                "position": bs.get("position"),
                "details": bs.get("details"),
                "series_id": bs["series"]["id"],
                "series_name": bs["series"]["name"],
                "series_slug": bs["series"].get("slug"),
                "series_books_count": bs["series"].get("books_count"),
            }
            for bs in entries
            if bs.get("series")
        ]

    # ── TBR / Want-to-Read methods ──

    def get_want_to_read_books(self) -> list[dict]:
        """Fetch all user_books with status_id=1 (Want to Read), with book metadata."""
        query = """
        query {
            me {
                user_books(where: {status_id: {_eq: 1}}) {
                    id
                    book_id
                    book {
                        id
                        title
                        slug
                        cached_image
                        cached_contributors
                        pages
                        rating
                        release_year
                    }
                }
            }
        }
        """
        result = self.query(query)
        if not result or not result.get("me"):
            return []

        me = result["me"]
        if isinstance(me, list):
            me = me[0] if me else {}

        results = []
        for ub in me.get("user_books", []):
            book = ub.get("book") or {}
            normalized = self._normalize_book(book)
            # Fallback to user_book's book_id if book object lacks it
            if not normalized["book_id"]:
                normalized["book_id"] = ub.get("book_id")
            results.append(normalized)
        return results

    def get_user_lists(self) -> list[dict]:
        """Fetch all custom lists for the current user."""
        user_id = self.get_user_id()
        if not user_id:
            return []

        query = """
        query ($userId: Int!) {
            lists(
                where: {user_id: {_eq: $userId}},
                order_by: {updated_at: desc}
            ) {
                id
                name
                description
                books_count
                privacy_setting_id
                updated_at
            }
        }
        """
        result = self.query(query, {"userId": user_id})
        if not result or not result.get("lists"):
            return []

        return [
            {
                "id": lst["id"],
                "name": lst.get("name", ""),
                "description": lst.get("description", ""),
                "books_count": lst.get("books_count", 0),
                "privacy_setting_id": lst.get("privacy_setting_id", 3),
                "public": lst.get("privacy_setting_id") == 1,
                "updated_at": lst.get("updated_at"),
            }
            for lst in result["lists"]
        ]

    def get_list_books(self, list_id: int) -> dict | None:
        """Fetch all books in a specific Hardcover list with metadata."""
        query = """
        query ($listId: Int!) {
            lists(where: {id: {_eq: $listId}}) {
                name
                description
                list_books {
                    position
                    date_added
                    book {
                        id
                        title
                        slug
                        cached_image
                        cached_contributors
                        pages
                        rating
                        release_year
                    }
                }
            }
        }
        """
        result = self.query(query, {"listId": list_id})
        lists = result.get("lists") if result else None
        if not lists:
            return None

        lst = lists[0]
        books = []
        for lb in lst.get("list_books", []):
            book = lb.get("book") or {}
            normalized = self._normalize_book(book)
            normalized["position"] = lb.get("position")
            normalized["date_added"] = lb.get("date_added")
            books.append(normalized)

        return {
            "name": lst.get("name", ""),
            "description": lst.get("description", ""),
            "books": books,
        }
