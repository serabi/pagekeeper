import json
import logging
import os
import re
import time
from pathlib import Path

import requests

from src.sync_clients.sync_client_interface import LocatorResult

logger = logging.getLogger(__name__)


class StorytellerAPIClient:
    def __init__(self):
        self._book_cache: dict[str, dict] = {}
        self._cache_timestamp = 0
        self._token = None
        self._token_timestamp = 0
        self._token_max_age = 30
        self._connection_retry_after = 0.0
        self._failure_signature: str | None = None
        self._failure_logged_at = 0.0
        self._suppressed_failure_count = 0
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    @property
    def base_url(self) -> str:
        raw_url = os.environ.get("STORYTELLER_API_URL", "http://localhost:8001").rstrip("/")
        if raw_url and not raw_url.lower().startswith(("http://", "https://")):
            raw_url = f"http://{raw_url}"
        return raw_url

    @property
    def username(self) -> str | None:
        return os.environ.get("STORYTELLER_USER")

    @property
    def password(self) -> str | None:
        return os.environ.get("STORYTELLER_PASSWORD")

    def clear_cache(self):
        """Call at start of each sync cycle to refresh."""
        self._book_cache = {}

    @property
    def _failure_cooldown_seconds(self) -> int:
        try:
            return max(1, int(os.environ.get("STORYTELLER_RETRY_COOLDOWN_SECONDS", "60")))
        except ValueError:
            return 60

    def is_configured(self):
        enabled_val = os.environ.get("STORYTELLER_ENABLED", "").lower()
        if enabled_val == "false":
            return False
        return bool(self.username and self.password)

    def _connection_backoff_active(self) -> bool:
        return time.time() < self._connection_retry_after

    def _mark_connection_success(self) -> None:
        self._connection_retry_after = 0.0
        self._failure_signature = None
        self._failure_logged_at = 0.0
        self._suppressed_failure_count = 0

    def _mark_connection_failure(self, message: str) -> None:
        now = time.time()
        if message == self._failure_signature and now < (self._failure_logged_at + self._failure_cooldown_seconds):
            self._suppressed_failure_count += 1
        else:
            suffix = ""
            if message == self._failure_signature and self._suppressed_failure_count:
                suffix = f" (suppressed {self._suppressed_failure_count} similar errors)"
            logger.error(f"{message}{suffix}")
            self._failure_signature = message
            self._failure_logged_at = now
            self._suppressed_failure_count = 0
        self._connection_retry_after = now + self._failure_cooldown_seconds

    def _get_fresh_token(self) -> str | None:
        if self._token and (time.time() - self._token_timestamp) < self._token_max_age:
            return self._token
        if not self.username or not self.password:
            return None
        if self._connection_backoff_active():
            return None
        try:
            response = self.session.post(
                f"{self.base_url}/api/token",
                data={"username": self.username, "password": self.password},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10,
            )
            if response.status_code == 200:
                data = response.json()
                self._token = data.get("access_token")
                self._token_timestamp = time.time()
                self._mark_connection_success()
                return self._token
            self._mark_connection_failure(f"Storyteller login failed: HTTP {response.status_code}")
        except Exception as e:
            self._mark_connection_failure(f"Storyteller login error: {e}")
        return None

    def _make_request(self, method: str, endpoint: str, json_data: dict = None) -> requests.Response | None:
        token = self._get_fresh_token()
        if not token:
            return None
        if self._connection_backoff_active():
            return None
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        try:
            url = f"{self.base_url}{endpoint}"
            response = self.session.request(method, url, headers=headers, json=json_data, timeout=10)
            if response.status_code == 401:
                self._token = None
                token = self._get_fresh_token()
                if not token:
                    return None
                headers["Authorization"] = f"Bearer {token}"
                response = self.session.request(method, url, headers=headers, json=json_data, timeout=10)
            self._mark_connection_success()
            return response
        except Exception as e:
            self._mark_connection_failure(f"Storyteller API request failed ('{method}' '{endpoint}'): {e}")
            return None

    def check_connection(self) -> bool:
        return bool(self._get_fresh_token())

    def _refresh_book_cache(self) -> bool:
        response = self._make_request("GET", "/api/v2/books")
        if response and response.status_code == 200:
            books = response.json()
            self._book_cache = {}
            for book in books:
                title = book.get("title", "").lower()
                self._book_cache[title] = {"id": book.get("id"), "uuid": book.get("uuid"), "title": book.get("title")}
            self._cache_timestamp = time.time()
            return True
        return False

    def get_position_details(self, book_uuid: str) -> tuple[float | None, int | None, str | None, str | None]:
        """
        Returns: (percentage, timestamp, href, fragment_id)
        """
        response = self._make_request("GET", f"/api/v2/books/{book_uuid}/positions")
        if response is None:
            raise ConnectionError("Storyteller positions request unavailable")
        if response and response.status_code == 200:
            data = response.json()
            locator = data.get("locator", {})
            locations = locator.get("locations", {})

            pct = float(locations.get("totalProgression", 0))
            ts = int(data.get("timestamp", 0))

            # --- EXTRACT PRECISION DATA ---
            href = locator.get("href")  # e.g. "OEBPS/Text/part0000.html"
            fragment = None
            if locations.get("fragments") and len(locations["fragments"]) > 0:
                fragment = locations["fragments"][0]  # e.g. "id628-sentence94"

            return pct, ts, href, fragment

        if response.status_code == 404:
            return None, None, None, None

        raise RuntimeError(f"Storyteller positions request failed: HTTP {response.status_code}")

    def get_all_positions_bulk(self) -> dict:
        """Fetch all book positions in one pass. Returns {title_lower: {pct, ts, href, frag, uuid}}"""
        if not self._book_cache:
            self._refresh_book_cache()

        positions = {}
        for title, book in self._book_cache.items():
            uuid = book.get("uuid")
            if not uuid:
                continue
            pct, ts, href, frag = self.get_position_details(uuid)
            if pct is not None:
                positions[title.lower()] = {"pct": pct, "ts": ts, "href": href, "frag": frag, "uuid": uuid}
        return positions

    def update_position(self, book_uuid: str, percentage: float, rich_locator: LocatorResult = None) -> bool:
        new_ts = int(time.time() * 1000)

        # Base Payload with UUID (critical)
        payload = {
            "uuid": book_uuid,
            "timestamp": new_ts,
            "locator": {
                "href": "",
                "type": "application/xhtml+xml",
                "locations": {"totalProgression": float(percentage)},
            },
        }

        if rich_locator:
            # 1. Href
            if rich_locator.href:
                payload["locator"]["href"] = rich_locator.href

            # 2. CSS Selector
            if rich_locator.css_selector:
                payload["locator"]["locations"]["cssSelector"] = rich_locator.css_selector

            # 3. Fragments (List)
            if rich_locator.fragment:
                payload["locator"]["locations"]["fragments"] = [rich_locator.fragment]
            elif rich_locator.fragments:  # Check if list already populated (future proof)
                payload["locator"]["locations"]["fragments"] = rich_locator.fragments

            # 4. Chapter Progress (Critical for Storyteller)
            if rich_locator.chapter_progress is not None:
                payload["locator"]["locations"]["progression"] = rich_locator.chapter_progress
            else:
                # Fallback: if we don't have chapter progress, maybe default to 0 or omit?
                # Storyteller logs show it as distinct.
                # If we omit, it might calculate it?
                # For now, let's leave it out if None to avoid sending null.
                pass

            # 5. Position (Global Integer)
            if rich_locator.match_index is not None:
                payload["locator"]["locations"]["position"] = rich_locator.match_index

            # 6. CFI
            if rich_locator.cfi:
                payload["locator"]["locations"]["cfi"] = rich_locator.cfi

        else:
            # Fallback for simple percentage update (legacy)
            try:
                r = self._make_request("GET", f"/api/v2/books/{book_uuid}/positions")
                if r and r.status_code == 200:
                    old = r.json().get("locator", {})
                    if old.get("href"):
                        payload["locator"]["href"] = old["href"]
                    if old.get("type"):
                        payload["locator"]["type"] = old["type"]
            except Exception as e:
                logger.debug(f"Failed to fetch existing locator for fallback: {e}")

        response = self._make_request("POST", f"/api/v2/books/{book_uuid}/positions", payload)

        if response:
            if response.status_code == 204:
                logger.info(f"Storyteller API: {book_uuid[:8]}... -> {percentage:.1%} (TS: {new_ts})")
                return True
            elif response.status_code == 409:
                logger.warning(
                    f"Storyteller rejected update for '{book_uuid[:8]}...': Timestamp older than server state (Ignored)"
                )
                return True  # Treat as 'handled' to prevent retry loops
            else:
                logger.warning(f"Storyteller API error: {response.status_code} - {response.text[:100]}")

        return False

    def trigger_processing(self, book_uuid: str) -> bool:
        """Trigger Storyteller to start processing a book (alignment/transcription).

        Must be called after files are placed in the import directory and
        Storyteller has detected them. Without this call, books sit idle.
        """
        response = self._make_request("POST", f"/api/v2/books/{book_uuid}/process")
        if response and response.status_code == 204:
            logger.info(f"Storyteller: triggered processing for {book_uuid[:8]}...")
            return True
        status = response.status_code if response else "no response"
        logger.warning(f"Storyteller: failed to trigger processing for {book_uuid[:8]}... (status: {status})")
        return False

    def search_books(self, query: str) -> list:
        """Search for books in Storyteller."""
        response = self._make_request("GET", "/api/v2/books", None)
        if response and response.status_code == 200:
            all_books = response.json()
            stopwords = {"the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "is"}
            query_lower = query.lower()
            query_tokens = [w for w in re.split(r"\W+", query_lower) if w and w not in stopwords]

            if not query_tokens:
                return []

            query_set = set(query_tokens)
            results = []
            for book in all_books:
                title = book.get("title", "")
                author_names = " ".join(a.get("name", "") for a in book.get("authors", []))
                searchable = f"{title} {author_names}".lower()

                if len(query_tokens) == 1:
                    matched = query_tokens[0] in searchable
                    overlap = 1 if matched else 0
                else:
                    searchable_tokens = set(w for w in re.split(r"\W+", searchable) if w and w not in stopwords)
                    overlap = len(query_set & searchable_tokens)
                    min_size = min(len(query_set), len(searchable_tokens))
                    if min_size <= 1:
                        matched = overlap >= 1
                    else:
                        matched = overlap >= max(round(min_size * 0.67), 2)

                if matched:
                    overlap_ratio = overlap / max(len(query_set), 1)
                    results.append(
                        (
                            overlap_ratio,
                            {
                                "uuid": book.get("uuid") or book.get("id"),
                                "title": title,
                                "authors": [a.get("name") for a in book.get("authors", [])],
                                "cover_url": f"/api/v2/books/{book.get('uuid') or book.get('id')}/cover",
                            },
                        )
                    )
            results.sort(key=lambda x: x[0], reverse=True)
            return [book for _, book in results]
        return []

    def get_book_details(self, book_uuid: str) -> dict | None:
        """Fetch full book details from Storyteller API."""
        try:
            response = self._make_request("GET", f"/api/v2/books/{book_uuid}")
            if response and response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.error(f"Error fetching book details: {e}")
        return None

    def get_word_timeline_chapters(self, book_uuid: str, title_hint: str = None) -> list[dict] | None:
        """Load wordTimeline data from Storyteller's assets directory.

        Storyteller organizes assets by book title (with optional suffix for
        duplicates), not by UUID. This method fetches the book's title via
        the API, then looks for transcript files in:
            {STORYTELLER_ASSETS_DIR}/assets/{title}{suffix}/transcriptions/

        If the API is unavailable and title_hint is provided, falls back to
        globbing for directories matching the title prefix (handles Storyteller's
        deduplication suffix like "[Ru93Xoc2]").

        Accepts any 5-digit prefix chapter files (e.g. 00000-00001.json).
        Returns a list of chapter dicts with 'words' entries, or None if unavailable.
        """
        import glob as glob_module

        assets_dir = os.environ.get("STORYTELLER_ASSETS_DIR", "").strip()
        if not assets_dir:
            return None

        assets_root = (Path(assets_dir) / "assets").resolve()

        # Resolve book title via API to find the correct directory name
        book_details = self.get_book_details(book_uuid)
        if book_details:
            title = book_details.get("title", "")
            suffix = book_details.get("suffix", "")
            if not title:
                return None

            dir_name = f"{title}{suffix}"
            transcripts_dir = (assets_root / dir_name / "transcriptions").resolve()
            if assets_root not in transcripts_dir.parents:
                logger.warning("Storyteller: Refusing out-of-root transcript path")
                return None
            if transcripts_dir.is_dir():
                return self._scan_transcription_dir(transcripts_dir, assets_root)

        # Fallback: API failed or dir not found — try globbing by title hint
        if title_hint:
            escaped = glob_module.escape(title_hint)
            candidates = [
                p
                for p in assets_root.glob(f"{escaped}*/transcriptions")
                if p.is_dir() and assets_root in p.resolve().parents
            ]
            if candidates:
                if len(candidates) > 1:
                    logger.warning(f"Storyteller: {len(candidates)} glob matches for '{title_hint}*', using first")
                result = self._scan_transcription_dir(candidates[0].resolve(), assets_root)
                if result:
                    logger.info(f"Storyteller: filesystem fallback found transcriptions at {candidates[0].parent.name}")
                    return result

        if not book_details:
            logger.debug(f"Storyteller: Could not fetch details for UUID {book_uuid}")
        else:
            logger.debug(f"Storyteller: No transcriptions dir for '{book_details.get('title', '')}'")
        return None

    @staticmethod
    def _scan_transcription_dir(transcripts_dir: Path, assets_root: Path) -> list[dict] | None:
        """Scan a transcriptions directory for chapter timeline files.

        Returns a list of chapter dicts with 'words' entries, or None if empty.
        """
        chapters = []
        pattern = re.compile(r"^\d{5}-\d{5}\.json$")
        for filename in sorted(os.listdir(transcripts_dir)):
            if not pattern.match(filename):
                continue
            filepath = (transcripts_dir / filename).resolve()
            if (
                transcripts_dir.resolve() not in filepath.parents
                and filepath.parent.resolve() != transcripts_dir.resolve()
            ):
                logger.warning(f"Storyteller: Refusing out-of-root transcript file: {filename}")
                continue
            try:
                with filepath.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                timeline = data.get("wordTimeline") or data.get("timeline")
                if timeline and isinstance(timeline, list):
                    chapters.append(
                        {
                            "filename": filename,
                            "words": timeline,
                        }
                    )
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Storyteller: Failed to read transcript {filename}: {e}")

        return chapters if chapters else None


def create_storyteller_client():
    return StorytellerAPIClient()
