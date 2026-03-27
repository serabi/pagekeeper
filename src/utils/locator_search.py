"""
Locator Search and Resolution for PageKeeper.

Finds text positions in EPUBs using anchor/exact/normalized/fuzzy matching,
resolves Readium/Storyteller locators (href + fragment ID), and resolves
EPUB CFIs back to text snippets.
"""

import logging
import re

import epubcfi
import rapidfuzz
from bs4 import BeautifulSoup, Tag
from lxml import html

from src.sync_clients.sync_client_interface import LocatorResult

logger = logging.getLogger(__name__)


class LocatorSearchService:
    def __init__(self, fuzzy_threshold: int = 80):
        self.fuzzy_threshold = fuzzy_threshold

    def find_text_location(self, full_text, spine_map, search_phrase, hint_percentage=None) -> LocatorResult | None:
        """
        Search for text in the EPUB using multiple strategies:
        1. Unique 10-word anchor (avoids ToC duplicates)
        2. Exact match
        3. Normalized match (case/punctuation insensitive)
        4. Fuzzy match (with optional percentage hint for windowed search)

        Returns LocatorResult with perfect_ko_xpath=None (caller fills it in).
        """
        try:
            if not full_text:
                return None
            total_len = len(full_text)

            clean_search = " ".join(search_phrase.split())
            words = clean_search.split()

            match_index = -1

            # 1. Unique anchor: find a 10-word subsequence that appears exactly once
            if len(words) >= 10:
                N = 10
                for i in range(len(words) - N + 1):
                    candidate = " ".join(words[i : i + N])
                    if full_text.count(candidate) == 1:
                        found_idx = full_text.find(candidate)
                        if found_idx != -1:
                            match_index = found_idx
                            logger.info(f"Found unique text anchor: '{candidate[:30]}...' at index {match_index}")
                            break

            # 2. Exact match
            if match_index == -1:
                match_index = full_text.find(search_phrase)

            # 3. Normalized match
            if match_index == -1:
                norm_content = self._normalize(full_text)
                norm_search = self._normalize(search_phrase)
                norm_index = norm_content.find(norm_search)
                if norm_index != -1:
                    match_index = int((norm_index / len(norm_content)) * total_len)

            # 4. Fuzzy match
            if match_index == -1:
                match_index = self._fuzzy_match(full_text, search_phrase, hint_percentage, total_len)

            if match_index == -1:
                return None

            return self._build_locator_result(full_text, spine_map, match_index, total_len)

        except Exception as e:
            logger.error(f"Error finding text: {e}")
            return None

    def resolve_locator_id(self, full_text, spine_map, href, fragment_id) -> str | None:
        """
        Resolve a Storyteller/Readium locator (href + fragment ID) to a text snippet.
        """
        try:
            target_item = None
            for item in spine_map:
                if href in item["href"] or item["href"] in href:
                    target_item = item
                    break

            if not target_item:
                return None

            soup = BeautifulSoup(target_item["content"], "html.parser")
            clean_id = fragment_id.lstrip("#")
            element = soup.find(id=clean_id)

            if not element:
                return None

            current_offset = 0
            found_offset = -1
            all_strings = soup.find_all(string=True)

            for s in all_strings:
                if s.parent == element or element in s.parents:
                    found_offset = current_offset
                    break
                text_len = len(s.strip())
                if text_len == 0:
                    continue
                current_offset += text_len

            if found_offset == -1:
                elem_text = element.get_text(separator=" ", strip=True)
                chapter_text = soup.get_text(separator=" ", strip=True)
                found_offset = chapter_text.find(elem_text)

            if found_offset == -1:
                return None

            global_offset = target_item["start"] + found_offset
            start = max(0, global_offset)
            end = min(len(full_text), global_offset + 500)
            return full_text[start:end]

        except Exception as e:
            logger.error(f"Error resolving locator ID '{fragment_id}': {e}")
            return None

    def get_text_around_cfi(self, full_text, spine_map, cfi, context=50) -> str | None:
        """
        Returns a text fragment of length 2*context centered on the position indicated by the CFI.
        Uses the epubcfi library for precise parsing.
        """
        try:
            parsed_cfi = epubcfi.parse(cfi)

            spine_step = None
            element_steps = []

            for step in parsed_cfi.steps:
                if hasattr(step, "index"):
                    if step.index == 6:
                        continue
                    elif not spine_step and step.index > 6:
                        spine_step = step.index
                    elif isinstance(step, epubcfi.cfi.Step):
                        element_steps.append(step)

            char_offset = parsed_cfi.offset.value if parsed_cfi.offset else 0

            if not spine_step:
                logger.error(f"Could not extract spine step from CFI: '{cfi}'")
                return None

            spine_index = (spine_step // 2) - 1
            if not (0 <= spine_index < len(spine_map)):
                logger.error(f"Spine index {spine_index} out of range for CFI '{cfi}'")
                return None

            item = spine_map[spine_index]

            tree = html.fromstring(item["content"])

            current_element = tree
            text_count = 0

            logger.debug(f"Following CFI path with {len(element_steps)} steps")

            for i, step in enumerate(element_steps):
                if not hasattr(step, "index"):
                    continue

                step_index = step.index
                step_assertion = step.assertion

                logger.debug(f"Step {i}: index={step_index}, assertion={step_assertion}")

                if step_assertion:
                    candidates = current_element.xpath(
                        f".//*[contains(@id, '{step_assertion}') or contains(@class, '{step_assertion}')]"
                    )
                    if candidates:
                        current_element = candidates[0]
                        logger.debug(f"Found element with assertion: {step_assertion}")
                        continue

                if step_index % 2 == 0:
                    element_index = (step_index // 2) - 1
                    children = [child for child in current_element if hasattr(child, "tag")]

                    if 0 <= element_index < len(children):
                        current_element = children[element_index]
                        logger.debug(f"Navigated to child element {element_index}: {current_element.tag}")
                    else:
                        logger.warning(f"Element index {element_index} out of range (have {len(children)} children)")
                        break
                else:
                    text_index = step_index // 2
                    text_nodes = []
                    for child in current_element:
                        if child.text and child.text.strip():
                            text_nodes.append(child.text.strip())
                        if child.tail and child.tail.strip():
                            text_nodes.append(child.tail.strip())

                    if 0 <= text_index < len(text_nodes):
                        text_count += sum(len(text) for text in text_nodes[:text_index])
                        logger.debug(f"Text node {text_index}, accumulated count: {text_count}")
                    break

            if current_element is not None:
                soup = BeautifulSoup(item["content"], "html.parser")
                chapter_text = soup.get_text(separator=" ", strip=True)

                element_text = ""
                if hasattr(current_element, "text_content"):
                    element_text = current_element.text_content()

                if element_text and len(element_text.strip()) > 5:
                    element_start = chapter_text.find(element_text.strip()[:50])
                    if element_start != -1:
                        local_offset = element_start + char_offset
                    else:
                        local_offset = text_count + char_offset
                else:
                    local_offset = text_count + char_offset
            else:
                local_offset = text_count + char_offset

            chapter_text = BeautifulSoup(item["content"], "html.parser").get_text(separator=" ", strip=True)
            local_offset = min(max(0, local_offset), len(chapter_text))

            global_offset = item["start"] + local_offset

            start_pos = max(0, global_offset - context)
            end_pos = min(len(full_text), global_offset + context)

            snippet = full_text[start_pos:end_pos]
            logger.info(f"Snippet extracted: {snippet[:30]}...")
            return snippet

        except Exception as e:
            logger.error(f"Error using epubcfi library for '{cfi}': {e}")
            return None

    # =========================================================================
    # PRIVATE: Search helpers
    # =========================================================================

    def _normalize(self, text):
        return re.sub(r"[^a-z0-9]", "", text.lower())

    def _fuzzy_match(self, full_text, search_phrase, hint_percentage, total_len):
        """Run fuzzy matching, optionally windowed around hint_percentage."""
        cutoff = self.fuzzy_threshold
        match_index = -1

        if hint_percentage is not None:
            w_start = int(max(0, hint_percentage - 0.10) * total_len)
            w_end = int(min(1.0, hint_percentage + 0.10) * total_len)
            alignment = rapidfuzz.fuzz.partial_ratio_alignment(
                search_phrase, full_text[w_start:w_end], score_cutoff=cutoff
            )
            if alignment:
                match_index = w_start + alignment.dest_start

        if match_index == -1:
            alignment = rapidfuzz.fuzz.partial_ratio_alignment(search_phrase, full_text, score_cutoff=cutoff)
            if alignment:
                match_index = alignment.dest_start

        return match_index

    # =========================================================================
    # PRIVATE: Locator building helpers
    # =========================================================================

    def _build_locator_result(self, full_text, spine_map, match_index, total_len):
        """Build a LocatorResult from a match position."""
        percentage = match_index / total_len
        for item in spine_map:
            if item["start"] <= match_index < item["end"]:
                local_index = match_index - item["start"]

                xpath_str, target_tag, is_anchored = self._generate_xpath_bs4(item["content"], local_index)
                css_selector = self._generate_css_selector(target_tag)
                cfi = self._generate_cfi(item["spine_index"] - 1, item["content"], local_index)

                doc_frag_prefix = f"/body/DocFragment[{item['spine_index']}]"
                if xpath_str.startswith("//"):
                    final_xpath = doc_frag_prefix + xpath_str[1:]
                elif xpath_str.startswith("/"):
                    final_xpath = doc_frag_prefix + xpath_str
                else:
                    final_xpath = f"{doc_frag_prefix}/{xpath_str}"

                spine_item_len = item["end"] - item["start"]
                chapter_progress = 0.0
                if spine_item_len > 0:
                    chapter_progress = local_index / spine_item_len

                return LocatorResult(
                    percentage=percentage,
                    xpath=final_xpath,
                    perfect_ko_xpath=None,
                    match_index=match_index,
                    cfi=cfi,
                    href=item["href"],
                    fragment=None,
                    css_selector=css_selector,
                    chapter_progress=chapter_progress,
                )

        return None

    def _generate_css_selector(self, target_tag):
        """Generate a Readium-compatible CSS selector."""
        if not target_tag:
            return ""
        segments = []
        curr = target_tag
        while curr and curr.name != "[document]":
            if not isinstance(curr, Tag):
                curr = curr.parent
                continue
            index = 1
            sibling = curr.previous_sibling
            while sibling:
                if isinstance(sibling, Tag):
                    index += 1
                sibling = sibling.previous_sibling
            segments.append(f"{curr.name}:nth-child({index})")
            curr = curr.parent
        return " > ".join(reversed(segments))

    def _generate_cfi(self, spine_index, html_content, local_target_index):
        """Generate an EPUB CFI for Booklore/Readium."""
        soup = BeautifulSoup(html_content, "html.parser")
        current_char_count = 0
        target_tag = None

        elements = soup.find_all(string=True)
        for string in elements:
            text_len = len(string.strip())
            if text_len == 0:
                continue
            if current_char_count + text_len >= local_target_index:
                target_tag = string.parent
                break
            current_char_count += text_len
            if current_char_count < local_target_index:
                current_char_count += 1

        if not target_tag:
            spine_step = (spine_index + 1) * 2
            return f"epubcfi(/6/{spine_step}!/4/2/1:0)"

        path_segments = []
        curr = target_tag
        while curr and curr.name != "[document]":
            if curr.name == "body":
                path_segments.append("4")
                break
            index = 1
            sibling = curr.previous_sibling
            while sibling:
                if isinstance(sibling, Tag):
                    index += 1
                sibling = sibling.previous_sibling
            path_segments.append(str(index * 2))
            curr = curr.parent

        spine_step = (spine_index + 1) * 2
        element_path = "/".join(reversed(path_segments))
        return f"epubcfi(/6/{spine_step}!/{element_path}:0)"

    def _generate_xpath_bs4(self, html_content, local_target_index):
        """
        BS4 XPath generator for find_text_location.
        Returns: (xpath_string, target_tag_object, is_anchored)
        """
        soup = BeautifulSoup(html_content, "html.parser")
        current_char_count = 0
        target_tag = None

        elements = soup.find_all(string=True)
        for string in elements:
            text_len = len(string.strip())
            if text_len == 0:
                continue
            if current_char_count + text_len >= local_target_index:
                target_tag = string.parent
                break
            current_char_count += text_len
            if current_char_count < local_target_index:
                current_char_count += 1

        if not target_tag:
            return "/body/div/p[1]", None, False

        path_segments = []
        curr = target_tag
        found_anchor = False

        while curr and curr.name != "[document]":
            if curr.name == "body":
                path_segments.append("body")
                break
            if curr.has_attr("id") and curr["id"]:
                path_segments.append(f"*[@id='{curr['id']}']")
                found_anchor = True
                break
            index = 1
            sibling = curr.previous_sibling
            while sibling:
                if isinstance(sibling, Tag) and sibling.name == curr.name:
                    index += 1
                sibling = sibling.previous_sibling
            path_segments.append(f"{curr.name}[{index}]")
            curr = curr.parent

        if not path_segments:
            return "/body/p[1]", target_tag, False

        xpath = "//" + "/".join(reversed(path_segments)) if found_anchor else "/" + "/".join(reversed(path_segments))
        xpath = xpath.rstrip("/")
        if xpath in ("", "/", "//", "/body", "//body"):
            xpath = "/body/p[1]"
            found_anchor = False
        return xpath, target_tag, found_anchor
