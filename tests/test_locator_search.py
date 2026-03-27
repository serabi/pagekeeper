"""
Tests for LocatorSearchService — text search, locator ID resolution, CFI resolution.

All tests use crafted text and synthetic spine maps — no file I/O or mocking.
"""

import pytest

pytestmark = pytest.mark.docker

from bs4 import BeautifulSoup

from src.utils.locator_search import LocatorSearchService


def _make_spine_map(html_contents):
    """Build a spine map from a list of HTML strings, matching extract_text_and_map format."""
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


class TestFindTextLocation:
    def setup_method(self):
        self.service = LocatorSearchService(fuzzy_threshold=80)

    def test_exact_match(self):
        content = "<html><body><p>The quick brown fox jumps over the lazy dog.</p></body></html>"
        full_text, spine_map = _make_spine_map([content])
        result = self.service.find_text_location(full_text, spine_map, "brown fox")
        assert result is not None
        assert result.match_index == full_text.find("brown fox")
        assert 0 < result.percentage < 1

    def test_unique_anchor_preferred_over_first_occurrence(self):
        # "Chapter One" appears twice (ToC and body), but 10-word anchor is unique
        toc = "<html><body><p>Table of Contents: Chapter One - Introduction</p></body></html>"
        body = "<html><body><p>Chapter One - Introduction to the wonderful world of testing software applications today</p></body></html>"
        full_text, spine_map = _make_spine_map([toc, body])
        # The 10-word unique sequence should match in the body, not the ToC
        search = "Chapter One - Introduction to the wonderful world of testing software"
        result = self.service.find_text_location(full_text, spine_map, search)
        assert result is not None
        # Should find in second chapter (body), not first (ToC)
        body_start = spine_map[1]["start"]
        assert result.match_index >= body_start

    def test_short_phrase_uses_exact_match(self):
        content = "<html><body><p>Short phrase here.</p></body></html>"
        full_text, spine_map = _make_spine_map([content])
        result = self.service.find_text_location(full_text, spine_map, "Short phrase")
        assert result is not None
        assert result.match_index == 0

    def test_normalized_match_ignores_case_and_punctuation(self):
        content = "<html><body><p>Hello, World! This is great.</p></body></html>"
        full_text, spine_map = _make_spine_map([content])
        result = self.service.find_text_location(full_text, spine_map, "hello world")
        assert result is not None

    def test_fuzzy_match_finds_approximate(self):
        content = (
            "<html><body><p>The extraordinary adventures of a curious explorer in distant lands.</p></body></html>"
        )
        full_text, spine_map = _make_spine_map([content])
        # Slightly different text should fuzzy-match
        result = self.service.find_text_location(full_text, spine_map, "extraordinary adventures of curious explorer")
        assert result is not None

    def test_fuzzy_match_with_hint_percentage(self):
        ch1 = "<html><body><p>Some filler text to pad the beginning of the book nicely.</p></body></html>"
        ch2 = "<html><body><p>The target sentence we want to find in the second half.</p></body></html>"
        full_text, spine_map = _make_spine_map([ch1, ch2])
        result = self.service.find_text_location(full_text, spine_map, "target sentence we want", hint_percentage=0.7)
        assert result is not None

    def test_no_match_returns_none(self):
        content = "<html><body><p>Simple text here.</p></body></html>"
        full_text, spine_map = _make_spine_map([content])
        result = self.service.find_text_location(full_text, spine_map, "completely unrelated xyz123 gibberish")
        assert result is None

    def test_empty_text_returns_none(self):
        result = self.service.find_text_location("", [], "anything")
        assert result is None

    def test_returns_valid_cfi(self):
        content = "<html><body><p>Text for CFI generation testing.</p></body></html>"
        full_text, spine_map = _make_spine_map([content])
        result = self.service.find_text_location(full_text, spine_map, "CFI generation")
        assert result is not None
        assert result.cfi is not None
        assert result.cfi.startswith("epubcfi(")

    def test_returns_chapter_progress(self):
        content = "<html><body><p>First half of content. Second half of content here now.</p></body></html>"
        full_text, spine_map = _make_spine_map([content])
        result = self.service.find_text_location(full_text, spine_map, "Second half")
        assert result is not None
        assert result.chapter_progress is not None
        assert 0 < result.chapter_progress < 1

    def test_perfect_ko_xpath_is_none(self):
        """Facade is responsible for filling in perfect_ko_xpath, not this service."""
        content = "<html><body><p>Some text to find.</p></body></html>"
        full_text, spine_map = _make_spine_map([content])
        result = self.service.find_text_location(full_text, spine_map, "text to find")
        assert result is not None
        assert result.perfect_ko_xpath is None


class TestResolveLocatorId:
    def setup_method(self):
        self.service = LocatorSearchService()

    def test_known_fragment_returns_text(self):
        content = "<html><body><p>Intro text.</p><p id='target'>Target paragraph with content.</p></body></html>"
        full_text, spine_map = _make_spine_map([content])
        result = self.service.resolve_locator_id(full_text, spine_map, "chapter1.xhtml", "target")
        assert result is not None
        assert "Target paragraph" in result

    def test_unknown_fragment_returns_none(self):
        content = "<html><body><p>Simple content.</p></body></html>"
        full_text, spine_map = _make_spine_map([content])
        result = self.service.resolve_locator_id(full_text, spine_map, "chapter1.xhtml", "nonexistent")
        assert result is None

    def test_wrong_href_returns_none(self):
        content = "<html><body><p id='found'>Found me.</p></body></html>"
        full_text, spine_map = _make_spine_map([content])
        result = self.service.resolve_locator_id(full_text, spine_map, "wrong_chapter.xhtml", "found")
        assert result is None

    def test_fragment_with_hash_prefix(self):
        content = "<html><body><p id='myid'>Identified paragraph.</p></body></html>"
        full_text, spine_map = _make_spine_map([content])
        result = self.service.resolve_locator_id(full_text, spine_map, "chapter1.xhtml", "#myid")
        assert result is not None
        assert "Identified paragraph" in result


class TestGetTextAroundCfi:
    def setup_method(self):
        self.service = LocatorSearchService()

    def test_spine_index_out_of_range_returns_none(self):
        content = "<html><body><p>Only chapter.</p></body></html>"
        full_text, spine_map = _make_spine_map([content])
        # CFI pointing to spine item 99 (doesn't exist)
        result = self.service.get_text_around_cfi(full_text, spine_map, "epubcfi(/6/200!/4/2/1:0)")
        assert result is None

    def test_malformed_cfi_returns_none(self):
        content = "<html><body><p>Some text.</p></body></html>"
        full_text, spine_map = _make_spine_map([content])
        result = self.service.get_text_around_cfi(full_text, spine_map, "not_a_valid_cfi")
        assert result is None


class TestNormalize:
    def test_strips_non_alphanumeric(self):
        service = LocatorSearchService()
        assert service._normalize("Hello, World!") == "helloworld"
        assert service._normalize("test-case_123") == "testcase123"
        assert service._normalize("") == ""


class TestGenerateXpathBs4:
    def setup_method(self):
        self.service = LocatorSearchService()

    def test_nested_elements_correct_path(self):
        html_content = "<html><body><div><p>First</p><p>Second</p></div></body></html>"
        xpath, tag, anchored = self.service._generate_xpath_bs4(html_content, 0)
        assert xpath.startswith("/body")
        assert tag is not None
        assert not anchored

    def test_element_with_id_uses_anchor(self):
        html_content = "<html><body><div id='ch1'><p>Content here.</p></div></body></html>"
        xpath, tag, anchored = self.service._generate_xpath_bs4(html_content, 0)
        assert anchored
        assert "@id='ch1'" in xpath
        assert xpath.startswith("//")

    def test_empty_body_returns_default(self):
        html_content = "<html><body></body></html>"
        xpath, tag, anchored = self.service._generate_xpath_bs4(html_content, 0)
        assert xpath == "/body/div/p[1]"
        assert tag is None
        assert not anchored


class TestGenerateCfi:
    def setup_method(self):
        self.service = LocatorSearchService()

    def test_produces_valid_cfi_format(self):
        html_content = "<html><body><p>Some text content for CFI.</p></body></html>"
        cfi = self.service._generate_cfi(0, html_content, 5)
        assert cfi.startswith("epubcfi(/6/")
        assert cfi.endswith(":0)")

    def test_no_text_produces_fallback_cfi(self):
        html_content = "<html><body><img src='x.jpg'/></body></html>"
        cfi = self.service._generate_cfi(2, html_content, 0)
        assert cfi.startswith("epubcfi(/6/")
        assert ":0)" in cfi
