import pytest

pytestmark = pytest.mark.docker

import unittest

from lxml import html

from src.utils.koreader_xpath import KoReaderXPathService
from src.utils.locator_search import LocatorSearchService


class TestEbookSentenceXPathFallback(unittest.TestCase):
    def setUp(self):
        self.service = KoReaderXPathService()

    def test_chapter_fallback_uses_sentence_text_node(self):
        html_content = "<html><body><div class='chapter'><img src='x.jpg'/><p>First sentence.</p></div></body></html>"
        xpath = self.service._build_sentence_level_chapter_fallback_xpath(html_content, 7)
        self.assertTrue(xpath.startswith("/body/DocFragment[7]/"))
        self.assertTrue(xpath.endswith(".0"))
        self.assertIn("/text()", xpath)

    def test_chapter_fallback_returns_default_when_no_text(self):
        html_content = "<html><body><div><img src='x.jpg'/></div></body></html>"
        xpath = self.service._build_sentence_level_chapter_fallback_xpath(html_content, 5)
        self.assertEqual(xpath, "/body/DocFragment[5]/body/p[1]/text().0")

    def test_generate_xpath_bs4_never_returns_root_or_trailing_slash(self):
        locator_service = LocatorSearchService()
        html_content = "<html><body>Single sentence only.</body></html>"
        xpath, _, _ = locator_service._generate_xpath_bs4(html_content, 0)
        self.assertEqual(xpath, "/body/p[1]")
        self.assertFalse(xpath.endswith("/"))

    def test_crengine_safe_xpath_collapses_inline_target_to_structural_anchor(self):
        html_content = "<html><body><p>Lead <span>inline target</span></p></body></html>"
        tree = html.fromstring(html_content)
        span = tree.xpath("//span")[0]

        xpath = self.service._build_crengine_safe_text_xpath(span, 3, html_content)

        self.assertEqual(xpath, "/body/DocFragment[3]/body/p/text().0")
        self.assertNotIn("/span", xpath)

    def test_crengine_safe_xpath_falls_back_when_anchor_has_no_direct_text(self):
        html_content = "<html><body><p><span>inline only</span></p></body></html>"
        tree = html.fromstring(html_content)
        span = tree.xpath("//span")[0]

        xpath = self.service._build_crengine_safe_text_xpath(span, 8, html_content)

        self.assertEqual(xpath, "/body/DocFragment[8]/body/p/text().0")
        self.assertNotIn("/span", xpath)


if __name__ == "__main__":
    unittest.main()
