"""
Alignment Service.
Handles the core logic for aligning ebook text with audio transcriptions,
cross-format position normalization, and storing results in the database.
"""

import json
import logging
import re
from datetime import datetime

from src.db.models import Book, BookAlignment, Job
from src.utils.logging_utils import sanitize_exception, time_execution
from src.utils.polisher import Polisher

logger = logging.getLogger(__name__)


def normalize_for_cross_format_comparison(book, config, sync_clients, ebook_parser, alignment_service):
    """Normalize positions for cross-format comparison (audiobook vs ebook).

    When syncing between audiobook (ABS) and ebook clients (KoSync, etc.),
    raw percentages are not comparable because:
    - Audiobook % = time position / total duration
    - Ebook % = text position / total text

    These don't correlate linearly. This function converts ebook positions
    to equivalent audiobook timestamps using text-matching, enabling
    accurate comparison of "who is further in the story".

    Args:
        book: Book model instance
        config: dict of {client_name: ServiceState}
        sync_clients: dict of {client_name: SyncClient}
        ebook_parser: EbookUtils instance for epub operations
        alignment_service: AlignmentService instance (or None)

    Returns:
        dict of {client_name: normalized_value} for comparison, or None
    """
    has_abs = 'ABS' in config
    ebook_clients = [k for k in config.keys() if k != 'ABS']
    book_label = book.title or str(book.id)

    if not ebook_clients:
        return None

    if not has_abs:
        # Ebook-only: normalize via character offsets in the shared EPUB
        if not book.ebook_filename or len(ebook_clients) < 2:
            return None
        try:
            book_path = ebook_parser.resolve_book_path(book.ebook_filename)
            full_text, _ = ebook_parser.extract_text_and_map(book_path)
            total_text_len = len(full_text)
        except Exception as e:
            logger.debug(f"'{book_label}' Could not load ebook for normalization: {e}")
            return None
        if not total_text_len:
            return None
        normalized = {}
        for client_name in ebook_clients:
            client = sync_clients.get(client_name)
            if not client:
                continue
            client_state = config[client_name]
            client_pct = client_state.current.get('pct', 0)
            try:
                client_pct = max(0.0, min(1.0, float(client_pct)))
            except (TypeError, ValueError):
                client_pct = 0.0
            try:
                text_snippet = client.get_text_from_current_state(book, client_state)
                if text_snippet:
                    loc = ebook_parser.find_text_location(
                        book.ebook_filename, text_snippet,
                        hint_percentage=client_pct
                    )
                    if loc and loc.match_index is not None:
                        normalized[client_name] = loc.match_index
                        logger.debug(f"'{book_label}' Normalized '{client_name}' {client_pct:.2%} -> char {loc.match_index}")
                        continue
            except Exception as e:
                logger.debug(f"'{book_label}' Text-based normalization failed for '{client_name}': {e}")
            normalized[client_name] = int(client_pct * total_text_len)
            logger.debug(f"'{book_label}' Normalized '{client_name}' {client_pct:.2%} -> char {int(client_pct * total_text_len)} (pct fallback)")
        return normalized if len(normalized) > 1 else None

    # Audio + ebook path
    if not book.transcript_file:
        logger.debug(f"'{book_label}' No transcript available for cross-format normalization")
        return None

    normalized = {}

    abs_state = config['ABS']
    abs_ts = abs_state.current.get('ts', 0)
    normalized['ABS'] = abs_ts

    for client_name in ebook_clients:
        client = sync_clients.get(client_name)
        if not client:
            continue

        client_state = config[client_name]
        client_pct = client_state.current.get('pct', 0)
        try:
            client_pct = max(0.0, min(1.0, float(client_pct)))
        except (TypeError, ValueError):
            client_pct = 0.0

        try:
            book_path = ebook_parser.resolve_book_path(book.ebook_filename)
            full_text, _ = ebook_parser.extract_text_and_map(book_path)
            total_text_len = len(full_text)

            char_offset = int(client_pct * total_text_len)
            txt = full_text[max(0, char_offset - 400):min(total_text_len, char_offset + 400)]

            if not txt:
                logger.debug(f"'{book_label}' Could not get text from '{client_name}' for normalization")
                continue

            if alignment_service:
                ts_for_text = alignment_service.get_time_for_text(
                    book.id,
                    char_offset_hint=char_offset
                )
            else:
                ts_for_text = None

            if ts_for_text is not None:
                normalized[client_name] = ts_for_text
                logger.debug(f"'{book_label}' Normalized '{client_name}' {client_pct:.2%} -> {ts_for_text:.1f}s")
            else:
                logger.debug(f"'{book_label}' Could not find timestamp for '{client_name}' text")
        except Exception as e:
            logger.warning(f"'{book_label}' Cross-format normalization failed for '{client_name}': {sanitize_exception(e)}")

    if len(normalized) > 1:
        return normalized
    return None

class AlignmentService:
    def __init__(self, database_service, polisher: Polisher):
        self.database_service = database_service
        self.polisher = polisher

    def has_alignment(self, book_id: int) -> bool:
        return bool(book_id and self._get_alignment(book_id))

    @time_execution
    def align_and_store(self, book_id: int, raw_segments: list[dict], ebook_text: str, spine_chapters: list[dict] = None, source: str = None):
        """
        Main entry point for "Unified Alignment".

        Steps:
        1. Validate Structure: Ensure we aren't trying to align mismatched content.
           (e.g., if spine_chapters provided, check roughly if segment count matches or text length matches).
        2. Normalize: Use Polisher to clean both raw transcript and ebook text.
        3. Anchor: Run N-Gram alignment to map characters to timestamps.
        4. Rebuild: Fix fragmented sentences in transcript using ebook text as a guide.
        5. Store: Save ONLY the mapping and essential metadata to DB.
        """
        logger.info(f"AlignmentService: Processing book {book_id} (Text: {len(ebook_text)} chars, Segments: {len(raw_segments)})")

        # 1. Validation (Spine Check)
        # Note: This is soft validation. If lengths assume vastly different sizes, warn.
        # Implementation of full spine verification requires mapping chapters to segments.
        # For now, we trust the inputs but log warnings.
        ebook_len = len(ebook_text)
        # Estimate audio text length
        audio_text_rough = " ".join([s['text'] for s in raw_segments])
        audio_len = len(audio_text_rough)

        ratio = audio_len / ebook_len if ebook_len > 0 else 0
        if ratio < 0.5 or ratio > 1.5:
             logger.warning(f"Alignment Size Mismatch: Audio text is {ratio:.2%} of Ebook text size.")

        # 2. Normalize & Rebuild
        # Fix fragmented sentences (Mr. Smith case)
        # We pass ebook_text to help (though rebuild_fragmented_sentences uses simple heuristics currently)
        rebuilt_segments = self.polisher.rebuild_fragmented_sentences(raw_segments, ebook_text)
        logger.info(f"   Rebuilt segments: {len(raw_segments)} -> {len(rebuilt_segments)}")

        # 3. Anchored Alignment
        alignment_map = self._generate_alignment_map(rebuilt_segments, ebook_text)

        if not alignment_map:
            logger.error("   Failed to generate alignment map.")
            return False

        # 4. Store to Database
        self._save_alignment(book_id, alignment_map, source=source)
        return True

    @time_execution
    def align_storyteller_and_store(self, book_id: int, storyteller_chapters: list[dict], ebook_text: str) -> bool:
        """Align using Storyteller's native word-level timing data.

        Converts wordTimeline entries into segments compatible with the existing
        alignment pipeline, then runs the standard N-gram anchoring algorithm.

        Each wordTimeline entry is expected to have 'startTime' (float seconds)
        and 'word' or 'text' (string).
        """
        logger.info(f"AlignmentService: Processing book {book_id} via Storyteller wordTimeline "
                     f"({len(storyteller_chapters)} chapters, {len(ebook_text)} chars)")

        # Build segments from wordTimeline data (~15-second groups)
        SEGMENT_DURATION = 15.0
        segments = []
        current_words = []
        segment_start = 0.0
        last_word_start = 0.0

        for chapter in storyteller_chapters:
            for entry in chapter.get('words', []):
                start_time = entry.get('startTime', 0.0)
                word = entry.get('word') or entry.get('text', '')
                if not word:
                    continue

                if not current_words:
                    segment_start = start_time

                current_words.append(word)
                last_word_start = start_time

                # Close segment when duration exceeds threshold
                if start_time - segment_start >= SEGMENT_DURATION and len(current_words) > 1:
                    segments.append({
                        'start': segment_start,
                        'end': start_time,
                        'text': ' '.join(current_words),
                    })
                    current_words = []

        # Flush remaining words
        if current_words:
            end_time = max(last_word_start, segment_start + 1.0)
            segments.append({
                'start': segment_start,
                'end': end_time,
                'text': ' '.join(current_words),
            })

        if not segments:
            logger.error(f"AlignmentService: No segments produced from wordTimeline for book {book_id}")
            return False

        logger.info(f"   Built {len(segments)} segments from wordTimeline data")

        # Run through standard pipeline
        rebuilt_segments = self.polisher.rebuild_fragmented_sentences(segments, ebook_text)
        alignment_map = self._generate_alignment_map(rebuilt_segments, ebook_text)

        if not alignment_map:
            # Fallback: linear map from total duration
            total_duration = segments[-1]['end']
            alignment_map = [
                {"char": 0, "ts": 0.0},
                {"char": len(ebook_text), "ts": total_duration},
            ]
            logger.warning(f"   N-gram anchoring failed, using linear fallback for book {book_id}")

        self._save_alignment(book_id, alignment_map, source='storyteller')
        return True

    def get_time_for_text(self, book_id: int, char_offset_hint: int = None) -> float | None:
        """Look up a timestamp from the alignment map using a character offset."""
        alignment = self._get_alignment(book_id)
        if not alignment:
            return None

        map_points = alignment
        target_offset = char_offset_hint

        if target_offset is None:
            return None

        # Binary search for the interval [p1, p2] where p1.char <= target <= p2.char
        left = 0
        right = len(map_points) - 1

        if target_offset < map_points[0]['char']:
            return map_points[0]['ts']

        # Detect partial alignment: use second-to-last point as the real
        # data boundary (last point may be a sentinel mapping to epub end)
        real_end = map_points[-1]
        if len(map_points) >= 2:
            penultimate = map_points[-2]
            char_gap = real_end['char'] - penultimate['char']
            ts_gap = real_end['ts'] - penultimate['ts']
            if ts_gap > 0 and char_gap / max(ts_gap, 1) > 1000:
                real_end = penultimate

        if target_offset > real_end['char']:
            logger.warning(f"book {book_id}: Char offset {target_offset} exceeds alignment range "
                           f"(max {real_end['char']}) — alignment may be partial")
            return None

        # Manual binary search to find floor
        floor_idx = 0
        while left <= right:
            mid = (left + right) // 2
            if map_points[mid]['char'] <= target_offset:
                floor_idx = mid
                left = mid + 1
            else:
                right = mid - 1

        p1 = map_points[floor_idx]

        # Ceiling is next point
        if floor_idx + 1 < len(map_points):
            p2 = map_points[floor_idx + 1]
        else:
            return p1['ts']

        # Linear Interpolation
        char_span = p2['char'] - p1['char']
        time_span = p2['ts'] - p1['ts']

        if char_span == 0: return p1['ts']

        ratio = (target_offset - p1['char']) / char_span
        estimated_time = p1['ts'] + (time_span * ratio)

        return float(estimated_time)

    def get_char_for_time(self, book_id: int, timestamp: float) -> int | None:
        """
        Reverse lookup: Find character offset for a given timestamp.
        Returns None if the timestamp is beyond the alignment data range.
        """
        # 1. Fetch Alignment Map
        alignment = self._get_alignment(book_id)
        if not alignment:
            return None

        map_points = alignment
        target_ts = timestamp

        # 2. Binary search for interval
        left = 0
        right = len(map_points) - 1

        if target_ts <= map_points[0]['ts']:
            return int(map_points[0]['char'])

        # Detect partial alignment: use second-to-last point as the real
        # data boundary (last point may be a sentinel mapping to epub end)
        real_end = map_points[-1]
        if len(map_points) >= 2:
            penultimate = map_points[-2]
            char_gap = real_end['char'] - penultimate['char']
            ts_gap = real_end['ts'] - penultimate['ts']
            # If the last point has a disproportionate char jump, it's a sentinel
            if ts_gap > 0 and char_gap / max(ts_gap, 1) > 1000:
                real_end = penultimate

        if target_ts > real_end['ts']:
            # Timestamp is beyond the alignment data — can't determine position
            logger.warning(f"book {book_id}: Timestamp {target_ts:.1f}s exceeds alignment range "
                           f"(max {real_end['ts']:.1f}s) — alignment may be partial")
            return None

        floor_idx = 0
        while left <= right:
            mid = (left + right) // 2
            if map_points[mid]['ts'] <= target_ts:
                floor_idx = mid
                left = mid + 1
            else:
                right = mid - 1

        p1 = map_points[floor_idx]
        if floor_idx + 1 < len(map_points):
            p2 = map_points[floor_idx + 1]
        else:
            return int(p1['char'])

        # 3. Interpolate
        time_span = p2['ts'] - p1['ts']
        char_span = p2['char'] - p1['char']

        if time_span == 0: return int(p1['char'])

        ratio = (target_ts - p1['ts']) / time_span
        estimated_char = p1['char'] + (char_span * ratio)

        return int(estimated_char)

    def _generate_alignment_map(self, segments: list[dict], full_text: str) -> list[dict]:
        """
        Core Anchored Alignment Algorithm (Two-Pass).
        Pass 1: High confidence (N=12) global search.
        Pass 2: Backfill start gap (N=6) if first anchor is late.
        """
        # 1. Tokenize Transcript
        transcript_words = []
        for seg in segments:
            raw_words = seg['text'].split()
            if not raw_words: continue

            duration = seg['end'] - seg['start']
            per_word = duration / len(raw_words)

            for i, w in enumerate(raw_words):
                norm = self.polisher.normalize(w)
                if not norm: continue
                transcript_words.append({
                    "word": norm,
                    "ts": seg['start'] + (i * per_word),
                    "orig_index": len(transcript_words) # Keep track for slicing
                })

        # 2. Tokenize Book
        book_words = []
        for match in re.finditer(r'\S+', full_text):
            raw_w = match.group()
            norm = self.polisher.normalize(raw_w)
            if not norm: continue
            book_words.append({
                "word": norm,
                "char": match.start(),
                "orig_index": len(book_words)
            })

        if not transcript_words or not book_words:
            return []

        # --- Helper for N-Gram Logic ---
        def _find_anchors(t_tokens, b_tokens, n_size):
            # Build N-Grams
            def build_ngrams(items, is_book=False):
                grams = {}
                for i in range(len(items) - n_size + 1):
                    keys = [x['word'] for x in items[i:i+n_size]]
                    key = "_".join(keys)
                    if key not in grams: grams[key] = []
                    # Store entire object to retrieve ts/char/index
                    grams[key].append(items[i])
                return grams

            t_grams = build_ngrams(t_tokens, False)
            b_grams = build_ngrams(b_tokens, True)

            found = []
            for key, t_list in t_grams.items():
                if len(t_list) == 1: # Unique in transcript slice
                    if key in b_grams and len(b_grams[key]) == 1: # Unique in book slice
                        # Safe access using indices
                        b_item = b_grams[key][0]
                        t_item = t_list[0]
                        found.append({
                            "ts": t_item['ts'],
                            "char": b_item['char'],
                            "t_idx": t_item['orig_index'],
                            "b_idx": b_item['orig_index']
                        })
            return found

        # 3. PASS 1: Global Search (N=12)
        anchors = _find_anchors(transcript_words, book_words, n_size=12)

        # Sort by character position
        anchors.sort(key=lambda x: x['char'])

        # Filter Monotonic (Global)
        valid_anchors = []
        if anchors:
            valid_anchors.append(anchors[0])
            for a in anchors[1:]:
                if a['ts'] > valid_anchors[-1]['ts']:
                    valid_anchors.append(a)

        # 4. PASS 2: Backfill Start (N=6) "Work Backwards"
        # If the first anchor is significantly into the book, try to recover the intro.
        # Threshold: First anchor is > 1000 chars in AND > 30 seconds in
        if valid_anchors and valid_anchors[0]['char'] > 1000 and valid_anchors[0]['ts'] > 30.0:
            first = valid_anchors[0]
            logger.info(f"   Late start detected (Char: {first['char']}, TS: {first['ts']:.1f}s) — Attempting backfill")

            # Slice the data: Everything BEFORE the first anchor
            # We use the indices we stored during tokenization
            t_slice = transcript_words[:first['t_idx']]
            b_slice = book_words[:first['b_idx']]

            if t_slice and b_slice:
                # Run with reduced N-Gram (N=6)
                # Lower N is risky globally, but safe in this small constrained window
                early_anchors = _find_anchors(t_slice, b_slice, n_size=6)

                # Filter Early Anchors (Must be monotonic with themselves)
                early_anchors.sort(key=lambda x: x['char'])
                valid_early = []
                if early_anchors:
                    valid_early.append(early_anchors[0])
                    for a in early_anchors[1:]:
                        if a['ts'] > valid_early[-1]['ts']:
                            valid_early.append(a)

                if valid_early:
                    logger.info(f"   Backfill success: Recovered {len(valid_early)} early anchors.")
                    # Prepend to main list
                    valid_anchors = valid_early + valid_anchors



        # 5. Build Final Map
        final_map = []
        if not valid_anchors:
            return []

        # Force 0,0 if still missing (Linear Interpolation fallback)
        if valid_anchors[0]['char'] > 0:
            final_map.append({"char": 0, "ts": 0.0})

        final_map.extend(valid_anchors)

        # Force End
        last = valid_anchors[-1]
        if last['char'] < len(full_text):
            # Safe check for segments
            end_ts = segments[-1]['end'] if segments else last['ts']
            final_map.append({"char": len(full_text), "ts": end_ts})

        logger.info(f"   Anchored Alignment: Found {len(valid_anchors)} anchors (Total).")
        return final_map

    def get_alignment_info(self, book_id: int) -> dict | None:
        """Return summary info about a book's alignment data without loading the full map."""
        with self.database_service.get_session() as session:
            row = session.query(BookAlignment).filter_by(book_id=book_id).first()
            if not row:
                return None

            try:
                data = json.loads(row.alignment_map_json)
            except (json.JSONDecodeError, TypeError):
                return None

            if not isinstance(data, list) or not data:
                return None

            try:
                # Detect sentinel: last point may be an end-of-book marker
                real_end = data[-1]
                if len(data) >= 2:
                    penultimate = data[-2]
                    char_gap = real_end['char'] - penultimate['char']
                    ts_gap = real_end['ts'] - penultimate['ts']
                    if ts_gap > 0 and char_gap / max(ts_gap, 1) > 1000:
                        real_end = penultimate

                return {
                    'num_points': len(data),
                    'max_timestamp': real_end['ts'],
                    'max_char': real_end['char'],
                    'total_chars': data[-1]['char'],
                    'last_updated': row.last_updated,
                    'source': row.source,
                }
            except (KeyError, TypeError, IndexError):
                logger.warning(f"Malformed alignment data for book {book_id}")
                return None

    def delete_alignment(self, book_id: int):
        """Delete alignment data for a book."""
        with self.database_service.get_session() as session:
            session.query(BookAlignment).filter_by(book_id=book_id).delete()
            logger.info(f"Deleted alignment data for book {book_id}")

    def realign_book(self, book_id: int):
        """Atomically delete alignment + jobs and requeue book for re-processing."""
        with self.database_service.get_session() as session:
            session.query(BookAlignment).filter_by(book_id=book_id).delete()
            session.query(Job).filter(Job.book_id == book_id).delete()
            book = session.query(Book).filter_by(id=book_id).first()
            if book:
                book.transcript_file = None
                book.status = 'pending'
            logger.info(f"Re-alignment queued for book {book_id}")

    def _save_alignment(self, book_id: int, alignment_map: list[dict], source: str = None):
        """Upsert alignment to SQLite."""
        if not alignment_map:
            logger.warning(f"Refusing to save empty alignment map for book {book_id}")
            return

        with self.database_service.get_session() as session:
            json_blob = json.dumps(alignment_map)

            # Check exist
            existing = session.query(BookAlignment).filter_by(book_id=book_id).first()
            if existing:
                existing.alignment_map_json = json_blob
                existing.last_updated = datetime.utcnow()
                if source:
                    existing.source = source
            else:
                new_align = BookAlignment(book_id=book_id, alignment_map_json=json_blob, source=source)
                session.add(new_align)

            # Context manager handles commit
            logger.info(f"   Saved alignment for book {book_id} to DB.")

    def _get_alignment(self, book_id: int) -> list[dict] | None:
        with self.database_service.get_session() as session:
            entry = session.query(BookAlignment).filter_by(book_id=book_id).first()
            if not entry:
                logger.debug(f"No alignment row for book {book_id}")
                return None

            try:
                raw = json.loads(entry.alignment_map_json)
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Corrupt alignment JSON for book {book_id}: {e}")
                return None

            # Validate structure: each point must have int 'char' and float 'ts'
            validated = []
            for point in raw:
                if isinstance(point, dict) and 'char' in point and 'ts' in point:
                    try:
                        validated.append({'char': int(point['char']), 'ts': float(point['ts'])})
                    except (ValueError, TypeError):
                        logger.warning(f"Skipping invalid alignment point for book {book_id}: {point}")
                else:
                    logger.warning(f"Skipping malformed alignment point for book {book_id}: {point}")

            if not validated:
                logger.warning(f"Alignment for book {book_id} has no valid points after validation")
                return None
            return validated

    def get_book_duration(self, book_id: int) -> float | None:
        """Get the total duration of the book from its alignment map."""
        alignment = self._get_alignment(book_id)
        if alignment and len(alignment) > 0:
            # The last point in the alignment map should have the max timestamp
            return float(alignment[-1]['ts'])
        return None
