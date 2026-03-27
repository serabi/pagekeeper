"""
Ebook Utilities for PageKeeper

EbookParser is the public facade for all EPUB operations. It owns book
resolution, hashing, text extraction/caching, and cover extraction, and
delegates XPath generation/resolution and text search to focused services.
"""

import glob
import hashlib
import logging
import os
import threading
from collections import OrderedDict
from pathlib import Path

import ebooklib
from bs4 import BeautifulSoup
from ebooklib import epub

from src.utils.koreader_xpath import KoReaderXPathService
from src.utils.locator_search import LocatorSearchService
from src.utils.path_utils import is_safe_path_within

logger = logging.getLogger(__name__)


class LRUCache:
    def __init__(self, capacity: int = 3):
        self.cache = OrderedDict()
        self.capacity = capacity
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            if key not in self.cache:
                return None
            self.cache.move_to_end(key)
            return self.cache[key]

    def put(self, key, value):
        with self._lock:
            if key in self.cache:
                self.cache.move_to_end(key)
            self.cache[key] = value
            while len(self.cache) > self.capacity:
                self.cache.popitem(last=False)

    def clear(self):
        with self._lock:
            self.cache.clear()


class EbookParser:
    def __init__(self, books_dir, epub_cache_dir=None):
        self.books_dir = Path(books_dir)
        self.epub_cache_dir = Path(epub_cache_dir) if epub_cache_dir else Path("/data/epub_cache")

        cache_size = int(os.getenv("EBOOK_CACHE_SIZE", 3))
        self.cache = LRUCache(capacity=cache_size)
        self.hash_method = os.getenv("KOSYNC_HASH_METHOD", "content").lower()
        self.useXpathSegmentFallback = os.getenv("XPATH_FALLBACK_TO_PREVIOUS_SEGMENT", "false").lower() == "true"

        fuzzy_threshold = int(os.getenv("FUZZY_MATCH_THRESHOLD", 80))
        self._ko_xpath = KoReaderXPathService()
        self._locator = LocatorSearchService(fuzzy_threshold=fuzzy_threshold)

        logger.info(
            f"EbookParser initialized (cache={cache_size}, hash={self.hash_method}, xpath_fallback={self.useXpathSegmentFallback})"
        )

    # =========================================================================
    # Book path resolution
    # =========================================================================

    def resolve_book_path(self, filename):
        try:
            safe_name = glob.escape(filename)
            return next(self.books_dir.glob(f"**/{safe_name}"))
        except StopIteration:
            pass

        if self.epub_cache_dir.exists():
            cached_path = self.epub_cache_dir / filename
            if is_safe_path_within(cached_path, self.epub_cache_dir) and cached_path.exists():
                return cached_path

        raise FileNotFoundError(f"Could not locate {filename}")

    # =========================================================================
    # KOReader hashing
    # =========================================================================

    def get_kosync_id(self, filepath):
        filepath = Path(filepath)
        if self.hash_method == "filename":
            return hashlib.md5(filepath.name.encode("utf-8")).hexdigest()

        md5 = hashlib.md5()
        try:
            file_size = os.path.getsize(filepath)
            with open(filepath, "rb") as f:
                for i in range(-1, 11):
                    offset = 0 if i == -1 else 1024 * (4**i)
                    if offset >= file_size:
                        break
                    f.seek(offset)
                    chunk = f.read(1024)
                    if not chunk:
                        break
                    md5.update(chunk)
            return md5.hexdigest()
        except Exception as e:
            logger.error(f"Error computing hash for {filepath}: {e}")
            return None

    def _compute_koreader_hash_from_bytes(self, content):
        md5 = hashlib.md5()
        try:
            file_size = len(content)
            for i in range(-1, 11):
                offset = 0 if i == -1 else 1024 * (4**i)
                if offset >= file_size:
                    break
                chunk = content[offset : offset + 1024]
                if not chunk:
                    break
                md5.update(chunk)
            return md5.hexdigest()
        except Exception as e:
            logger.error(f"Error computing KOReader hash from bytes: {e}")
            return None

    def get_kosync_id_from_bytes(self, filename, content):
        if self.hash_method == "filename":
            return hashlib.md5(filename.encode("utf-8")).hexdigest()
        return self._compute_koreader_hash_from_bytes(content)

    # =========================================================================
    # Cover extraction
    # =========================================================================

    def extract_cover(self, filepath, output_path):
        """Extract cover image from EPUB to output_path. Returns True if successful."""
        try:
            filepath = Path(filepath)
            try:
                book = epub.read_epub(str(filepath))
                cover_item = None

                for item in book.get_items():
                    if item.get_type() == ebooklib.ITEM_IMAGE:
                        if "cover" in item.get_name().lower():
                            cover_item = item
                            break
                    if item.get_type() == ebooklib.ITEM_COVER:
                        cover_item = item
                        break

                if cover_item:
                    with open(output_path, "wb") as f:
                        f.write(cover_item.get_content())
                    logger.debug(f"Extracted cover for {filepath.name}")
                    return True
            except Exception as e:
                logger.debug(f"ebooklib cover extraction failed for {filepath.name}: {e}")

            return False

        except Exception as e:
            logger.error(f"Error extracting cover from '{filepath}': {e}")
            return False

    # =========================================================================
    # Text extraction and caching
    # =========================================================================

    def extract_text_and_map(self, filepath, progress_callback=None):
        """
        Parse EPUB into full text + spine map. Results are cached.
        Uses BeautifulSoup for text extraction.
        """
        filepath = Path(filepath)
        if not filepath.exists():
            filepath = self.resolve_book_path(filepath.name)
        str_path = str(filepath)

        cached = self.cache.get(str_path)
        if cached:
            if progress_callback:
                progress_callback(1.0)
            return cached["text"], cached["map"]

        logger.info(f"Parsing EPUB: {filepath.name}")

        try:
            book = epub.read_epub(str_path)
            full_text_parts = []
            spine_map = []
            current_idx = 0

            total_spine = len(book.spine)

            for i, item_ref in enumerate(book.spine):
                if progress_callback:
                    progress_callback(i / total_spine)

                item = book.get_item_with_id(item_ref[0])
                if item.get_type() == ebooklib.ITEM_DOCUMENT:
                    soup = BeautifulSoup(item.get_content(), "html.parser")
                    text = soup.get_text(separator=" ", strip=True)

                    start = current_idx
                    length = len(text)
                    end = current_idx + length

                    spine_map.append(
                        {
                            "start": start,
                            "end": end,
                            "spine_index": i + 1,
                            "href": item.get_name(),
                            "content": item.get_content(),
                        }
                    )

                    full_text_parts.append(text)
                    current_idx = end + 1

            combined_text = " ".join(full_text_parts)
            self.cache.put(str_path, {"text": combined_text, "map": spine_map})
            return combined_text, spine_map

        except Exception as e:
            logger.error(f"Failed to parse EPUB '{filepath}': {e}")
            return "", []

    def get_text_at_percentage(self, filename, percentage):
        """Get text snippet at a given percentage through the book."""
        try:
            book_path = self.resolve_book_path(filename)
            full_text, spine_map = self.extract_text_and_map(book_path)

            if not full_text:
                return None

            target_pos = int(len(full_text) * percentage)
            start = max(0, target_pos - 400)
            end = min(len(full_text), target_pos + 400)

            return full_text[start:end]
        except Exception as e:
            logger.error(f"Error getting text at percentage: {e}")
            return None

    # =========================================================================
    # Delegated: KOReader XPath generation/resolution
    # =========================================================================

    def get_perfect_ko_xpath(self, filename, position=0) -> str | None:
        try:
            book_path = self.resolve_book_path(filename)
            full_text, spine_map = self.extract_text_and_map(book_path)
            if not full_text or not spine_map:
                return None
            return self._ko_xpath.generate_xpath(full_text, spine_map, position)
        except Exception as e:
            logger.error(f"Error generating KOReader XPath: {e}")
            return None

    def get_sentence_level_ko_xpath(self, filename, percentage) -> str | None:
        try:
            book_path = self.resolve_book_path(filename)
            full_text, spine_map = self.extract_text_and_map(book_path)
            if not full_text:
                return None
            return self._ko_xpath.generate_sentence_level_xpath(full_text, spine_map, percentage)
        except Exception as e:
            logger.error(f"Error generating sentence-level KOReader XPath: {e}")
            return None

    def resolve_xpath(self, filename, xpath_str):
        try:
            book_path = self.resolve_book_path(filename)
            full_text, spine_map = self.extract_text_and_map(book_path)
            if not full_text:
                return None
            return self._ko_xpath.resolve_xpath(full_text, spine_map, xpath_str)
        except Exception as e:
            logger.error(f"Error resolving XPath '{xpath_str}': {e}")
            return None

    # =========================================================================
    # Delegated: Text search and locator resolution
    # =========================================================================

    def find_text_location(self, filename, search_phrase, hint_percentage=None):
        try:
            book_path = self.resolve_book_path(filename)
            full_text, spine_map = self.extract_text_and_map(book_path)
            if not full_text:
                return None
            result = self._locator.find_text_location(full_text, spine_map, search_phrase, hint_percentage)
            # Facade coordinates: fill in perfect_ko_xpath from xpath service
            if result and result.match_index is not None:
                result.perfect_ko_xpath = self._ko_xpath.generate_xpath(full_text, spine_map, result.match_index)
            return result
        except Exception as e:
            logger.error(f"Error finding text in '{filename}': {e}")
            return None

    def resolve_locator_id(self, filename, href, fragment_id):
        try:
            book_path = self.resolve_book_path(filename)
            full_text, spine_map = self.extract_text_and_map(book_path)
            if not full_text:
                return None
            return self._locator.resolve_locator_id(full_text, spine_map, href, fragment_id)
        except Exception as e:
            logger.error(f"Error resolving locator ID '{fragment_id}' in '{filename}': {e}")
            return None

    def get_text_around_cfi(self, filename, cfi, context=50):
        try:
            book_path = self.resolve_book_path(filename)
            full_text, spine_map = self.extract_text_and_map(book_path)
            if not full_text:
                return None
            return self._locator.get_text_around_cfi(full_text, spine_map, cfi, context)
        except Exception as e:
            logger.error(f"Error using epubcfi library for '{cfi}': {e}")
            return None
