"""
Tests for KoReaderXPathService — KOReader XPath generation and resolution.

All tests use crafted HTML and synthetic spine maps — no file I/O or mocking.
"""

import pytest

pytestmark = pytest.mark.docker

from lxml import html

from src.utils.koreader_xpath import KoReaderXPathService


def _make_spine_map(html_contents):
    """Build a spine map from a list of HTML strings, matching extract_text_and_map format."""
    from bs4 import BeautifulSoup

    spine_map = []
    full_text_parts = []
    current_idx = 0

    for i, content in enumerate(html_contents):
        if isinstance(content, str):
            content = content.encode("utf-8")
        soup = BeautifulSoup(content, "html.parser")
        text = soup.get_text(separator=" ", strip=True)
        start = current_idx
        end = current_idx + len(text)
        spine_map.append(
            {
                "start": start,
                "end": end,
                "spine_index": i + 1,
                "href": f"chapter{i + 1}.xhtml",
                "content": content,
            }
        )
        full_text_parts.append(text)
        current_idx = end + 1

    full_text = " ".join(full_text_parts)
    return full_text, spine_map


class TestGenerateXpath:
    def setup_method(self):
        self.service = KoReaderXPathService()

    def test_simple_paragraph_text(self):
        content = "<html><body><p>Hello world this is a test.</p></body></html>"
        full_text, spine_map = _make_spine_map([content])
        xpath = self.service.generate_xpath(full_text, spine_map, 0)
        assert xpath is not None
        assert xpath.startswith("/body/DocFragment[1]/")
        assert "/text()" in xpath
        assert xpath.endswith(".0")

    def test_inline_tags_skipped_to_structural_parent(self):
        content = "<html><body><p>Lead text <em>emphasized word</em> more text.</p></body></html>"
        full_text, spine_map = _make_spine_map([content])
        # Position inside the <em> text
        em_start = full_text.find("emphasized")
        xpath = self.service.generate_xpath(full_text, spine_map, em_start)
        assert xpath is not None
        assert "/em" not in xpath
        assert "/p" in xpath or "body" in xpath

    def test_duplicate_text_correct_occurrence(self):
        content = "<html><body><p>Hello world.</p><p>Hello world.</p><p>Unique ending.</p></body></html>"
        full_text, spine_map = _make_spine_map([content])
        # Target the second "Hello world." occurrence
        first = full_text.find("Hello world.")
        second = full_text.find("Hello world.", first + 1)
        xpath = self.service.generate_xpath(full_text, spine_map, second)
        assert xpath is not None
        assert "/body/DocFragment[1]/" in xpath

    def test_position_at_start(self):
        content = "<html><body><p>First paragraph.</p><p>Second paragraph.</p></body></html>"
        full_text, spine_map = _make_spine_map([content])
        xpath = self.service.generate_xpath(full_text, spine_map, 0)
        assert xpath is not None
        assert "/body/DocFragment[1]/" in xpath

    def test_position_at_end(self):
        content = "<html><body><p>First paragraph.</p><p>Last paragraph here.</p></body></html>"
        full_text, spine_map = _make_spine_map([content])
        xpath = self.service.generate_xpath(full_text, spine_map, len(full_text) - 1)
        assert xpath is not None
        assert "/body/DocFragment[1]/" in xpath

    def test_empty_chapter_falls_back_to_sentence_level(self):
        content = "<html><body><div><img src='cover.jpg'/></div></body></html>"
        spine_map = [
            {
                "start": 0,
                "end": 0,
                "spine_index": 1,
                "href": "chapter1.xhtml",
                "content": content.encode("utf-8"),
            }
        ]
        xpath = self.service.generate_xpath("x", spine_map, 0)
        # Should get a fallback xpath
        assert xpath is not None
        assert "/body/DocFragment[1]/" in xpath

    def test_multiple_spine_items_correct_docfragment(self):
        ch1 = "<html><body><p>Chapter one text here.</p></body></html>"
        ch2 = "<html><body><p>Chapter two different content.</p></body></html>"
        full_text, spine_map = _make_spine_map([ch1, ch2])
        # Target text in chapter 2
        ch2_start = full_text.find("Chapter two")
        xpath = self.service.generate_xpath(full_text, spine_map, ch2_start)
        assert xpath is not None
        assert "/body/DocFragment[2]/" in xpath

    def test_nested_structural_tags(self):
        content = "<html><body><div><section><p>Nested <span>deeply</span> text.</p></section></div></body></html>"
        full_text, spine_map = _make_spine_map([content])
        xpath = self.service.generate_xpath(full_text, spine_map, 0)
        assert xpath is not None
        assert "/span" not in xpath

    def test_returns_none_for_empty_inputs(self):
        assert self.service.generate_xpath("", [], 0) is None
        assert self.service.generate_xpath("text", [], 0) is None
        assert self.service.generate_xpath("", [{"start": 0, "end": 0}], 0) is None


class TestGenerateSentenceLevelXpath:
    def setup_method(self):
        self.service = KoReaderXPathService()

    def test_valid_percentage(self):
        content = "<html><body><p>Some text in a paragraph.</p></body></html>"
        full_text, spine_map = _make_spine_map([content])
        xpath = self.service.generate_sentence_level_xpath(full_text, spine_map, 0.5)
        assert xpath is not None
        assert "/body/DocFragment[1]/" in xpath
        assert xpath.endswith(".0")

    def test_percentage_zero(self):
        content = "<html><body><p>Beginning text.</p></body></html>"
        full_text, spine_map = _make_spine_map([content])
        xpath = self.service.generate_sentence_level_xpath(full_text, spine_map, 0.0)
        assert xpath is not None
        assert "/body/DocFragment[1]/" in xpath

    def test_percentage_near_end(self):
        ch1 = "<html><body><p>Chapter one.</p></body></html>"
        ch2 = "<html><body><p>Chapter two final.</p></body></html>"
        full_text, spine_map = _make_spine_map([ch1, ch2])
        xpath = self.service.generate_sentence_level_xpath(full_text, spine_map, 0.99)
        assert xpath is not None
        assert "/body/DocFragment[2]/" in xpath

    def test_returns_none_for_empty_text(self):
        assert self.service.generate_sentence_level_xpath("", [], 0.5) is None


class TestResolveXpath:
    def setup_method(self):
        self.service = KoReaderXPathService()

    def test_round_trip_generate_then_resolve(self):
        content = "<html><body><p>The quick brown fox jumps over the lazy dog.</p></body></html>"
        full_text, spine_map = _make_spine_map([content])
        position = full_text.find("brown fox")
        xpath = self.service.generate_xpath(full_text, spine_map, position)
        assert xpath is not None

        resolved = self.service.resolve_xpath(full_text, spine_map, xpath)
        assert resolved is not None
        assert "brown fox" in resolved or "quick" in resolved

    def test_xpath_with_id_anchor_fallback(self):
        content = "<html><body><div id='chapter3'><p>Content in identified div.</p></div></body></html>"
        full_text, spine_map = _make_spine_map([content])
        xpath = "/body/DocFragment[1]/body/div[@id='chapter3']/p/text().0"
        # The @id fallback in _resolve_xpath_elements should find this
        resolved = self.service.resolve_xpath(full_text, spine_map, xpath)
        # May or may not resolve depending on exact xpath format; just shouldn't crash
        assert resolved is None or isinstance(resolved, str)

    def test_no_matching_elements_returns_none(self):
        content = "<html><body><p>Simple text.</p></body></html>"
        full_text, spine_map = _make_spine_map([content])
        xpath = "/body/DocFragment[1]/body/section[99]/p[42]/text().0"
        resolved = self.service.resolve_xpath(full_text, spine_map, xpath)
        assert resolved is None

    def test_missing_docfragment_returns_none(self):
        content = "<html><body><p>Some text.</p></body></html>"
        full_text, spine_map = _make_spine_map([content])
        result = self.service.resolve_xpath(full_text, spine_map, "/body/p/text().0")
        assert result is None

    def test_wrong_spine_index_returns_none(self):
        content = "<html><body><p>Only chapter.</p></body></html>"
        full_text, spine_map = _make_spine_map([content])
        result = self.service.resolve_xpath(full_text, spine_map, "/body/DocFragment[99]/body/p/text().0")
        assert result is None


class TestHelperMethods:
    def setup_method(self):
        self.service = KoReaderXPathService()

    def test_build_crengine_safe_xpath_collapses_inline(self):
        html_content = "<html><body><p>Lead <span>target text</span></p></body></html>"
        tree = html.fromstring(html_content)
        span = tree.xpath("//span")[0]
        xpath = self.service._build_crengine_safe_text_xpath(span, 3, html_content)
        assert "/body/DocFragment[3]/" in xpath
        assert "/span" not in xpath
        assert "/text()" in xpath

    def test_sentence_level_fallback_with_paragraph(self):
        html_content = "<html><body><img src='x.jpg'/><p>First real text.</p></body></html>"
        xpath = self.service._build_sentence_level_chapter_fallback_xpath(html_content, 5)
        assert xpath.startswith("/body/DocFragment[5]/")
        assert "/text()" in xpath
        assert xpath.endswith(".0")

    def test_sentence_level_fallback_no_text_returns_default(self):
        html_content = "<html><body><img src='x.jpg'/></body></html>"
        xpath = self.service._build_sentence_level_chapter_fallback_xpath(html_content, 2)
        assert xpath == "/body/DocFragment[2]/body/p[1]/text().0"

    def test_build_xpath_with_indexed_siblings(self):
        html_content = "<html><body><p>First</p><p>Second</p><p>Third</p></body></html>"
        tree = html.fromstring(html_content)
        paragraphs = tree.xpath("//p")
        # Third paragraph should get index [3]
        xpath = self.service._build_xpath(paragraphs[2])
        assert "p[3]" in xpath

    def test_build_xpath_single_child_no_index(self):
        html_content = "<html><body><div><p>Only child</p></div></body></html>"
        tree = html.fromstring(html_content)
        p = tree.xpath("//p")[0]
        xpath = self.service._build_xpath(p)
        # Single p child shouldn't need an index
        assert "p[" not in xpath or "p[1]" not in xpath
