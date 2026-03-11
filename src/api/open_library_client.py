"""Open Library Search API client — free, no authentication required."""

import logging

import requests

logger = logging.getLogger(__name__)


class OpenLibraryClient:
    BASE_URL = "https://openlibrary.org"
    COVER_URL = "https://covers.openlibrary.org"

    def search_books(self, query: str, limit: int = 10) -> list[dict]:
        """Search Open Library for books by title/author.

        Returns normalized results with enrichment fields where available:
        [{title, author, cover_url, isbn, first_publish_year, ol_work_key,
          number_of_pages_median, ratings_average, ratings_count, subject}]
        """
        try:
            resp = requests.get(
                f"{self.BASE_URL}/search.json",
                params={
                    "q": query,
                    "limit": limit,
                    "fields": (
                        "key,title,author_name,cover_i,isbn,first_publish_year,"
                        "number_of_pages_median,ratings_average,ratings_count,subject"
                    ),
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            logger.error("Open Library search failed: %s", e)
            return []

        results = []
        for doc in data.get("docs", []):
            # Build cover URL from cover_i (integer ID)
            cover_i = doc.get("cover_i")
            cover_url = f"{self.COVER_URL}/b/id/{cover_i}-M.jpg" if cover_i else None

            # Pick best ISBN: prefer first ISBN-13, fallback to ISBN-10
            isbn = self._pick_isbn(doc.get("isbn", []))

            # author_name is an array — take first element
            authors = doc.get("author_name", [])
            author = authors[0] if authors else None

            # Subjects: OL returns a list, take first few as genres
            subjects = doc.get("subject") or []
            genres = subjects[:8] if isinstance(subjects, list) else []

            # Rating: OL returns float or None
            raw_rating = doc.get("ratings_average")
            try:
                rating = round(float(raw_rating), 2) if raw_rating else None
            except (TypeError, ValueError):
                rating = None

            results.append({
                "title": doc.get("title", ""),
                "author": author,
                "cover_url": cover_url,
                "isbn": isbn,
                "first_publish_year": doc.get("first_publish_year"),
                "ol_work_key": doc.get("key"),  # e.g. "/works/OL45883W"
                "page_count": doc.get("number_of_pages_median"),
                "rating": rating,
                "ratings_count": doc.get("ratings_count"),
                "genres": genres,
            })

        return results

    def get_work_details(self, ol_work_key: str) -> dict | None:
        """Fetch detailed work data from Open Library for enrichment.

        Args:
            ol_work_key: Work key, e.g. "/works/OL45883W"

        Returns dict with description, subjects, first_publish_year, or None on failure.
        """
        # Normalize key — strip leading slash if present for URL construction
        key = ol_work_key.lstrip("/")
        try:
            resp = requests.get(f"{self.BASE_URL}/{key}.json", timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            logger.error("Open Library work fetch failed for %s: %s", ol_work_key, e)
            return None

        # Description can be a string or {"type": "/type/text", "value": "..."}
        raw_desc = data.get("description")
        if isinstance(raw_desc, dict):
            description = raw_desc.get("value", "")
        elif isinstance(raw_desc, str):
            description = raw_desc
        else:
            description = None

        subjects = data.get("subjects") or []
        if not isinstance(subjects, list):
            subjects = []

        return {
            "description": description,
            "subjects": subjects[:8],
        }

    @staticmethod
    def _pick_isbn(isbn_list: list[str]) -> str | None:
        """Prefer first ISBN-13 (length 13), fallback to first ISBN-10."""
        isbn13 = next((i for i in isbn_list if len(i) == 13), None)
        if isbn13:
            return isbn13
        return isbn_list[0] if isbn_list else None
