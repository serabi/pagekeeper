from pathlib import Path

from src.utils.markdown import render_markdown_html, sanitize_html


def test_render_markdown_html_returns_block_html():
    assert render_markdown_html("Hello world") == "<p>Hello world</p>"


def test_sanitize_html_returns_markup():
    assert str(sanitize_html("<p>safe</p>")) == "<p>safe</p>"


def test_reading_detail_uses_block_container_for_markdown():
    template = (Path(__file__).resolve().parent.parent / "templates" / "reading_detail.html").read_text()
    assert '<div class="r-tl-text">{{ journal.entry|markdown }}</div>' in template
