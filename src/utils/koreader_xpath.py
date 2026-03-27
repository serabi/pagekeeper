"""
KOReader XPath Generation and Resolution for PageKeeper.

Generates CREngine-compatible XPaths from character positions in EPUB text,
and resolves existing XPaths back to text snippets. Uses a hybrid BS4→LXML
strategy: BS4 for exact text-offset alignment, LXML for structurally correct
XPath output.
"""

import logging
import re

from bs4 import BeautifulSoup, Tag
from lxml import html

logger = logging.getLogger(__name__)


class KoReaderXPathService:
    CRENGINE_FRAGILE_INLINE_TAGS = {"span", "em", "strong", "b", "i", "u", "a", "font", "small", "big", "sub", "sup"}
    CRENGINE_STRUCTURAL_TAGS = {
        "p",
        "div",
        "section",
        "article",
        "blockquote",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "header",
        "footer",
        "aside",
        "td",
        "th",
        "dt",
        "dd",
        "figcaption",
        "pre",
    }

    def generate_xpath(self, full_text, spine_map, position) -> str | None:
        """
        Generate a KOReader XPath for a specific character position in the book.

        Uses BS4 to find the exact text node matching the position, then maps
        to LXML for a structurally correct XPath. Falls back to BS4 structural
        path or sentence-level chapter fallback if hybrid mapping fails.
        """
        try:
            if not full_text or not spine_map:
                return None

            position = max(0, min(position, len(full_text) - 1))

            target_item = next((item for item in spine_map if item["start"] <= position < item["end"]), spine_map[-1])
            local_pos = position - target_item["start"]

            target_string, target_tag, elements = self._find_text_node(target_item, local_pos)

            if not target_string:
                logger.warning(f"No matching text element found in spine {target_item['spine_index']}")
                return self._build_sentence_level_chapter_fallback_xpath(
                    target_item["content"], target_item["spine_index"]
                )

            if not target_tag or target_tag.name == "[document]":
                return self._build_sentence_level_chapter_fallback_xpath(
                    target_item["content"], target_item["spine_index"]
                )

            # Phase 1: Try hybrid BS4→LXML anchor mapping
            result = self._hybrid_anchor_to_lxml(target_string, elements, target_item)
            if result:
                return result

            # Phase 2: Fall back to BS4 structural path
            return self._bs4_structural_fallback(target_tag, target_item)

        except Exception as e:
            logger.error(f"Error generating KOReader XPath: {e}")
            return None

    def generate_sentence_level_xpath(self, full_text, spine_map, percentage) -> str | None:
        """
        Resolve a sentence-level KOReader XPath from percentage.
        Returns node-start offset (.0), not word-level offsets.
        """
        try:
            if not full_text:
                return None
            pct = max(0.0, min(1.0, float(percentage if percentage is not None else 0.0)))
            position = int((len(full_text) - 1) * pct) if len(full_text) > 1 else 0
            return self.generate_xpath(full_text, spine_map, position)
        except Exception as e:
            logger.error(f"Error generating sentence-level KOReader XPath: {e}")
            return None

    def resolve_xpath(self, full_text, spine_map, xpath_str) -> str | None:
        """
        Resolve a KOReader XPath back to a text snippet.

        Uses LXML to find the target element, then searches for its text in the
        BS4-generated full_text to ensure alignment (fixes parser drift).
        """
        try:
            logger.debug(f"Resolving XPath (Hybrid): {xpath_str}")

            match = re.search(r"DocFragment\[(\d+)]", xpath_str)
            if not match:
                return None
            spine_index = int(match.group(1))

            target_item = next((i for i in spine_map if i["spine_index"] == spine_index), None)
            if not target_item:
                return None

            # Parse path and offset
            relative_path = xpath_str.split(f"DocFragment[{spine_index}]")[-1]
            offset_match = re.search(r"/text\(\)\.(\d+)$", relative_path)
            target_offset = int(offset_match.group(1)) if offset_match else 0
            clean_xpath = re.sub(r"/text\(\)\.(\d+)$", "", relative_path)

            if clean_xpath.startswith("/"):
                clean_xpath = "." + clean_xpath

            tree = html.fromstring(target_item["content"])

            elements = self._resolve_xpath_elements(tree, clean_xpath)
            if not elements:
                logger.warning(f"Could not resolve XPath: {clean_xpath}")
                return None

            target_node = elements[0]

            # Try text anchor matching first (avoids parser drift)
            result = self._resolve_via_text_anchor(target_node, target_item, target_offset, full_text)
            if result:
                return result

            # Fallback to LXML offset calculation
            return self._resolve_via_lxml_offset(tree, target_node, target_item, target_offset, full_text)

        except Exception as e:
            logger.error(f"Error resolving XPath '{xpath_str}': {e}")
            return None

    # =========================================================================
    # PRIVATE: Text node location (Phase 0)
    # =========================================================================

    def _find_text_node(self, target_item, local_pos):
        """
        Walk BS4 text nodes to find the NavigableString at the given local
        character position within a spine item.

        Returns (target_string, target_tag, all_elements) or (None, None, []).
        """
        soup = BeautifulSoup(target_item["content"], "html.parser")
        current_char_count = 0
        target_string = None
        first_non_empty_string = None
        last_non_empty_string = None

        elements = soup.find_all(string=True)
        for string in elements:
            clean_text = string.strip()
            text_len = len(clean_text)
            if text_len == 0:
                continue

            if first_non_empty_string is None:
                first_non_empty_string = string
            last_non_empty_string = string

            if current_char_count + text_len > local_pos:
                target_string = string
                break

            current_char_count += text_len
            if current_char_count <= local_pos:
                current_char_count += 1

        if target_string is None:
            target_string = last_non_empty_string or first_non_empty_string

        target_tag = target_string.parent if target_string else None
        return target_string, target_tag, elements

    # =========================================================================
    # PRIVATE: Hybrid BS4→LXML anchor mapping (Phase 1)
    # =========================================================================

    def _hybrid_anchor_to_lxml(self, target_string, elements, target_item):
        """
        Map a BS4 NavigableString to its LXML equivalent by counting
        text occurrences, then build a CREngine-safe XPath.

        Returns the xpath string, or None if mapping fails.
        """
        search_text = str(target_string)
        occurrence_index = 0

        for string in elements:
            if string is target_string:
                break
            if str(string) == search_text:
                occurrence_index += 1

        tree = html.fromstring(target_item["content"])
        current_occurrence = 0

        for el in tree.iter():
            if el.text and el.text == search_text:
                if current_occurrence == occurrence_index:
                    return self._build_crengine_safe_text_xpath(el, target_item["spine_index"], target_item["content"])
                current_occurrence += 1

            if el.tail and el.tail == search_text:
                if current_occurrence == occurrence_index:
                    parent = el.getparent()
                    node_to_build = parent if parent is not None else el
                    return self._build_crengine_safe_text_xpath(
                        node_to_build, target_item["spine_index"], target_item["content"]
                    )
                current_occurrence += 1

        logger.warning(f"Hybrid Anchor mapping failed for '{search_text}'. Falling back to BS4 structural path.")
        return None

    # =========================================================================
    # PRIVATE: BS4 structural fallback (Phase 2)
    # =========================================================================

    def _bs4_structural_fallback(self, target_tag, target_item):
        """
        Build a positional XPath by walking BS4 parents, skipping
        CREngine-fragile inline tags.
        """
        path_segments = []
        curr = target_tag

        while curr and curr.name != "[document]":
            if curr.name == "body":
                path_segments.append("body")
                break

            if curr.name in self.CRENGINE_FRAGILE_INLINE_TAGS:
                curr = curr.parent
                continue

            index = 1
            sibling = curr.previous_sibling
            while sibling:
                if isinstance(sibling, Tag) and sibling.name == curr.name:
                    index += 1
                sibling = sibling.previous_sibling

            path_segments.append(f"{curr.name}[{index}]")
            curr = curr.parent

        if not path_segments or path_segments[-1] != "body":
            path_segments.append("body")

        xpath = "/".join(reversed(path_segments))
        if xpath == "body":
            return self._build_sentence_level_chapter_fallback_xpath(target_item["content"], target_item["spine_index"])
        return f"/body/DocFragment[{target_item['spine_index']}]/{xpath}/text().0"

    # =========================================================================
    # PRIVATE: XPath building helpers
    # =========================================================================

    def _build_crengine_safe_text_xpath(self, element, spine_index, html_content) -> str:
        anchor = self._nearest_crengine_anchor(element)
        suffix = self._first_non_empty_direct_text_suffix(anchor)
        if not suffix:
            suffix = "/text()"
        xpath_base = self._build_xpath(anchor)
        return f"/body/DocFragment[{spine_index}]/{xpath_base}{suffix}.0"

    def _build_sentence_level_chapter_fallback_xpath(self, html_content, spine_index) -> str:
        """
        Build a safe sentence-level XPath anchored to the first readable text node
        in the chapter. Targets node starts (.0) instead of character-level offsets.
        """
        default_xpath = f"/body/DocFragment[{spine_index}]/body/p[1]/text().0"
        try:
            tree = html.fromstring(html_content)
        except Exception as e:
            logger.debug(f"Failed to parse HTML for sentence-level XPath (spine_index={spine_index}): {e}")
            return default_xpath

        sentence_tags = (
            "p",
            "li",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "blockquote",
            "figcaption",
            "dd",
            "dt",
            "td",
            "th",
            "div",
            "section",
            "article",
            "pre",
        )

        sentence_tag_set = set(sentence_tags)
        for element in tree.iter():
            if self._local_tag_name(element) in sentence_tag_set:
                suffix = self._first_non_empty_direct_text_suffix(element)
                if suffix:
                    xpath_base = self._build_xpath(element)
                    return f"/body/DocFragment[{spine_index}]/{xpath_base}{suffix}.0"

        for element in tree.iter():
            if self._local_tag_name(element) not in self.CRENGINE_STRUCTURAL_TAGS:
                continue
            suffix = self._first_non_empty_direct_text_suffix(element)
            if suffix:
                xpath_base = self._build_xpath(element)
                return f"/body/DocFragment[{spine_index}]/{xpath_base}{suffix}.0"

        return default_xpath

    def _build_xpath(self, element):
        """Build XPath for an lxml element, ensuring proper KOReader format."""
        parts = []
        current = element

        while current is not None and current.tag not in ["html", "document"]:
            tag_name = self._local_tag_name(current)

            if tag_name in self.CRENGINE_FRAGILE_INLINE_TAGS:
                current = current.getparent()
                continue

            parent = current.getparent()
            if parent is not None:
                siblings = [s for s in parent if self._local_tag_name(s) == tag_name]
                if len(siblings) > 1:
                    index = siblings.index(current) + 1
                    parts.insert(0, f"{tag_name}[{index}]")
                else:
                    parts.insert(0, tag_name)
            else:
                parts.insert(0, tag_name)
            current = parent

        if parts and parts[0] == "html":
            parts.pop(0)
        if not parts or parts[0] != "body":
            parts.insert(0, "body")
        if len(parts) <= 1:
            parts = ["body", "p[1]"]

        return "/".join(parts)

    def _nearest_crengine_anchor(self, node):
        current = node
        while current is not None:
            tag_name = self._local_tag_name(current)
            if tag_name == "body":
                return current
            if tag_name in self.CRENGINE_STRUCTURAL_TAGS:
                return current
            if tag_name in ("html", "document", "[document]"):
                break
            current = self._get_parent_node(current)
        return node

    def _first_non_empty_direct_text_suffix(self, element) -> str | None:
        if element is None:
            return None
        try:
            direct_text_nodes = element.xpath("text()")
            for i, node in enumerate(direct_text_nodes, start=1):
                if str(node).strip():
                    return "/text()" if i == 1 else f"/text()[{i}]"
        except Exception as e:
            logger.debug(f"XPath text() node lookup failed: {e}")

        if isinstance(element, Tag):
            text_nodes = [child for child in element.children if isinstance(child, str)]
            for i, node in enumerate(text_nodes, start=1):
                if str(node).strip():
                    return "/text()" if i == 1 else f"/text()[{i}]"
        return None

    def _local_tag_name(self, node) -> str:
        tag = getattr(node, "tag", None)
        if not isinstance(tag, str):
            tag = getattr(node, "name", None)
        if not isinstance(tag, str):
            return ""
        if "}" in tag:
            tag = tag.split("}", 1)[1]
        return tag.lower()

    def _get_parent_node(self, node):
        if node is None:
            return None
        getparent = getattr(node, "getparent", None)
        if callable(getparent):
            return getparent()
        return getattr(node, "parent", None)

    # =========================================================================
    # PRIVATE: XPath resolution helpers
    # =========================================================================

    def _resolve_xpath_elements(self, tree, clean_xpath):
        """Try multiple XPath resolution strategies, returning matched elements."""
        elements = []
        try:
            elements = tree.xpath(clean_xpath)
        except Exception as e:
            logger.debug(f"XPath query failed: {e}")

        if not elements and clean_xpath.startswith("./"):
            try:
                elements = tree.xpath(clean_xpath[2:])
            except Exception as e:
                logger.debug(f"XPath fallback (strip ./) failed: {e}")

        if not elements:
            id_match = re.search(r"@id='([^']+)'", clean_xpath)
            if id_match:
                try:
                    elements = tree.xpath(f"//*[@id='{id_match.group(1)}']")
                except Exception as e:
                    logger.debug(f"XPath fallback (id lookup) failed: {e}")

        if not elements:
            simple_path = re.sub(r"\[\d+]", "", clean_xpath)
            try:
                elements = tree.xpath(simple_path)
            except Exception as e:
                logger.debug(f"XPath fallback (simplified path) failed: {e}")

        return elements

    def _resolve_via_text_anchor(self, target_node, target_item, target_offset, full_text):
        """Resolve XPath by finding the target node's text in the BS4 chapter text."""
        node_text = ""
        if target_node.text:
            node_text += target_node.text.strip()
        if target_node.tail:
            node_text += " " + target_node.tail.strip()

        if len(node_text) < 20:
            parent = target_node.getparent()
            if parent is not None:
                node_text = parent.text_content().strip()

        clean_anchor = " ".join(node_text.split())
        if not clean_anchor:
            return None

        bs4_chapter_text = BeautifulSoup(target_item["content"], "html.parser").get_text(separator=" ", strip=True)
        local_start_index = bs4_chapter_text.find(clean_anchor)

        if local_start_index != -1:
            safe_offset = min(target_offset, len(clean_anchor))
            global_index = target_item["start"] + local_start_index + safe_offset
            start = max(0, global_index)
            end = min(len(full_text), global_index + 600)
            return full_text[start:end]

        return None

    def _resolve_via_lxml_offset(self, tree, target_node, target_item, target_offset, full_text):
        """Fallback: calculate position by iterating LXML tree nodes."""
        logger.debug("Exact text match failed, falling back to LXML offset calculation")

        preceding_len = 0
        found_target = False
        SEPARATOR_LEN = 1

        for node in tree.iter():
            if node == target_node:
                found_target = True
                if node.text and target_offset > 0:
                    raw_segment = node.text[: min(len(node.text), target_offset)]
                    preceding_len += len(raw_segment.strip())
                break

            if node.text and node.text.strip():
                preceding_len += len(node.text.strip()) + SEPARATOR_LEN
            if node.tail and node.tail.strip():
                preceding_len += len(node.tail.strip()) + SEPARATOR_LEN

        if found_target:
            local_pos = preceding_len
            global_offset = target_item["start"] + local_pos
            start = max(0, global_offset)
            end = min(len(full_text), global_offset + 500)
            return full_text[start:end]

        return None
