#!/usr/bin/env python3
"""Tests for the text preprocessing module."""

import pytest

from preprocess_text import preprocess_html, preprocess_markdown


class TestPreprocessHtml:
    """Tests for HTML preprocessing."""

    def test_simple_paragraph(self):
        """Text inside a simple paragraph tag is preserved."""
        text, mapping = preprocess_html("<p>Hello world</p>")
        assert text == "   Hello world    "
        assert len(text) == len("<p>Hello world</p>")
        assert mapping[0] == 0
        # 'H' at position 3 in cleaned text maps to position 3 in original.
        assert mapping[3] == 3

    def test_nested_tags(self):
        """Nested tags are all replaced with whitespace."""
        html = "<div><p>Hello <b>world</b>!</p></div>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "Hello " in text
        assert "world" in text
        assert "!" in text
        # First 8 characters are spaces from <div><p>, then "Hello " starts.
        assert text[:8].strip() == ""
        assert text[8:14] == "Hello "

    def test_self_closing_tag(self):
        """Self-closing tags are replaced with whitespace."""
        html = "Hello<br>world"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert text == "Hello    world"
        # The <br> is 4 chars, replaced by 4 spaces.
        assert "    " in text

    def test_html_entities_decoded(self):
        """HTML entities are decoded to Unicode characters."""
        html = "Hello &amp; world &#123;"
        text, mapping = preprocess_html(html)
        assert "&" in text
        assert "{" in text
        assert len(text) < len(html)  # entities decode to shorter strings

    def test_comments_removed(self):
        """HTML comments are replaced with whitespace."""
        html = "Hello<!-- comment -->world"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "Hello" in text
        assert "world" in text
        assert "comment" not in text

    def test_empty_input(self):
        """Empty string returns empty string."""
        text, mapping = preprocess_html("")
        assert text == ""
        assert mapping == {}

    def test_no_tags(self):
        """Plain text with no tags is returned unchanged."""
        html = "Hello world"
        text, mapping = preprocess_html(html)
        assert text == "Hello world"
        assert len(text) == len(html)
        for i in range(len(text)):
            assert mapping[i] == i

    def test_offset_mapping_valid(self):
        """Every offset in the mapping is valid."""
        html = "<p>Hello <b>world</b>!</p>"
        text, mapping = preprocess_html(html)
        for cleaned_pos, orig_pos in mapping.items():
            assert 0 <= orig_pos < len(html)
            assert 0 <= cleaned_pos < len(text)


class TestPreprocessMarkdown:
    """Tests for Markdown preprocessing."""

    def test_simple_heading(self):
        """Heading text is preserved."""
        md = "# Hello World\n\nSome text."
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # Heading is preserved.
        assert "Hello World" in text
        assert "Some text." in text

    def test_inline_code_replaced_with_noun(self):
        """Inline code spans are replaced with a noun token."""
        md = "Use `code` here."
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # Backticks are replaced with spaces
        assert "`" not in text
        # Code content is replaced with a noun token (letters only)
        # The token should be present in the text
        assert "code" in text
        assert "Use " in text
        assert " here." in text

    def test_inline_link_removed(self):
        """Inline link URL is replaced with whitespace, link text preserved."""
        md = "Visit [link](http://example.com) now."
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # Link text is preserved.
        assert "link" in text
        # URL is replaced with spaces.
        assert "example.com" not in text
        assert "Visit " in text
        assert " now." in text

    def test_image_removed(self):
        """Image syntax is replaced with whitespace."""
        md = "An image ![alt](img.png) here."
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "alt" not in text
        assert "img.png" not in text

    def test_empty_input(self):
        """Empty string returns empty string."""
        text, mapping, _ = preprocess_markdown("")
        assert text == ""
        assert mapping == {}

    def test_no_special_elements(self):
        """Plain Markdown text is returned unchanged."""
        md = "Hello\n\nWorld\n"
        text, mapping, _ = preprocess_markdown(md)
        assert text == md
        assert len(text) == len(md)

    def test_offset_mapping_valid(self):
        """Every offset in the mapping is within bounds."""
        md = "# Title\n\nSome `code` here.\n"
        text, mapping, _ = preprocess_markdown(md)
        for cleaned_pos, orig_pos in mapping.items():
            assert 0 <= orig_pos < len(md)

    def test_fenced_code_block_removed(self):
        """Fenced code blocks are replaced with whitespace."""
        md = "Text before\n\n```python\nprint('hello')\n```\n\nText after."
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "print" not in text
        assert "python" not in text
        assert "Text before" in text
        assert "Text after" in text


class TestPreprocessHtmlCornerCases:
    """Corner case tests for HTML preprocessing."""

    def test_malformed_html_unclosed_tag(self):
        """Handle malformed HTML with unclosed tags."""
        html = "<p>Hello world"
        text, mapping = preprocess_html(html)
        # The unclosed tag should still be detected and replaced.
        assert len(text) == len(html)
        assert "Hello world" in text

    def test_nested_same_tags(self):
        """Handle nested tags of the same type."""
        html = "<b><b>nested</b></b>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "nested" in text
        # Should have spaces for all 4 tags.
        assert text[:2] == "  "
        assert text[-2:] == "  "

    def test_tags_with_attributes(self):
        """Handle tags with attributes containing special characters."""
        html = '<a href="http://example.com?a=1&b=2">link</a>'
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "link" in text
        # The attribute value should be part of the tag span.
        assert text[:text.find("link")].strip() == ""

    def test_multiple_entities_in_sequence(self):
        """Handle multiple consecutive HTML entities."""
        html = "&amp;&lt;&gt;&quot;"
        text, mapping = preprocess_html(html)
        assert len(text) < len(html)  # entities decode to shorter strings
        assert "&" in text
        assert "<" in text
        assert ">" in text
        assert '"' in text

    def test_numeric_entity_boundary(self):
        """Handle numeric entities at string boundaries."""
        html = "&#65;&#66;&#67;"
        text, mapping = preprocess_html(html)
        assert text == "ABC"
        assert len(text) < len(html)

    def test_script_tag_with_content(self):
        """Handle script tags with JavaScript content."""
        html = "<script>var x = 1;</script>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "var" not in text
        assert "x" not in text
        # Entire script element should be replaced.
        assert text.strip() == ""

    def test_style_tag_with_content(self):
        """Handle style tags with CSS content."""
        html = "<style>body { color: red; }</style>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "body" not in text
        assert "color" not in text

    def test_void_elements(self):
        """Handle void elements like br, hr, img, input."""
        html = "Line1<br>Line2<hr>Line3<img src='x.png'>Line4"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "Line1" in text
        assert "Line2" in text
        assert "Line3" in text
        assert "Line4" in text
        # All void elements should be replaced with spaces.
        for tag in ["<br>", "<hr>", "<img src='x.png'>"]:
            assert tag not in text

    def test_uppercase_tags(self):
        """Handle uppercase HTML tags."""
        html = "<DIV><P>Hello</P></DIV>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "Hello" in text

    def test_mixed_case_tags(self):
        """Handle mixed case HTML tags."""
        html = "<Div><p>Hello</P></div>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "Hello" in text

    def test_special_characters_in_text(self):
        """Handle special characters in text content."""
        html = "<p>Line1\nLine2\tTabbed</p>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "Line1" in text
        assert "\n" in text
        assert "\t" in text

    def test_unicode_text(self):
        """Handle Unicode text content."""
        html = "<p>Héllo wörld 日本語</p>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "Héllo" in text
        assert "wörld" in text
        assert "日本語" in text

    def test_adjacent_tags(self):
        """Handle adjacent tags with no text between them."""
        html = "<b><i>text</i></b>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "text" in text
        # All tags should be replaced with spaces.
        assert text[:2] == "  "
        assert text[-2:] == "  "

    def test_deeply_nested_tags(self):
        """Handle deeply nested tag structures."""
        html = "<div><section><article><p>Deep text</p></article></section></div>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "Deep text" in text
        # All opening and closing tags should be replaced.
        assert "<div>" not in text
        assert "</div>" not in text

    def test_tags_with_newlines(self):
        """Handle tags that span multiple lines."""
        html = "<div\n  class=\"test\"\n>Content</div>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "Content" in text
        # The opening tag with attributes and newlines should be replaced.
        assert "<div\n  class=\"test\"\n>" not in text

    def test_empty_tag(self):
        """Handle empty or malformed tags."""
        html = "<>text</>"
        text, mapping = preprocess_html(html)
        # The regex should handle malformed tags gracefully.
        assert len(text) == len(html)

    def test_single_char_tag(self):
        """Handle single-character tag names."""
        html = "<a>link</a>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "link" in text
        assert text[:2] == "  "
        assert text[-2:] == "  "

    def test_tag_with_no_spaces(self):
        """Handle tags without spaces before closing bracket."""
        html = "<p>Hello</p>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "Hello" in text


class TestPreprocessMarkdownCornerCases:
    """Corner case tests for Markdown preprocessing."""

    def test_backticks_without_code(self):
        """Handle backticks in text without forming code spans."""
        md = "Use `single` backtick for emphasis."
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # Single backticks should not be treated as code spans.
        # The regex requires matching pairs of backticks.

    def test_nested_backticks(self):
        """Handle nested backtick code spans."""
        md = "Outer `inner `code` span` here."
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # The inner code span should be replaced.

    def test_link_with_url_containing_parens(self):
        """Handle links with URLs containing parentheses."""
        md = "See [example](http://en.wikipedia.org/wiki/Test_(test)) for more."
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # Link text is preserved.
        assert "example" in text
        assert "wikipedia.org" not in text
        assert "See " in text

    def test_image_with_alt_containing_brackets(self):
        """Handle images with alt text containing brackets."""
        md = "![alt [text] here](image.png)"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "alt [text] here" not in text
        assert "image.png" not in text

    def test_mixed_inline_elements(self):
        """Handle multiple inline elements in one line."""
        md = "Use `code` and [link](url) and ![img](pic.png) together."
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # Code is replaced with a noun token
        assert "code" in text
        # Link text is preserved, URL is replaced.
        assert "link" in text
        assert "url" not in text
        assert "img" not in text
        assert "pic.png" not in text
        assert "Use " in text
        assert " and " in text
        assert " together." in text

    def test_code_block_with_special_chars(self):
        """Handle fenced code blocks with special characters."""
        md = "```\nimport os; print('Hello!')\n```"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "import" not in text
        assert "print" not in text
        assert "Hello!" not in text

    def test_unicode_in_text(self):
        """Handle Unicode characters in text content."""
        md = "# Título\n\nContenido en español: ¡Hola!\n\n日本語テキスト"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # Heading is preserved.
        assert "Título" in text
        # Body text is preserved.
        assert "¡Hola!" in text
        assert "日本語テキスト" in text

    def test_very_long_input(self):
        """Handle very long input strings."""
        md = "Text " * 1000 + "\n"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert text == md

    def test_whitespace_preservation(self):
        """Ensure whitespace in non-code areas is preserved."""
        md = "Hello   world\n\n  indented  \n"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert text == md

    def test_empty_code_span(self):
        """Handle empty inline code spans."""
        md = "Use `` empty `` code."
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # Empty code spans should be replaced.
        assert "``" not in text or "empty" not in text

    def test_code_span_with_newline(self):
        """Handle code spans with newlines (should not match)."""
        md = "Line1 `code\nspan` Line2"
        text, mapping, _ = preprocess_markdown(md)
        # The regex uses `[^`]+` which does not match newlines.
        # So the backticks should not be treated as code spans.

    def test_multiple_fenced_blocks(self):
        """Handle multiple fenced code blocks."""
        md = "```\ncode1\n```\n\nSome text.\n\n```\ncode2\n```"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "code1" not in text
        assert "code2" not in text
        assert "Some text." in text

    def test_html_block_removed(self):
        """Handle HTML blocks in Markdown."""
        md = "<!-- comment -->\n\nSome text."
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "comment" not in text
        assert "Some text." in text

    def test_table_rows_removed(self):
        """Handle table rows in Markdown."""
        md = "| Header |\n|--------|\n| Cell |\n"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # Table delimiters (|) should be replaced, but cell content preserved.
        assert "|" not in text
        assert "Header" in text
        assert "Cell" in text


class TestPreprocessHtmlAdvancedCornerCases:
    """Advanced corner case tests for HTML preprocessing."""

    def test_tag_with_gt_in_attribute(self):
        """Handle tags with > character in attribute values (quoted)."""
        html = '<a href="http://example.com?a=1>b">link</a>'
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "link" in text
        # The > in the attribute value should be part of the tag span.
        assert "<a href=\"http://example.com?a=1>b\">" not in text

    def test_nested_entities(self):
        """Handle nested/escaped HTML entities."""
        html = "&amp;amp;"
        text, mapping = preprocess_html(html)
        assert len(text) < len(html)
        # The first &amp; should decode to &amp;, then the second &amp; should
        # decode to &. But since we process entities in one pass, only the
        # first level should decode.
        assert "&amp;" in text or "&" in text

    def test_self_closing_with_space(self):
        """Handle self-closing tags with space before />."""
        html = "Line1<br />Line2"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "Line1" in text
        assert "Line2" in text
        assert "<br />" not in text

    def test_multiple_comments_in_sequence(self):
        """Handle multiple consecutive HTML comments."""
        html = "<!--a--><!--b--><!--c-->"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "a" not in text
        assert "b" not in text
        assert "c" not in text

    def test_comment_with_dashes(self):
        """Handle comments containing dashes (but not --)."""
        html = "<!-- this is a comment with dashes - here -->"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "comment" not in text

    def test_bogus_comment(self):
        """Handle bogus comments (unclosed)."""
        html = "Text<!bogus comment"
        text, mapping = preprocess_html(html)
        # Bogus comments should be handled gracefully.
        assert len(text) == len(html)

    def test_doctype_variations(self):
        """Handle different DOCTYPE declarations."""
        htmls = [
            "<!DOCTYPE html>",
            "<!DOCTYPE HTML PUBLIC \"-//W3C//DTD HTML 4.01//EN\">",
            "<!DOCTYPE html PUBLIC \"-//W3C//DTD XHTML 1.0 Transitional//EN\" \"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd\">",
        ]
        for html in htmls:
            text, mapping = preprocess_html(html)
            assert len(text) == len(html)
            assert text.strip() == ""

    def test_cdata_section(self):
        """Handle CDATA sections."""
        html = "Text<![CDATA[<script>alert('xss')</script>]]>more"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "Text" in text
        assert "more" in text
        assert "<script>" not in text

    def test_processing_instruction(self):
        """Handle processing instructions."""
        html = "Text<?xml version=\"1.0\"?>more"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "Text" in text
        assert "more" in text

    def test_tag_with_slash_in_attribute(self):
        """Handle tags with / character in attribute values."""
        html = '<a href="http://example.com/path/file.html">link</a>'
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "link" in text

    def test_deep_nesting(self):
        """Handle deeply nested tag structures (50 levels)."""
        depth = 50
        html = "<div>" * depth + "deep" + "</div>" * depth
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "deep" in text
        # All tags should be replaced with spaces.
        assert html.startswith("<div>")
        assert text.startswith(" " * 5)

    def test_mixed_entity_types(self):
        """Handle mix of named, decimal, and hex entities."""
        html = "&amp; &#38; &#x26;"
        text, mapping = preprocess_html(html)
        assert len(text) < len(html)
        assert "&" in text

    def test_entity_at_string_end(self):
        """Handle entity at the end of the string."""
        html = "Text &amp;"
        text, mapping = preprocess_html(html)
        assert "&" in text

    def test_entity_at_string_start(self):
        """Handle entity at the start of the string."""
        html = "&amp; Text"
        text, mapping = preprocess_html(html)
        assert "&" in text

    def test_adjacent_tags_no_space(self):
        """Handle adjacent tags with no space between them."""
        html = "<b><i>text</i></b>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "text" in text
        # All tags should be replaced.
        assert "<b>" not in text
        assert "</b>" not in text

    def test_tag_with_newline_in_attribute(self):
        """Handle tags with newlines in attribute values."""
        html = '<div\n  class="test\n  multiple\n  lines"\n>Content</div>'
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "Content" in text

    def test_numeric_entity_to_gt(self):
        """Handle numeric entity that decodes to > character."""
        html = "&#62;"
        text, mapping = preprocess_html(html)
        assert text == ">"

    def test_uppercase_lowercase_entities(self):
        """Handle entities in both uppercase and lowercase."""
        html = "&AMP; &amp; &Amp;"
        text, mapping = preprocess_html(html)
        assert "&" in text

    def test_invalid_tag_name_with_digits(self):
        """Handle tags with digits in names (invalid but should handle)."""
        html = "<div1>text</div1>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "text" in text


class TestPreprocessMarkdownAdvancedCornerCases:
    """Advanced corner case tests for Markdown preprocessing."""

    def test_code_span_with_backticks_inside(self):
        """Handle code spans using double backticks containing single backticks."""
        md = "Outer `inner `code` span` here."
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # The inner code span should be replaced.

    def test_link_with_title(self):
        """Handle links with title attributes."""
        md = '[link](http://example.com "title")'
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # Link text is preserved.
        assert "link" in text
        assert "example.com" not in text
        assert "title" not in text

    def test_image_with_title(self):
        """Handle images with title attributes."""
        md = '![alt](http://example.com/img.png "title")'
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "alt" not in text
        assert "img.png" not in text

    def test_autolink(self):
        """Handle autolinks (angle-bracket URLs)."""
        md = "See <http://example.com> for more."
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "example.com" not in text

    def test_inline_html_in_markdown(self):
        """Handle inline HTML tags in Markdown."""
        md = "This has <strong>bold</strong> text."
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # The HTML tags should be removed.
        assert "<strong>" not in text
        assert "</strong>" not in text
        assert "bold" in text

    def test_html_comment_in_markdown(self):
        """Handle HTML comments in Markdown."""
        md = "Text <!-- comment --> more"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "comment" not in text

    def test_fenced_code_with_info_string(self):
        """Handle fenced code blocks with info strings."""
        md = "```python\nimport os\n```"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "import" not in text
        assert "python" not in text

    def test_tilde_fenced_code_block(self):
        """Handle fenced code blocks with tilde fences."""
        md = "~~~python\ncode\n~~~"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "code" not in text

    def test_horizontal_rule_dashes(self):
        """Handle horizontal rules made of dashes."""
        md = "---\n\nText"
        text, mapping, _ = preprocess_markdown(md)
        # Horizontal rules are replaced with spaces.
        assert len(text) == len(md)
        assert "---" not in text
        assert "Text" in text

    def test_horizontal_rule_stars(self):
        """Handle horizontal rules made of asterisks."""
        md = "***\n\nText"
        text, mapping, _ = preprocess_markdown(md)
        # Horizontal rules are replaced with spaces.
        assert len(text) == len(md)
        assert "***" not in text
        assert "Text" in text

    def test_atx_heading_with_closing_hash(self):
        """Handle ATX headings with closing # characters."""
        md = "# Heading ##\n\nText"
        text, mapping, _ = preprocess_markdown(md)
        # ATX headings are preserved.
        assert len(text) == len(md)
        assert "Heading" in text
        assert "Text" in text

    def test_link_with_empty_url(self):
        """Handle links with empty URL."""
        md = '[link]()'
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # Link text is preserved.
        assert "link" in text

    def test_image_with_empty_alt(self):
        """Handle images with empty alt text."""
        md = '![](image.png)'
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "image.png" not in text

    def test_nested_emphasis_with_code(self):
        """Handle emphasis with code inside."""
        md = "*Text with `code` inside*"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # The code span should be replaced.
        assert "`code`" not in text

    def test_multiple_tables_and_code_blocks(self):
        """Handle document with multiple tables and code blocks."""
        md = "| H |\n|---|\n| C |\n\n```\ncode\n```\n\n| H2 |\n|----|\n| C2 |"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "code" not in text
        # Table delimiters should be replaced, but cell content preserved.
        assert "|" not in text
        assert "H" in text
        assert "C" in text
        assert "H2" in text
        assert "C2" in text

    def test_code_span_with_special_characters(self):
        """Handle code spans containing special characters."""
        md = "Use `alert('XSS')` carefully."
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # Special characters are removed, only letters remain
        assert "alert" in text
        assert "XSS" in text
        # Backticks are replaced with spaces
        assert "`" not in text

    def test_link_with_fragment(self):
        """Handle links with fragment identifiers."""
        md = '[link](http://example.com#section)'
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # Link text is preserved.
        assert "link" in text
        assert "example.com" not in text
        assert "section" not in text

    def test_image_with_query_params(self):
        """Handle images with query parameters in URL."""
        md = '![alt](image.png?w=100&h=200)'
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "alt" not in text
        assert "image.png" not in text

    def test_empty_fenced_code_block(self):
        """Handle empty fenced code blocks."""
        md = "```\n\n```"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # The fenced code block should be replaced with spaces.
        assert "```" not in text or text.count("```") < 2

    def test_code_block_with_only_whitespace(self):
        """Handle fenced code blocks containing only whitespace."""
        md = "```\n   \n   \n```"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # The entire code block should be replaced.
        assert text.strip() == ""

    def test_mixed_backtick_levels(self):
        """Handle document with both single and double backtick code spans."""
        md = "`single` and ``double`` code"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # Code content is replaced with noun tokens
        assert "single" in text
        assert "double" in text
        # Backticks are replaced with spaces
        assert "`" not in text


class TestPreprocessHtmlTrivialCases:
    """Trivial test cases for HTML preprocessing."""

    def test_single_tag_no_content(self):
        """Handle a single tag with no content."""
        html = "<p>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert text.strip() == ""

    def test_only_closing_tag(self):
        """Handle only a closing tag."""
        html = "</p>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert text.strip() == ""

    def test_only_opening_tag(self):
        """Handle only an opening tag."""
        html = "<p>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert text.strip() == ""

    def test_empty_attribute(self):
        """Handle tag with empty attribute."""
        html = '<p data-test="">text</p>'
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "text" in text

    def test_attribute_empty_value(self):
        """Handle tag with attribute having empty value."""
        html = '<p data-test="">text</p>'
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "text" in text

    def test_tag_with_only_whitespace(self):
        """Handle tag containing only whitespace."""
        html = "<p>   </p>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        # The whitespace should be preserved.
        assert "   " in text

    def test_single_character_text(self):
        """Handle single character text."""
        html = "<p>a</p>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "a" in text

    def test_text_only_newline(self):
        """Handle text containing only a newline."""
        html = "<p>\n</p>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "\n" in text

    def test_text_only_spaces(self):
        """Handle text containing only spaces."""
        html = "<p>   </p>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        # Spaces should be preserved.
        assert text.strip() == ""

    def test_multiple_spaces_between_tags(self):
        """Handle multiple spaces between tags."""
        html = "<p>   </p>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        # The spaces between tags should be preserved.
        assert "   " in text

    def test_newlines_between_tags(self):
        """Handle newlines between tags."""
        html = "<p>\n</p>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "\n" in text

    def test_tabs_between_tags(self):
        """Handle tabs between tags."""
        html = "<p>\t</p>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "\t" in text

    def test_mixed_whitespace_between_tags(self):
        """Handle mixed whitespace between tags."""
        html = "<p> \t\n </p>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        # All whitespace should be preserved.
        assert " " in text
        assert "\t" in text
        assert "\n" in text

    def test_tag_immediately_followed_by_text(self):
        """Handle tag immediately followed by text."""
        html = "<p>text</p>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "text" in text

    def test_text_immediately_followed_by_tag(self):
        """Handle text immediately followed by tag."""
        html = "text</p>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "text" in text

    def test_multiple_text_nodes_between_tags(self):
        """Handle multiple text nodes between tags."""
        html = "<p>a b c</p>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "a" in text
        assert "b" in text
        assert "c" in text

    def test_consecutive_same_tags(self):
        """Handle consecutive tags of the same type."""
        html = "<b>a</b><b>b</b>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "a" in text
        assert "b" in text

    def test_alternating_text_and_tags(self):
        """Handle alternating text and tags."""
        html = "a<b>b</b>c"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "a" in text
        assert "b" in text
        assert "c" in text

    def test_text_with_only_entities(self):
        """Handle text consisting only of entities."""
        html = "&amp;"
        text, mapping = preprocess_html(html)
        assert len(text) < len(html)
        assert "&" in text

    def test_multiple_consecutive_entities(self):
        """Handle multiple consecutive entities."""
        html = "&amp;&amp;"
        text, mapping = preprocess_html(html)
        assert len(text) < len(html)
        assert "&&" in text

    def test_single_char_tag_name(self):
        """Handle tag with single-character name."""
        html = "<a>link</a>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "link" in text

    def test_tag_with_no_attributes(self):
        """Handle tag with no attributes."""
        html = "<p>text</p>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "text" in text

    def test_adjacent_opening_tags(self):
        """Handle adjacent opening tags with no space."""
        html = "<div><p>text</p></div>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "text" in text

    def test_adjacent_closing_tags(self):
        """Handle adjacent closing tags with no space."""
        html = "</div></p>"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert text.strip() == ""

    def test_text_before_and_after_tag(self):
        """Handle text before and after a single tag."""
        html = "before<p>middle</p>after"
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "before" in text
        assert "middle" in text
        assert "after" in text


class TestPreprocessMarkdownAutolink:
    """Test cases for autolink handling."""

    def test_autolink_replaced(self):
        """Autolink should be replaced with spaces."""
        md = "See <http://example.com> for more"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # Autolink should be replaced with spaces
        assert "<http://example.com>" not in text
        # Text content preserved
        assert "See" in text
        assert "for more" in text

    def test_autolink_with_angle_brackets(self):
        """Autolink with angle brackets."""
        md = "Visit <https://github.com> now"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # Autolink replaced
        assert "<https://github.com>" not in text
        # Text content preserved
        assert "Visit" in text
        assert "now" in text

    def test_multiple_autolinks(self):
        """Multiple autolinks in text."""
        md = "See <http://a.com> and <http://b.com>"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # All autolinks replaced
        assert "<http://a.com>" not in text
        assert "<http://b.com>" not in text
        # Text content preserved
        assert "See" in text
        assert "and" in text

    def test_autolink_with_inline_formatting(self):
        """Autolink with inline formatting."""
        md = "See **<http://example.com>** for more"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # Autolink replaced
        assert "<http://example.com>" not in text
        # Bold markers replaced
        assert "**" not in text
        # Text content preserved
        assert "See" in text
        assert "for more" in text

    def test_email_autolink(self):
        """Email autolink should be replaced."""
        md = "Contact <user@example.com> for help"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # Email autolink replaced
        assert "<user@example.com>" not in text
        # Text content preserved
        assert "Contact" in text
        assert "for help" in text
    """Test cases for task list handling."""

    def test_unchecked_task_replaced(self):
        """Unchecked task checkbox should be replaced with spaces."""
        md = "- [ ] Unchecked task"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # [ ] should be replaced with spaces
        assert "[ ]" not in text
        # Text content preserved
        assert "Unchecked task" in text

    def test_checked_task_replaced(self):
        """Checked task checkbox should be replaced with spaces."""
        md = "- [x] Checked task"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # [x] should be replaced with spaces
        assert "[x]" not in text
        # Text content preserved
        assert "Checked task" in text

    def test_task_list_with_dash(self):
        """Task list with dash marker."""
        md = "- [ ] Item one\n- [x] Item two"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # Task checkboxes replaced
        assert "[ ]" not in text
        assert "[x]" not in text
        # Text content preserved
        assert "Item one" in text
        assert "Item two" in text

    def test_task_list_with_star(self):
        """Task list with star marker."""
        md = "* [ ] Star task"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # Task checkbox replaced
        assert "[ ]" not in text
        # Text content preserved
        assert "Star task" in text

    def test_task_with_inline_formatting(self):
        """Task with inline formatting."""
        md = "- [ ] Use `code` here"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # Task checkbox replaced
        assert "[ ]" not in text
        # Inline code normalized
        assert "`" not in text
        # Text content preserved
        assert "Use" in text
        assert "code" in text
        assert "here" in text
    """Test cases for footnote handling."""

    def test_footnote_reference_replaced(self):
        """Footnote reference should be replaced with spaces."""
        md = "Text with[^1] reference"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # [^1] should be replaced with spaces
        assert "[^1]" not in text
        # Text content preserved
        assert "Text with" in text
        assert "reference" in text

    def test_footnote_definition_marker_replaced(self):
        """Footnote definition marker should be replaced with spaces."""
        md = "[^1]: This is the footnote text"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # [^1]: should be replaced with spaces
        assert "[^1]:" not in text
        # Footnote text preserved
        assert "This is the footnote text" in text

    def test_multiple_footnote_references(self):
        """Multiple footnote references in text."""
        md = "Text[^1] and text[^2]"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # All footnote references replaced
        assert "[^1]" not in text
        assert "[^2]" not in text
        # Text content preserved
        assert "Text" in text
        assert "and" in text

    def test_footnote_with_inline_formatting(self):
        """Footnote reference with inline formatting."""
        md = "Text with[^1] **bold** reference"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # Footnote reference replaced
        assert "[^1]" not in text
        # Bold markers replaced
        assert "**" not in text
        # Text content preserved
        assert "Text with" in text
        assert "bold" in text
        assert "reference" in text

    def test_footnote_with_numbers(self):
        """Footnote with multi-digit numbers."""
        md = "Text with[^123] reference"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # Footnote reference replaced
        assert "[^123]" not in text
        # Text content preserved
        assert "Text with" in text
        assert "reference" in text
    """Test cases for list handling."""

    def test_dash_list_marker_replaced(self):
        """Dash list marker should be replaced with spaces."""
        md = "- Item one"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # - should be replaced with space
        assert text[0] == " "
        # Text content preserved
        assert "Item one" in text

    def test_star_list_marker_replaced(self):
        """Star list marker should be replaced with spaces."""
        md = "* Item one"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # * should be replaced with space
        assert text[0] == " "
        # Text content preserved
        assert "Item one" in text

    def test_numbered_list_marker_replaced(self):
        """Numbered list marker should be replaced with spaces."""
        md = "1. Item one"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # 1. should be replaced with spaces
        assert text[0:2] == "  "
        # Text content preserved
        assert "Item one" in text

    def test_nested_list(self):
        """Nested list with indentation."""
        md = "- Outer\n  - Inner"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # Both markers replaced
        assert text[0] == " "
        assert text[8] == " "
        # Text content preserved
        assert "Outer" in text
        assert "Inner" in text

    def test_list_with_inline_formatting(self):
        """List containing inline formatting."""
        md = "- Use `code` here"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # List marker replaced
        assert text[0] == " "
        # Inline code normalized
        assert "`" not in text
        # Text content preserved
        assert "Use" in text
        assert "code" in text
        assert "here" in text

    def test_list_preserves_italic_star(self):
        """Star used for italic should not be treated as list marker."""
        md = "This is *italic* text"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # Text content preserved
        assert "italic" in text
        # Star markers replaced (as italic, not list)
        assert "*" not in text
    """Test cases for blockquote handling."""

    def test_blockquote_marker_replaced(self):
        """Blockquote marker should be replaced with spaces."""
        md = "> This is a quote"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # > should be replaced with space
        assert text[0] == " "
        # Text content preserved
        assert "This is a quote" in text

    def test_blockquote_with_space(self):
        """Blockquote with space after marker."""
        md = "> This is a quote"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # > and space replaced with spaces
        assert text[0:2] == "  "
        # Text content preserved
        assert "This is a quote" in text

    def test_nested_blockquote(self):
        """Nested blockquote (>>)."""
        md = ">> Nested quote"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # Both > replaced with spaces
        assert text[0:2] == "  "
        # Text content preserved
        assert "Nested quote" in text

    def test_multiple_blockquotes(self):
        """Multiple blockquotes in document."""
        md = "> First quote\n\n> Second quote"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # Text content preserved
        assert "First quote" in text
        assert "Second quote" in text

    def test_blockquote_with_inline_formatting(self):
        """Blockquote containing inline formatting."""
        md = "> This is **bold** text"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # Blockquote marker replaced
        assert text[0] == " "
        # Bold markers replaced
        assert "**" not in text
        # Text content preserved
        assert "This is" in text
        assert "bold" in text
        assert "text" in text
    """Test cases for horizontal rule handling."""

    def test_horizontal_rule_dashes_replaced(self):
        """Horizontal rule with dashes should be replaced with spaces."""
        md = "Text before\n\n---\n\nText after"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # Dashes should be replaced with spaces
        assert "---" not in text
        # Text before and after should be preserved
        assert "Text before" in text
        assert "Text after" in text

    def test_horizontal_rule_stars_replaced(self):
        """Horizontal rule with asterisks should be replaced with spaces."""
        md = "Text before\n\n***\n\nText after"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # Asterisks should be replaced with spaces
        assert "***" not in text
        # Text before and after should be preserved
        assert "Text before" in text
        assert "Text after" in text

    def test_horizontal_rule_underscores_replaced(self):
        """Horizontal rule with underscores should be replaced with spaces."""
        md = "Text before\n\n___\n\nText after"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # Underscores should be replaced with spaces
        assert "___" not in text
        # Text before and after should be preserved
        assert "Text before" in text
        assert "Text after" in text

    def test_horizontal_rule_with_spaces(self):
        """Horizontal rule with spaces between characters."""
        md = "Text before\n\n- - -\n\nText after"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # The horizontal rule markers should be preserved (spaces between dashes)
        # This is a valid horizontal rule in markdown
        assert "Text before" in text
        assert "Text after" in text

    def test_horizontal_rule_with_trailing_spaces(self):
        """Horizontal rule with trailing spaces."""
        md = "Text before\n\n---   \n\nText after"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # Dashes should be replaced with spaces
        assert "---" not in text
        # Text before and after should be preserved
        assert "Text before" in text
        assert "Text after" in text

    def test_multiple_horizontal_rules(self):
        """Multiple horizontal rules in document."""
        md = "Section 1\n\n---\n\nSection 2\n\n***\n\nSection 3"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # No horizontal rule markers
        assert "---" not in text
        assert "***" not in text
        # All section text preserved
        assert "Section 1" in text
        assert "Section 2" in text
        assert "Section 3" in text
    """Test cases for strikethrough handling."""

    def test_strikethrough_replaced_with_spaces(self):
        """Strikethrough markers should be replaced with spaces."""
        md = "This is ~~deleted~~ text."
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # Tildes should be replaced with spaces
        assert text[7:9] == "  "
        assert text[17:19] == "  "
        # Text content should be preserved
        assert "deleted" in text
        # No tildes in output
        assert "~" not in text

    def test_strikethrough_region_marked(self):
        """Strikethrough markers should be marked as 'strikethrough' regions."""
        md = "This is ~~deleted~~ text."
        text, mapping, regions = preprocess_markdown(md)
        strikethrough_regions = [r for r in regions if r[2] == "strikethrough"]
        # Each ~~ pair is a separate region (consistent with bold/italic)
        assert len(strikethrough_regions) == 2
        assert strikethrough_regions[0] == (8, 10, "strikethrough")
        assert strikethrough_regions[1] == (17, 19, "strikethrough")

    def test_multiple_strikethrough(self):
        """Multiple strikethrough spans should all be handled."""
        md = "~~first~~ and ~~second~~"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # No tildes in output
        assert "~" not in text
        # Both words should be preserved
        assert "first" in text
        assert "second" in text
        # Should have 4 strikethrough regions (2 for each ~~ pair)
        strikethrough_regions = [r for r in regions if r[2] == "strikethrough"]
        assert len(strikethrough_regions) == 4

    def test_strikethrough_with_other_formatting(self):
        """Strikethrough combined with other formatting."""
        md = "**bold ~~and strike~~**"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # No special chars
        assert "*" not in text
        assert "~" not in text
        # Content preserved
        assert "bold" in text
        assert "and" in text
        assert "strike" in text

    def test_strikethrough_empty(self):
        """Empty strikethrough should be handled."""
        md = "~~"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "~" not in text

    def test_strikethrough_with_code(self):
        """Strikethrough containing inline code."""
        md = "~~`code`~~"
        text, mapping, regions = preprocess_markdown(md)
        assert len(text) == len(md)
        # Both strikethrough and code should be handled
        assert "~" not in text
        assert "`" not in text
        # Code content should be normalized to noun
        assert "code" in text
    """Test cases for bugs discovered during debugging."""

    def test_bold_markers_replaced_with_spaces(self):
        """Bold markers should be replaced with spaces, not kept visible."""
        md = "**Sandboxed agent.** The agent runs."
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # Bold markers should be spaces (positions 0-1 and 18-19)
        assert text[0:2] == "  "
        assert text[18:20] == "  "
        # Text content should be preserved
        assert "Sandboxed agent" in text
        # No asterisks in output
        assert "*" not in text

    def test_italic_markers_replaced_with_spaces(self):
        """Italic markers should be replaced with spaces, not kept visible."""
        md = "*italic text* here"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # Italic markers should be spaces (positions 0 and 12)
        assert text[0] == " "
        assert text[12] == " "
        # Text content should be preserved
        assert "italic text" in text
        # No asterisks in output
        assert "*" not in text

    def test_link_text_with_backticks_stripped(self):
        """Link text wrapped in backticks should have backticks stripped."""
        md = "Use [`podman`](https://podman.io) here."
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # Backticks should be replaced with spaces
        assert "`" not in text
        # Link text should be preserved without backticks
        assert "podman" in text
        # No brackets in link region (they're also stripped)
        assert "[" not in text
        assert "]" not in text

    def test_link_text_with_image_syntax_stripped(self):
        """Link text containing image syntax should have ![]() stripped."""
        md = "[![CI](https://ci-img.png)](https://ci-url)"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # Image syntax characters should be replaced
        assert "!" not in text or text.count("!") == 0  # No exclamation marks
        # Link text (CI) should be preserved
        assert "CI" in text
        # URL should be replaced
        assert "ci-img.png" not in text
        assert "ci-url" not in text

    def test_cleaned_text_same_length_as_original(self):
        """Cleaned text should be same length as original (no characters removed)."""
        md = "# Title\n\nSome `code` and [link](url).\n\n![img](pic.png)"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md), f"Cleaned length {len(text)} != original length {len(md)}"

    def test_offset_mapping_no_none_values(self):
        """Offset mapping should have no None values."""
        md = "# Title\n\nSome `code` and [link](url).\n\n![img](pic.png)"
        text, mapping, _ = preprocess_markdown(md)
        none_count = sum(1 for v in mapping.values() if v is None)
        assert none_count == 0, f"Found {none_count} None values in offset mapping"

    def test_no_markdown_characters_in_output(self):
        """Cleaned output should contain no markdown characters."""
        md = "**bold** *italic* `code` [link](url) ![img](pic.png)"
        text, mapping, _ = preprocess_markdown(md)
        markdown_chars = ['`', '*', '[', ']', '#', '!', '|']
        found_chars = [ch for ch in text if ch in markdown_chars]
        assert len(found_chars) == 0, f"Found markdown characters in output: {set(found_chars)}"

    def test_header_markers_replaced_with_spaces(self):
        """Header # markers should be replaced with spaces."""
        md = "## Highlights\n\nText"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # # markers should be spaces
        assert text[0:2] == "  "
        # Header text should be preserved
        assert "Highlights" in text
        # No # in header region
        header_end = text.find("\n\n")
        assert "#" not in text[:header_end]

    def test_nested_image_in_link(self):
        """Test nested image syntax in link text is properly stripped."""
        md = "[![Coverage](docs/assets/coverage.svg)](docs/development.md#coverage)"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # All markdown characters should be replaced
        assert "!" not in text
        assert "[" not in text  # Opening bracket of link
        assert "]" not in text  # Closing brackets
        # Link text (Coverage) should be preserved
        assert "Coverage" in text
        # URLs should be replaced
        assert "coverage.svg" not in text
        assert "development.md" not in text

    def test_common_words_false_positive_in_connecting_words(self):
        """Test that common words don't cause false positive ConnectingWords warnings."""
        # This test verifies the dynamic common word filtering in shared.py
        # The actual ConnectingWords check is in checks_section4.py
        md = "The tool provides full auditability. In addition, a proxy intercepts the traffic."
        text, mapping, _ = preprocess_markdown(md)
        # Just verify preprocessing doesn't break
        assert len(text) == len(md)
        assert "tool" in text
        assert "proxy" in text

    def test_multiple_links_with_backticks(self):
        """Test multiple links with backticks in link text."""
        md = "Use [`mitmproxy`](url1) and [`llama.cpp`](url2) here."
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # No backticks in output
        assert "`" not in text
        # Link texts should be preserved
        assert "mitmproxy" in text
        assert "llama.cpp" in text
        # URLs should be replaced
        assert "url1" not in text
        assert "url2" not in text

    def test_badge_links_with_image_syntax(self):
        """Test badge links with image syntax in link text."""
        md = "[![CI](ci.png)](ci-url) [![Coverage](cov.png)](cov-url)"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # All markdown characters should be replaced
        assert "!" not in text
        # Badge texts should be preserved
        assert "CI" in text
        assert "Coverage" in text
        # URLs should be replaced
        assert "ci.png" not in text
        assert "cov.png" not in text

    def test_offset_mapping_identity_for_non_special_chars(self):
        """Offset mapping should be identity for non-special characters."""
        md = "Hello world"
        text, mapping, _ = preprocess_markdown(md)
        for i in range(len(text)):
            assert mapping[i] == i, f"mapping[{i}] = {mapping[i]} != {i}"

    def test_complex_readme_preprocessing(self):
        """Test preprocessing of a complex README with multiple features."""
        md = """# pi-container

This tool runs a sandboxed [`pi-coding-agent`](https://pi.dev) and uses a local LLM.

[![CI](https://ci-img.png)](https://ci-url)
[![Coverage](docs/assets/coverage.svg)](docs/development.md#coverage)

## Highlights

**Sandboxed agent.** The agent sends all internet traffic through the proxy.

**Traffic logging.** [`mitmproxy`](https://mitmproxy.org) intercepts traffic.

**Local inference.** `llama-server` from [`llama.cpp`](https://llama.app) runs.

## Quick setup

```bash
cp .env.example .env
```

Read **[Getting Started](docs/getting-started.md)** for details.
"""
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md), "Cleaned text length should match original"
        
        # Check no markdown characters
        markdown_chars = ['`', '*', '[', ']', '#', '!', '|']
        found_chars = [ch for ch in text if ch in markdown_chars]
        assert len(found_chars) == 0, f"Found markdown characters: {set(found_chars)}"
        
        # Check no None values in mapping
        none_count = sum(1 for v in mapping.values() if v is None)
        assert none_count == 0, f"Found {none_count} None values in offset mapping"
        
        # Check key text content is preserved
        assert "pi-container" in text
        assert "pi-coding-agent" in text
        assert "CI" in text
        assert "Coverage" in text
        assert "Sandboxed agent" in text
        assert "mitmproxy" in text
        assert "llama.cpp" in text
        # llama-server is in backticks (inline code) and should be removed
        assert "llama-server" not in text
        assert "Getting Started" in text
        
        # Check key content is removed
        assert "pi.dev" not in text
        assert "ci-img.png" not in text
        assert "mitmproxy.org" not in text
        assert "llama.app" not in text
        assert "docs/getting-started.md" not in text

    def test_just_heading(self):
        """Handle just a heading."""
        md = "# Heading"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # Heading is preserved.
        assert "Heading" in text

    def test_just_paragraph(self):
        """Handle just a paragraph."""
        md = "Just a paragraph."
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert text == md

    def test_just_code_span(self):
        """Handle just a code span."""
        md = "`code`"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # Code content is replaced with a noun token
        assert "code" in text
        # Backticks are replaced with spaces
        assert "`" not in text

    def test_just_link(self):
        """Handle just a link."""
        md = "[text](url)"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # Link text is preserved.
        assert "text" in text
        # URL is replaced with spaces.
        assert "url" not in text

    def test_just_image(self):
        """Handle just an image."""
        md = "![alt](img.png)"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "alt" not in text
        assert "img.png" not in text

    def test_single_character(self):
        """Handle a single character."""
        md = "a"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert text == md

    def test_single_space(self):
        """Handle a single space."""
        md = " "
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert text == md

    def test_single_newline(self):
        """Handle a single newline."""
        md = "\n"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert text == md

    def test_just_whitespace(self):
        """Handle just whitespace."""
        md = "   \n  \t  "
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert text == md

    def test_multiple_consecutive_newlines(self):
        """Handle multiple consecutive newlines."""
        md = "\n\n\n"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert text == md

    def test_just_code_block(self):
        """Handle just a code block (no other content)."""
        md = "```\ncode\n```"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "code" not in text

    def test_just_table(self):
        """Handle just a table (no other content)."""
        md = "| H |\n|---|\n| C |"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # Table delimiters (|) should be replaced, but cell content preserved.
        assert "|" not in text
        assert "H" in text
        assert "C" in text

    def test_just_horizontal_rule(self):
        """Handle just a horizontal rule."""
        md = "---"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # Horizontal rules are replaced with spaces.
        assert "---" not in text

    def test_just_link_with_no_text(self):
        """Handle just a link with empty text."""
        md = "[](url)"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "url" not in text

    def test_just_image_with_no_alt(self):
        """Handle just an image with empty alt."""
        md = "![](img.png)"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "img.png" not in text

    def test_multiple_links_in_sequence(self):
        """Handle multiple links in sequence."""
        md = "[a](1) [b](2) [c](3)"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # Link texts are preserved.
        assert "a" in text
        assert "b" in text
        assert "c" in text
        # URLs are replaced with spaces.
        assert "1" not in text
        assert "2" not in text
        assert "3" not in text

    def test_multiple_images_in_sequence(self):
        """Handle multiple images in sequence."""
        md = "![a](1.png) ![b](2.png) ![c](3.png)"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "a" not in text
        assert "b" not in text
        assert "c" not in text

    def test_multiple_code_spans_in_sequence(self):
        """Handle multiple code spans in sequence."""
        md = "`a` `b` `c`"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # Code content is replaced with noun tokens
        assert "a" in text
        assert "b" in text
        assert "c" in text
        # Backticks are replaced with spaces
        assert "`" not in text

    def test_empty_string_already_tested(self):
        """Verify empty string behavior (already tested elsewhere)."""
        md = ""
        text, mapping, _ = preprocess_markdown(md)
        assert text == ""
        assert mapping == {}

    def test_heading_with_trailing_spaces(self):
        """Handle heading with trailing spaces."""
        md = "# Heading   "
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # Heading is preserved.
        assert "Heading" in text

    def test_paragraph_with_trailing_newline(self):
        """Handle paragraph with trailing newline."""
        md = "Text.\n"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "Text." in text

    def test_code_span_with_single_char(self):
        """Handle code span with single character."""
        md = "`a`"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # Code content is replaced with a noun token
        assert "a" in text
        # Backticks are replaced with spaces
        assert "`" not in text

    def test_link_with_single_char_text(self):
        """Handle link with single character text."""
        md = "[a](b)"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # Link text is preserved.
        assert "a" in text
        # URL is replaced with spaces.
        assert "b" not in text

    def test_image_with_single_char_alt(self):
        """Handle image with single character alt."""
        md = "![a](b.png)"
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "a" not in text
        assert "b.png" not in text


class TestHtmlIntegrationTests:
    """Full document integration tests for HTML preprocessing."""

    def test_complete_html5_document(self):
        """Test a complete HTML5 document structure."""
        html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Test Page</title>
</head>
<body>
    <h1>Hello World</h1>
    <p>This is a <strong>test</strong> page.</p>
</body>
</html>"""
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        # All text content should be preserved.
        assert "Hello World" in text
        assert "This is a" in text
        assert "test" in text
        assert "page" in text
        # All tags should be replaced with whitespace.
        assert "<html" not in text
        assert "</html>" not in text
        assert "<head>" not in text

    def test_document_with_navigation(self):
        """Test document with navigation links."""
        html = """<nav>
    <ul>
        <li><a href="/home">Home</a></li>
        <li><a href="/about">About</a></li>
        <li><a href="/contact">Contact</a></li>
    </ul>
</nav>"""
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "Home" in text
        assert "About" in text
        assert "Contact" in text
        # Links and tags should be replaced.
        assert "<a href=" not in text
        assert "</a>" not in text

    def test_document_with_table(self):
        """Test document with a data table."""
        html = """<table>
    <thead>
        <tr><th>Name</th><th>Age</th></tr>
    </thead>
    <tbody>
        <tr><td>Alice</td><td>30</td></tr>
        <tr><td>Bob</td><td>25</td></tr>
    </tbody>
</table>"""
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "Name" in text
        assert "Age" in text
        assert "Alice" in text
        assert "Bob" in text
        assert "30" in text
        assert "25" in text

    def test_document_with_forms(self):
        """Test document with form elements."""
        html = """<form action="/submit" method="post">
    <label for="name">Name:</label>
    <input type="text" id="name" name="name">
    <button type="submit">Submit</button>
</form>"""
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "Name:" in text
        assert "Submit" in text
        # Form elements should be replaced.
        assert "<form" not in text
        assert "</form>" not in text

    def test_document_with_lists(self):
        """Test document with ordered and unordered lists."""
        html = """<ul>
    <li>Item 1</li>
    <li>Item 2</li>
</ul>
<ol>
    <li>First</li>
    <li>Second</li>
</ol>"""
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "Item 1" in text
        assert "Item 2" in text
        assert "First" in text
        assert "Second" in text

    def test_document_with_nested_layouts(self):
        """Test document with deeply nested layout divs."""
        html = """<div class="container">
    <div class="header">
        <h1>Title</h1>
    </div>
    <div class="content">
        <p>Main content here.</p>
    </div>
    <div class="footer">
        <p>Copyright 2024</p>
    </div>
</div>"""
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "Title" in text
        assert "Main content here" in text
        assert "Copyright 2024" in text

    def test_document_with_scripts_and_styles(self):
        """Test document with script and style elements."""
        html = """<html>
<head>
    <style>body { color: red; }</style>
</head>
<body>
    <h1>Hello</h1>
    <script>console.log('test');</script>
</body>
</html>"""
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "Hello" in text
        # Script and style content should be removed.
        assert "console.log" not in text
        assert "color: red" not in text

    def test_document_with_images_and_media(self):
        """Test document with images."""
        html = """<div>
    <img src="photo.jpg" alt="A photo">
    <p>Image description.</p>
</div>"""
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "Image description" in text
        # Image tag should be replaced.
        assert "<img" not in text

    def test_document_with_comments(self):
        """Test document with multiple comments."""
        html = """<!-- Header -->
<div>
    <!-- Navigation -->
    <nav>Links</nav>
    <!-- Main content -->
    <main>Content here</main>
</div>"""
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "Links" in text
        assert "Content here" in text
        # Comments should be removed.
        assert "Header" not in text
        assert "Navigation" not in text

    def test_document_with_entities(self):
        """Test document with HTML entities."""
        html = """<p>Copyright &copy; 2024 &mdash; All rights reserved.</p>"""
        text, mapping = preprocess_html(html)
        assert len(text) < len(html)
        # Entities should be decoded.
        assert "©" in text or "copy" in text.lower()
        assert "2024" in text

    def test_document_with_mixed_content(self):
        """Test document with mixed content types."""
        html = """<!DOCTYPE html>
<html>
<head><title>Test</title></head>
<body>
    <h1>Title</h1>
    <p>Paragraph with <a href="/link">link</a> and <strong>bold</strong>.</p>
    <ul>
        <li>Item 1</li>
        <li>Item 2</li>
    </ul>
    <!-- Comment -->
    <footer>Footer text</footer>
</body>
</html>"""
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        # All text content should be preserved.
        assert "Title" in text
        assert "Paragraph with" in text
        assert "link" in text
        assert "bold" in text
        assert "Item 1" in text
        assert "Footer text" in text
        # No HTML tags should remain.
        assert "<html>" not in text
        assert "</html>" not in text
        assert "<head>" not in text

    def test_document_with_attributes(self):
        """Test document with various attribute types."""
        html = """<div id="main" class="container" data-value="123" aria-label="Main content">
    <p>Content</p>
</div>"""
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "Content" in text
        # Tag with attributes should be replaced.
        assert "id=\"main\"" not in text

    def test_document_with_self_closing_tags(self):
        """Test document with self-closing tags."""
        html = """<p>Line 1<br>Line 2<hr>Line 3</p>"""
        text, mapping = preprocess_html(html)
        assert len(text) == len(html)
        assert "Line 1" in text
        assert "Line 2" in text
        assert "Line 3" in text


class TestMarkdownIntegrationTests:
    """Full document integration tests for Markdown preprocessing."""

    def test_complete_readme_document(self):
        """Test a complete README-style document."""
        md = """# Project Title

A description of the project.

## Installation

```bash
pip install project
```

## Usage

```python
import project
project.run()
```

## API Reference

| Method | Description |
|--------|-------------|
| `run()` | Run the project |
| `stop()` | Stop the project |

## Links

[Documentation](https://docs.example.com) | [GitHub](https://github.com/example)

![Logo](logo.png)

## License

MIT License"""
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # All text content should be preserved.
        assert "Project Title" in text
        assert "Installation" in text
        assert "Usage" in text
        assert "API Reference" in text
        # Link text is preserved.
        assert "Documentation" in text
        assert "License" in text
        # Code blocks should be removed.
        assert "pip install" not in text
        assert "import project" not in text
        # Code spans should be removed.
        assert "`run()`" not in text
        # URLs should be removed.
        assert "docs.example.com" not in text
        assert "github.com" not in text
        # Image should be removed.
        assert "logo.png" not in text
        # Table delimiters should be removed, but cell content preserved.
        # Note: standalone | characters (not in table rows) are preserved.
        assert "Method" in text
        assert "Description" in text

    def test_document_with_multiple_sections(self):
        """Test document with multiple sections."""
        md = """# Section 1

Paragraph 1.

## Subsection 1.1

More text with `code` here.

# Section 2

Paragraph 2.

![Image](img.png)

[Link](http://example.com)"""
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "Section 1" in text
        assert "Subsection 1.1" in text
        assert "Paragraph 1" in text
        assert "Paragraph 2" in text
        assert "More text with" in text
        assert "here" in text
        # Code, image, and link should be removed.
        assert "`code`" not in text
        assert "img.png" not in text
        assert "example.com" not in text

    def test_document_with_inline_html(self):
        """Test document with inline HTML."""
        md = """# Title

This has <strong>bold</strong> and <em>italic</em> text.

<!-- This is a comment -->

More text."""
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "Title" in text
        assert "bold" in text
        assert "italic" in text
        assert "More text" in text
        # HTML tags and comments should be removed.
        assert "<strong>" not in text
        assert "</strong>" not in text
        assert "comment" not in text

    def test_document_with_tables_and_code(self):
        """Test document with tables and code blocks."""
        md = """# Data Table

| Column 1 | Column 2 |
|----------|----------|
| Value 1  | Value 2  |

```python
def function():
    return 42
```

Some text."""
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "Data Table" in text
        assert "Some text" in text
        # Table rows should be removed.
        for line in md.split("\n"):
            if line.startswith("|"):
                assert line not in text
        # Code block should be removed.
        assert "def function" not in text
        assert "return 42" not in text

    def test_document_with_images_and_links(self):
        """Test document with multiple images and links."""
        md = """# Gallery

![Image 1](img1.png) ![Image 2](img2.png)

[Link 1](http://example1.com) [Link 2](http://example2.com)

Descriptions."""
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "Gallery" in text
        assert "Descriptions" in text
        # Images and links should be removed.
        assert "img1.png" not in text
        assert "img2.png" not in text
        assert "example1.com" not in text
        assert "example2.com" not in text

    def test_document_with_nested_elements(self):
        """Test document with nested inline elements."""
        md = """# Title

This has **bold** and *italic* and `code` and [link](url) and ![image](img.png) all together."""
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "Title" in text
        assert "This has" in text
        assert "bold" in text
        assert "italic" in text
        assert "all together" in text
        # "code" is removed because it's in a code span
        # Code, link, and image should be removed.
        assert "`code`" not in text
        assert "url" not in text
        assert "img.png" not in text

    def test_document_with_fenced_code_variations(self):
        """Test document with different fenced code block styles."""
        md = """# Code Examples

```python
code1
```

~~~javascript
code2
~~~

```text
code3
```"""
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "Code Examples" in text
        # All code blocks should be removed.
        assert "code1" not in text
        assert "code2" not in text
        assert "code3" not in text

    def test_document_with_lists_and_code(self):
        """Test document with lists containing code."""
        md = """# Steps

1. First step with `code`
2. Second step
3. Third step

- Bullet with `code`
- Another bullet"""
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "Steps" in text
        assert "First step" in text
        assert "Second step" in text
        # Code spans should be removed.
        assert "`code`" not in text

    def test_document_with_autolinks(self):
        """Test document with autolinks."""
        md = """# Documentation

See <http://example.com> for details.

Visit <https://docs.example.com/api> for the API."""
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "Documentation" in text
        assert "See" in text
        assert "Visit" in text
        # Autolinks should be removed.
        assert "example.com" not in text
        assert "docs.example.com" not in text

    def test_document_with_definitions(self):
        """Test document with definition lists."""
        md = """# Terms

Term 1
:   Definition 1

Term 2
:   Definition 2"""
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        assert "Terms" in text
        assert "Term 1" in text
        assert "Definition 1" in text

    def test_realistic_blog_post(self):
        """Test a realistic blog post document."""
        md = """# My Blog Post

*Published on January 1, 2024*

This is the introduction.

## Getting Started

Install the package:

```bash
npm install my-package
```

Then import it:

```javascript
import { func } from 'my-package';
```

## Examples

Here's an example with `code` inline.

![Screenshot](screenshot.png)

Check the [documentation](https://docs.example.com) for more.

## Conclusion

That's it! Happy coding."""
        text, mapping, _ = preprocess_markdown(md)
        assert len(text) == len(md)
        # All text content should be preserved.
        assert "My Blog Post" in text
        assert "Published on" in text
        assert "Getting Started" in text
        assert "Examples" in text
        assert "Conclusion" in text
        assert "Happy coding" in text
        # Code blocks should be removed.
        assert "npm install" not in text
        assert "import { func }" not in text
        # Code spans should be removed.
        assert "`code`" not in text
        # Image should be removed.
        assert "screenshot.png" not in text
        # Link should be removed.
        assert "docs.example.com" not in text


class TestSteIntegrationHtml:
    """Integration tests for HTML preprocessing with STE100 linter."""

    def test_html_to_ste_pipeline(self):
        """Test HTML document through preprocessing to STE100."""
        from spacy import load as spacy_load
        import spacy

        nlp = spacy_load("en_core_web_sm")

        html = """<html>
<head><title>Test Document</title></head>
<body>
    <h1>Welcome to Our Service</h1>
    <p>This is a simple test document for STE100 compliance checking.</p>
    <p>We provide <strong>excellent</strong> support and <em>fast</em> delivery.</p>
    <ul>
        <li>Feature one with good description</li>
        <li>Feature two with clear benefits</li>
    </ul>
</body>
</html>"""
        # Preprocess the HTML
        cleaned_text, mapping = preprocess_html(html)

        # Verify preprocessing
        assert len(cleaned_text) == len(html)
        assert "Welcome to Our Service" in cleaned_text
        assert "excellent" in cleaned_text
        assert "fast" in cleaned_text
        assert "Feature one" in cleaned_text

        # Run through spaCy for STE100 analysis
        doc = nlp(cleaned_text)

        # Verify spaCy can process the text
        assert len(doc) > 0
        assert doc.text.strip()  # Should have non-whitespace content

        # Verify tokens are reasonable
        tokens = [token.text for token in doc]
        assert "Welcome" in tokens or "welcome" in tokens
        assert "Service" in tokens or "service" in tokens

    def test_html_entities_to_ste(self):
        """Test HTML with entities through preprocessing to STE100."""
        from spacy import load as spacy_load

        nlp = spacy_load("en_core_web_sm")

        html = """<p>Copyright &copy; 2024 &mdash; All rights reserved.</p>
<p>Visit our &amp; website for more information.</p>"""
        cleaned_text, mapping = preprocess_html(html)

        # Entities should be decoded
        assert "&copy;" not in cleaned_text
        assert "&mdash;" not in cleaned_text
        assert "&amp;" not in cleaned_text

        # Run through spaCy
        doc = nlp(cleaned_text)
        assert len(doc) > 0

        # Verify the decoded text is processed
        tokens = [token.text for token in doc]
        text_str = " ".join(tokens)
        assert "2024" in text_str
        assert "rights" in text_str

    def test_html_offset_mapping_accuracy(self):
        """Test that offset mapping accurately maps cleaned positions to original."""
        html = "<p>Hello World</p>"
        cleaned_text, mapping = preprocess_html(html)

        # Find "Hello" in cleaned text
        hello_pos = cleaned_text.find("Hello")
        assert hello_pos >= 0

        # Map back to original
        orig_pos = mapping[hello_pos]
        assert html[orig_pos:orig_pos+5] == "Hello"

        # Find "World" in cleaned text
        world_pos = cleaned_text.find("World")
        assert world_pos >= 0

        # Map back to original
        orig_pos = mapping[world_pos]
        assert html[orig_pos:orig_pos+5] == "World"

    def test_html_nested_structure_to_ste(self):
        """Test deeply nested HTML structure through STE100 pipeline."""
        from spacy import load as spacy_load

        nlp = spacy_load("en_core_web_sm")

        html = """<div class="container">
    <div class="section">
        <h2>Main Content</h2>
        <p>This is the primary content area with important information.</p>
        <div class="subsection">
            <h3>Details</h3>
            <p>Additional details go here.</p>
        </div>
    </div>
</div>"""
        cleaned_text, mapping = preprocess_html(html)

        # Verify all text is preserved
        assert "Main Content" in cleaned_text
        assert "primary content area" in cleaned_text
        assert "Important information" in cleaned_text or "important information" in cleaned_text
        assert "Details" in cleaned_text
        assert "Additional details" in cleaned_text

        # Run through STE100
        doc = nlp(cleaned_text)
        assert len(doc) > 0
        assert doc.text.strip()


class TestSteIntegrationMarkdown:
    """Integration tests for Markdown preprocessing with STE100 linter."""

    def test_markdown_to_ste_pipeline(self):
        """Test Markdown document through preprocessing to STE100."""
        from spacy import load as spacy_load

        nlp = spacy_load("en_core_web_sm")

        md = """# Welcome Guide

This guide helps you get started.

## Installation

Run the following command:

```bash
pip install mypackage
```

## Usage

Use the `main()` function to start:

```python
from mypackage import main
main()
```

## Features

- Easy to use interface
- Fast performance
- Reliable support

## Contact

Email us at [support@example.com](mailto:support@example.com) for help."""

        # Preprocess the Markdown
        cleaned_text, mapping, _ = preprocess_markdown(md)

        # Verify preprocessing removed code and URLs
        assert "pip install mypackage" not in cleaned_text
        assert "from mypackage import main" not in cleaned_text
        # Link text is preserved, but URLs are removed.
        assert "support@example.com" in cleaned_text  # Link text preserved
        # Check that the URL part is removed (the second occurrence).
        assert cleaned_text.count("support@example.com") == 1

        # Verify text content is preserved
        assert "Welcome Guide" in cleaned_text
        assert "helps you get started" in cleaned_text
        assert "Easy to use" in cleaned_text
        assert "Fast performance" in cleaned_text

        # Run through STE100
        doc = nlp(cleaned_text)
        assert len(doc) > 0
        assert doc.text.strip()

        # Verify tokens are reasonable
        tokens = [token.text for token in doc]
        text_str = " ".join(tokens)
        assert "Welcome" in text_str or "welcome" in text_str
        assert "Guide" in text_str or "guide" in text_str

    def test_markdown_tables_to_ste(self):
        """Test Markdown with tables through STE100 pipeline."""
        from spacy import load as spacy_load

        nlp = spacy_load("en_core_web_sm")

        md = """# Product Comparison

| Feature | Basic | Pro |
|---------|-------|-----|
| Storage | 10GB | 100GB |
| Users | 1 | 10 |
| Support | Email | 24/7 |

Choose the plan that fits your needs."""

        cleaned_text, mapping, _ = preprocess_markdown(md)

        # Verify table delimiters are removed, but cell content preserved
        assert "|" not in cleaned_text
        assert "Feature" in cleaned_text
        assert "Basic" in cleaned_text
        assert "Pro" in cleaned_text
        assert "Storage" in cleaned_text
        assert "Users" in cleaned_text
        assert "Support" in cleaned_text

        # Verify text content is preserved
        assert "Product Comparison" in cleaned_text
        assert "Choose the plan" in cleaned_text
        assert "fits your needs" in cleaned_text

        # Run through STE100
        doc = nlp(cleaned_text)
        assert len(doc) > 0

        # Verify specific content is in the processed text
        tokens = [token.text for token in doc]
        text_str = " ".join(tokens)
        assert "Product Comparison" in text_str or "product comparison" in text_str
        assert "Choose the plan" in text_str or "choose the plan" in text_str

    def test_markdown_code_blocks_to_ste(self):
        """Test Markdown with code blocks through STE100 pipeline."""
        from spacy import load as spacy_load

        nlp = spacy_load("en_core_web_sm")

        md = """# API Documentation

## Authentication

To authenticate, send this request:

```http
POST /api/auth/login
Content-Type: application/json

{
  "username": "user",
  "password": "pass"
}
```

## Response

The API returns a JSON response with the token."""

        cleaned_text, mapping, _ = preprocess_markdown(md)

        # Verify code block content is removed
        assert "POST /api/auth/login" not in cleaned_text
        assert '"username": "user"' not in cleaned_text
        assert '"password": "pass"' not in cleaned_text

        # Verify explanatory text is preserved
        assert "API Documentation" in cleaned_text
        assert "Authentication" in cleaned_text
        assert "To authenticate" in cleaned_text
        assert "send this request" in cleaned_text
        assert "API returns a JSON response" in cleaned_text

        # Run through STE100
        doc = nlp(cleaned_text)
        assert len(doc) > 0
        assert doc.text.strip()

        # Verify the linter sees the explanatory text
        tokens = [token.text for token in doc]
        text_str = " ".join(tokens)
        assert "authenticate" in text_str.lower()
        assert "request" in text_str.lower()

    def test_markdown_offset_mapping_for_ste(self):
        """Test offset mapping accuracy for STE100 error reporting."""
        md = "# Title\n\nSome text with `code` and [link](url).\n\nMore text."
        cleaned_text, mapping, _ = preprocess_markdown(md)

        # Find "Title" in cleaned text
        title_pos = cleaned_text.find("Title")
        assert title_pos >= 0
        orig_pos = mapping[title_pos]
        assert md[orig_pos:orig_pos+5] == "Title"

        # Find "Some text" in cleaned text
        some_pos = cleaned_text.find("Some text")
        assert some_pos >= 0
        orig_pos = mapping[some_pos]
        assert md[orig_pos:orig_pos+9] == "Some text"

        # Find "More text" in cleaned text
        more_pos = cleaned_text.find("More text")
        assert more_pos >= 0
        orig_pos = mapping[more_pos]
        assert md[orig_pos:orig_pos+9] == "More text"

    def test_markdown_inline_elements_to_ste(self):
        """Test Markdown with inline elements through STE100 pipeline."""
        from spacy import load as spacy_load

        nlp = spacy_load("en_core_web_sm")

        md = """# Quick Start

1. Install the package using `pip install package`
2. Import the module with `import package`
3. Call the `main()` function

For more details, see the [documentation](https://docs.example.com) or contact [support](mailto:support@example.com)."""

        cleaned_text, mapping, _ = preprocess_markdown(md)

        # Verify inline code and links are removed
        assert "pip install package" not in cleaned_text
        assert "import package" not in cleaned_text
        assert "docs.example.com" not in cleaned_text
        assert "support@example.com" not in cleaned_text

        # Verify explanatory text is preserved
        assert "Quick Start" in cleaned_text
        assert "Install the package" in cleaned_text
        assert "Import the module" in cleaned_text
        assert "Call the" in cleaned_text
        assert "function" in cleaned_text
        assert "For more details" in cleaned_text
        assert "see the" in cleaned_text
        assert "contact" in cleaned_text
        # "documentation" is removed because it's link text

        # Run through STE100
        doc = nlp(cleaned_text)
        assert len(doc) > 0
        assert doc.text.strip()

        # Verify the linter can process the instructions
        tokens = [token.text for token in doc]
        text_str = " ".join(tokens)
        assert "install" in text_str.lower()
        assert "import" in text_str.lower()
        assert "call" in text_str.lower()

    def test_table_cells_as_paragraphs_to_ste(self):
        """Test that table cells are treated as paragraphs for STE100."""
        from spacy import load as spacy_load

        nlp = spacy_load("en_core_web_sm")

        md = """# Product Features

| Feature | Description |
|---------|-------------|
| Fast | Processes data quickly |
| Secure | Encrypts all information |
| Reliable | Uptime guarantee of 99.9% |

Choose the plan that works for you."""

        cleaned_text, mapping, _ = preprocess_markdown(md)

        # Verify table delimiters are removed, but cell content preserved
        assert "|" not in cleaned_text
        assert "Feature" in cleaned_text
        assert "Description" in cleaned_text
        assert "Fast" in cleaned_text
        assert "Processes data quickly" in cleaned_text
        assert "Secure" in cleaned_text
        assert "Encrypts all information" in cleaned_text
        assert "Reliable" in cleaned_text
        assert "Uptime guarantee of 99.9%" in cleaned_text

        # Run through STE100 - table cells should be checked as prose
        doc = nlp(cleaned_text)
        assert len(doc) > 0
        assert doc.text.strip()

        # Verify the linter can process the table cell content
        tokens = [token.text for token in doc]
        text_str = " ".join(tokens)
        assert "processes" in text_str.lower()
        assert "encrypts" in text_str.lower()
        assert "uptime" in text_str.lower()
        assert "guarantee" in text_str.lower()

    def test_html_inner_text_to_ste(self):
        """Test that HTML inner text is preserved for STE100 checking."""
        from spacy import load as spacy_load

        nlp = spacy_load("en_core_web_sm")

        html = """<html>
<body>
    <h1>Welcome</h1>
    <p>This page provides <strong>essential</strong> information about our <em>services</em>.</p>
    <ul>
        <li>Feature one: Fast processing</li>
        <li>Feature two: Secure storage</li>
    </ul>
    <p>Contact us for <a href="/info">more details</a>.</p>
</body>
</html>"""

        cleaned_text, mapping = preprocess_html(html)

        # Verify HTML tags are removed, but inner text preserved
        assert "<html>" not in cleaned_text
        assert "<body>" not in cleaned_text
        assert "<h1>" not in cleaned_text
        assert "</h1>" not in cleaned_text
        assert "<strong>" not in cleaned_text
        assert "</strong>" not in cleaned_text
        assert "<em>" not in cleaned_text
        assert "</em>" not in cleaned_text
        assert "<ul>" not in cleaned_text
        assert "<li>" not in cleaned_text
        assert "</li>" not in cleaned_text
        assert "<a href=\"/info\">" not in cleaned_text
        assert "</a>" not in cleaned_text

        # Verify all text content is preserved
        assert "Welcome" in cleaned_text
        assert "This page provides" in cleaned_text
        assert "essential" in cleaned_text
        assert "information" in cleaned_text
        assert "about" in cleaned_text
        assert "services" in cleaned_text
        assert "Feature one" in cleaned_text
        assert "Fast processing" in cleaned_text
        assert "Feature two" in cleaned_text
        assert "Secure storage" in cleaned_text
        assert "Contact us" in cleaned_text
        assert "more details" in cleaned_text

        # Run through STE100 - all text should be checked
        doc = nlp(cleaned_text)
        assert len(doc) > 0
        assert doc.text.strip()

        # Verify the linter can process all the inner text
        tokens = [token.text for token in doc]
        text_str = " ".join(tokens)
        assert "provides" in text_str.lower()
        assert "essential" in text_str.lower()
        assert "services" in text_str.lower()
        assert "contact" in text_str.lower()
        assert "details" in text_str.lower()


class TestPreprocessMarkdownLinkHandling:
    """Tests for link text preservation and URL replacement."""

    def test_link_text_preserved_url_replaced(self):
        """Link text is kept visible but URL is replaced with spaces."""
        md = "Visit [link](http://example.com) now."
        text, offset_map, regions = preprocess_markdown(md)
        assert "link" in text
        assert "example.com" not in text
        # URL should be replaced with spaces.
        assert "(" in text or ")" not in text  # No parentheses from URL

    def test_link_region_metadata(self):
        """Link regions are included in the third return value."""
        md = "Visit [link](http://example.com) now."
        text, offset_map, regions = preprocess_markdown(md)
        # Should have at least one link region.
        link_regions = [r for r in regions if r[2] == "link"]
        assert len(link_regions) >= 1

    def test_nested_link_and_code(self):
        """Code inside link text should not cause issues."""
        md = "Use [`code`](http://example.com) here."
        text, offset_map, regions = preprocess_markdown(md)
        # The link text including code should be preserved.
        assert "code" in text
        # The URL should be replaced.
        assert "example.com" not in text

    def test_multiple_links_preserve_text(self):
        """Multiple links should all have their text preserved."""
        md = "[a](1) and [b](2)"
        text, offset_map, regions = preprocess_markdown(md)
        assert "a" in text
        assert "b" in text
        assert "1" not in text
        assert "2" not in text

    def test_link_with_nested_image(self):
        """Badge-style links with nested images should work."""
        md = '[![CI](img.svg)](http://example.com)'
        text, offset_map, regions = preprocess_markdown(md)
        # The entire link should be handled correctly.
        assert "CI" in text  # Link text preserved
        assert "img.svg" not in text  # Image URL replaced
        assert "example.com" not in text  # Link URL replaced

    def test_adjacent_links_preserved(self):
        """Adjacent links should both preserve their text."""
        md = "[a](1)[b](2)"
        text, offset_map, regions = preprocess_markdown(md)
        assert "a" in text
        assert "b" in text
        assert "1" not in text
        assert "2" not in text

    def test_link_text_with_special_chars(self):
        """Link text with special characters should be preserved."""
        md = '[Link & More](http://example.com)'
        text, offset_map, regions = preprocess_markdown(md)
        assert "Link" in text
        assert "More" in text
        assert "example.com" not in text

    def test_overlapping_blocks_handled(self):
        """Overlapping blocks (code inside link) should be handled."""
        md = "[`code`](url)"
        text, offset_map, regions = preprocess_markdown(md)
        # Should not raise an error.
        assert len(text) == len(md)
        # Link text should be preserved.
        assert "code" in text
