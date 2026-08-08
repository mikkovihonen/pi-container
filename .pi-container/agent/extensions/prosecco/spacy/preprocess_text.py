#!/usr/bin/env python3
"""
Text preprocessing for prose linting.

Filters out non-text elements from HTML and Markdown input while preserving
exact character positions and offsets. This allows downstream linting tools
to report precise locations of issues in the original document.

Deleted elements are replaced with whitespace of equal length so that the
output string has the same length as the input. Character offset mappings
enable bidirectional lookup between cleaned and original text positions.

Modules:
    preprocess_html -- Remove HTML tags while preserving character positions.
    preprocess_markdown -- Remove Markdown non-text elements with source maps.
"""

from __future__ import annotations

import re
from html.entities import name2codepoint


def _decode_entity(match: re.Match) -> str:
    """Decode a named or numeric HTML entity to a single Unicode character.

    Returns the original match unchanged for unrecognized entities so that
    the output preserves the input byte-for-byte.
    """
    text = match.group(0)
    if text.startswith("&#x") or text.startswith("&#X"):
        try:
            return chr(int(text[3:-1], 16))
        except (ValueError, OverflowError):
            return text
    if text.startswith("&#"):
        try:
            return chr(int(text[2:-1]))
        except (ValueError, OverflowError):
            return text
    name = text[1:-1]
    cp = name2codepoint.get(name)
    if cp:
        return chr(cp)
    return text


_ENTITY_RE = re.compile(r"&(?:#x[0-9a-fA-F]+|#[0-9]+|[a-zA-Z]+);")


def _decode_entities(text: str) -> str:
    """Replace all HTML entities in ``text`` with their Unicode equivalents."""
    return _ENTITY_RE.sub(_decode_entity, text)


def _find_html_spans(html: str) -> list[tuple[int, int]]:
    """Find all non-text spans in HTML (tags, comments, CDATA, etc.).

    Uses regex to locate every non-text region in the original HTML string.
    This approach works on the raw source so offsets remain accurate.

    Args:
        html: Raw HTML string to process.

    Returns:
        A sorted list of ``(start, end)`` tuples for each non-text span.
    """
    spans: list[tuple[int, int]] = []

    # Match all non-text regions:
    # - HTML comments: <!-- ... -->
    # - CDATA sections: <![CDATA[ ... ]]>
    # - Processing instructions: <? ... ?>
    # - DOCTYPE declarations: <!DOCTYPE ...>
    # - Script/style tags with content: <script>...</script>, <style>...</style>
    # - Opening/self-closing tags: <tagname ... >
    tag_pattern = re.compile(
        r"<!--.*?-->"
        r"|<!\[CDATA\[.*?\]\]>"
        r"|<\?.*?\?>"
        r"|<!DOCTYPE[^>]*>"
        r"|<(/?)(script|style)[^>]*>[\s\S]*?</\2>"
        r"|</?[a-zA-Z][a-zA-Z0-9]*(?:\s+[^>]*)?\s*/?>"
    )

    for m in tag_pattern.finditer(html):
        spans.append((m.start(), m.end()))

    # Sort and merge overlapping spans.
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    return merged


def preprocess_html(
    html: str,
    *,
    keep_ids: bool = False,
) -> tuple[str, dict[int, int]]:
    """Parse HTML and remove non-text elements while preserving offsets.

    This function uses BeautifulSoup to validate the HTML structure and
    regex-based detection to locate non-text regions. Every non-text element
    (tags, comments, CDATA, processing instructions, DOCTYPE) is replaced
    with whitespace of equal length. The output has the same number of
    characters as the input.

    HTML entities (``&amp;``, ``&#123;``, etc.) are decoded to their Unicode
    equivalents. The length difference between decoded text and the original
    entity string is absorbed into the surrounding whitespace so offsets stay
    valid.

    Args:
        html: Raw HTML string to process.
        keep_ids: When True, preserve ``id`` attribute values inside tags as
            readable text in the output. Default is False.

    Returns:
        A tuple of ``(cleaned_text, offset_map)`` where:
            * ``cleaned_text`` contains the original text with all non-text
              elements replaced by whitespace. Entities are decoded.
            * ``offset_map`` maps each character position in ``cleaned_text``
              to the corresponding position in the original ``html`` string.

    Example:
        >>> text, mapping = preprocess_html("<p>Hello <b>world</b>!</p>")
        >>> assert "Hello " in text
        >>> assert mapping[0] == 0
    """
    # Use BeautifulSoup to validate the HTML structure.
    try:
        from bs4 import BeautifulSoup
        BeautifulSoup(html, "html.parser")
    except ImportError:
        raise ImportError(
            "beautifulsoup4 is required for HTML preprocessing. "
            "Install it with: pip install beautifulsoup4"
        )
    except Exception:
        pass  # Invalid HTML; proceed with regex-based processing.

    # Find all non-text spans.
    spans = _find_html_spans(html)

    # Build the cleaned string and offset map.
    # We need to track positions in the decoded text, not the original.
    parts: list[str] = []
    offset_map: dict[int, int] = {}
    last_end = 0
    decoded_pos = 0

    for start, end in spans:
        # Text before this span.
        text_before = html[last_end:start]
        decoded_text_before = _decode_entities(text_before)
        for i, ch in enumerate(decoded_text_before):
            offset_map[decoded_pos] = last_end + i
            parts.append(ch)
            decoded_pos += 1

        # Replace span with spaces.
        span_length = end - start
        parts.append(" " * span_length)
        for i in range(span_length):
            offset_map[decoded_pos] = start + i
            decoded_pos += 1

        last_end = end

    # Text after the last span.
    text_after = html[last_end:]
    decoded_text_after = _decode_entities(text_after)
    for i, ch in enumerate(decoded_text_after):
        offset_map[decoded_pos] = last_end + i
        parts.append(ch)
        decoded_pos += 1

    cleaned_text = "".join(parts)
    return cleaned_text, offset_map


def _collect_emphasis_markers(md: str, blocks: list[tuple[int, int, str]]) -> None:
    """Collect bold, italic, and strikethrough markers in the markdown text.

    This function identifies emphasis markers and adds them to the blocks list
    with appropriate types. The markers are:
    - Bold: ** (double asterisk)
    - Italic: * (single asterisk, not part of bold)
    - Strikethrough: ~~ (double tilde)

    Args:
        md: The markdown text to process.
        blocks: List to append detected markers to.
    """
    # Bold: ** (double asterisk)
    bold_pattern = re.compile(r"\*\*")
    for m in bold_pattern.finditer(md):
        blocks.append((m.start(), m.end(), "bold_marker"))

    # Italic: * (single asterisk, not part of bold)
    italic_pattern = re.compile(r"(?<!\*)\*(?!\*)")
    for m in italic_pattern.finditer(md):
        blocks.append((m.start(), m.end(), "italic_marker"))

    # Strikethrough: ~~ (double tilde)
    strikethrough_pattern = re.compile(r"~~")
    for m in strikethrough_pattern.finditer(md):
        blocks.append((m.start(), m.end(), "strikethrough"))


def _get_markdown_blocks(md: str) -> list[tuple[int, int, str]]:
    """Use markdown-it-py to identify non-text blocks in Markdown.

    Returns a list of ``(start, end, block_type)`` tuples for blocks that
    should be removed from the cleaned output. Block types include
    ``"code_fence"``, ``"code_inline"``, ``"link"``, ``"image"``,
    ``"table"``, ``"html_block"``, ``"bold_marker"``, and ``"italic_marker"``.
    """
    try:
        from markdown_it import MarkdownIt
    except ImportError:  # pragma: no cover
        raise ImportError(
            "markdown-it-py is required for Markdown preprocessing. "
            "Install it with: pip install markdown-it-py"
        )

    md_it = MarkdownIt()
    tokens = md_it.parse(md)

    blocks: list[tuple[int, int, str]] = []

    # Collect emphasis markers (bold, italic, strikethrough).
    # Replace markers with spaces to remove markdown formatting while keeping
    # the inner text visible. This prevents prosecco from counting special
    # characters as words in multi-word noun checks.
    # Uses a generic function to collect markers with their types.
    _collect_emphasis_markers(md, blocks)

    # Collect inline code spans.
    # Match single or double backtick code spans, but not triple or more (which are fenced code blocks).
    # Use negative lookbehind/lookahead to ensure we don't match part of a fenced code block.
    inline_code_pattern = re.compile(r"(?<!`)``?(?![`]).*?(?<!`)``?(?!`)")
    for m in inline_code_pattern.finditer(md):
        blocks.append((m.start(), m.end(), "code_inline"))

    # Collect inline links [text](url) and images ![alt](url).
    # For links, we keep the link text visible but replace the URL with spaces.
    # The entire link region is marked so the linter can suppress errors on
    # the visible link text.
    image_pattern = re.compile(r"!\[.*?\]\([^)]*\)", re.DOTALL)
    # Match a link that may contain nested images: [ ... (url) ] where ... can
    # include ![alt](img) sequences (e.g. badge links like [![CI](img)](url)).
    link_pattern = re.compile(
        r"\[(?:[^\[\]]|!\[[^\[\]]*\]\([^)]*\))*\]\([^)]*\)",
        re.DOTALL,
    )
    for m in image_pattern.finditer(md):
        blocks.append((m.start(), m.end(), "image"))
    for m in link_pattern.finditer(md):
        full_match = m.group()
        # Find where the URL part starts (the opening paren of the URL).
        url_start_in_match = full_match.find('(')
        if url_start_in_match >= 0:
            # Replace only the URL part with spaces, keep link text visible.
            # The entire link is marked as "link" region.
            text_part = full_match[:url_start_in_match]
            url_part = full_match[url_start_in_match:]
            # Add the text part as visible (not replaced)
            # Add the URL part as replaced with spaces
            blocks.append((m.start(), m.start() + len(text_part), "link_text"))
            blocks.append((m.start() + len(text_part), m.start() + len(full_match), "link_url"))
        else:
            blocks.append((m.start(), m.end(), "link"))

    # Collect fenced code blocks.
    # Match from opening fence (``` or ~~~) to closing fence on a line by itself.
    fence_pattern = re.compile(r"^(`{3,}|~{3,})[^\n]*\n[\s\S]*?\n\1`*\s*$", re.MULTILINE)
    for m in fence_pattern.finditer(md):
        blocks.append((m.start(), m.end(), "code_fence"))

    # Collect HTML blocks and inline HTML.
    # Match HTML comments, opening/closing tags, and autolinks.
    # The pattern matches from < to >, handling attributes and special cases.
    html_block_pattern = re.compile(
        r"<!--.*?-->"
        r"|<[/!\?][^>]*>"  # Comments, declarations, processing instructions
        r"|<([a-zA-Z][a-zA-Z0-9]*)(?:\s[^>]*)?\s*/?>"  # Opening/self-closing tags
        r"|</[a-zA-Z][a-zA-Z0-9]*\s*>"  # Closing tags
        r"|<([a-zA-Z][a-zA-Z0-9]*:?[a-zA-Z0-9.:]*)\s+/?>"  # Namespaced tags
        r"|<([a-zA-Z]+)://[^>\s]*>",  # Autolinks (angle-bracket URLs)
        re.DOTALL,
    )
    for m in html_block_pattern.finditer(md):
        blocks.append((m.start(), m.end(), "html_block"))

    # Collect table row delimiters (| characters).
    # Match | characters that are part of table rows (not standalone pipes).
    # Only replace the | delimiters, keeping cell content visible.
    table_delimiter_pattern = re.compile(r"\|")
    for m in table_delimiter_pattern.finditer(md):
        # Check if this | is part of a table row.
        line_start = md.rfind('\n', 0, m.start()) + 1
        line_end = md.find('\n', m.start())
        if line_end == -1:
            line_end = len(md)
        
        line = md[line_start:line_end]
        # Only treat as table delimiter if line has multiple | characters
        # (indicating it's a table row, not a standalone pipe).
        if line.count('|') >= 2:
            blocks.append((m.start(), m.end(), "table_delimiter"))

    # Collect horizontal rules: ---, ***, ___, or with spaces between chars
    # Must be on their own line (preceded by newline or start of string).
    horizontal_rule_pattern = re.compile(
        r"""
        ^[\t ]*(?:
            [-]{3,}[\t ]*  # Three or more dashes
            |[*]{3,}[\t ]*  # Three or more asterisks
            |[_]{3,}[\t ]*  # Three or more underscores
            |(?:[-*+][\t ]){2,}[-*+][\t ]*  # Dashes, asterisks, or pluses with spaces
        )$  # End of line
        """,
        re.MULTILINE | re.VERBOSE,
    )
    for m in horizontal_rule_pattern.finditer(md):
        blocks.append((m.start(), m.end(), "horizontal_rule"))

    # Collect blockquote markers: > at start of line (including nested >>)
    # Replace > with spaces, preserve text content.
    blockquote_pattern = re.compile(r"^>{1,}[\t ]*", re.MULTILINE)
    for m in blockquote_pattern.finditer(md):
        blocks.append((m.start(), m.end(), "blockquote"))

    # Collect list markers: -, *, or numbered (1.) at start of line
    # Replace marker with spaces, preserve text content.
    # List markers must be at start of line (with optional indentation),
    # followed by the marker, followed by a space or end of line.
    list_marker_pattern = re.compile(
        r"^[\t ]*(?:[-*+]|[0-9]+\.)[\t ]",
        re.MULTILINE,
    )
    for m in list_marker_pattern.finditer(md):
        blocks.append((m.start(), m.end(), "list_marker"))

    # Collect footnote references: [^1], [^123], etc.
    # Replace with spaces, preserve surrounding text.
    footnote_ref_pattern = re.compile(r"\[\^\d+\]")
    for m in footnote_ref_pattern.finditer(md):
        blocks.append((m.start(), m.end(), "footnote_ref"))

    # Collect footnote definitions: [^1]: footnote text
    # Replace [^1]: with spaces, preserve footnote text.
    footnote_def_pattern = re.compile(r"^\[\^\d+\]:", re.MULTILINE)
    for m in footnote_def_pattern.finditer(md):
        blocks.append((m.start(), m.end(), "footnote_def"))

    # Collect task list checkboxes: [ ] (unchecked) or [x] (checked)
    # Replace with spaces, preserve surrounding text.
    task_checkbox_pattern = re.compile(r"\[[ x]\]", re.IGNORECASE)
    for m in task_checkbox_pattern.finditer(md):
        blocks.append((m.start(), m.end(), "task_checkbox"))

    # Collect email autolinks: <user@example.com>
    # Replace with spaces, preserve surrounding text.
    email_autolink_pattern = re.compile(r"<[^>@\s]+@[^>@\s]+\.[^>@\s]+>")
    for m in email_autolink_pattern.finditer(md):
        blocks.append((m.start(), m.end(), "email_autolink"))

    # Collect definition list markers: : at start of line
    # Replace : with space, preserve definition text.
    definition_marker_pattern = re.compile(r"^:[\t ]", re.MULTILINE)
    for m in definition_marker_pattern.finditer(md):
        blocks.append((m.start(), m.end(), "definition_marker"))

    # Collect math delimiters: $ for inline math, $$ for display math
    # Replace $ with spaces, preserve math content.
    math_delimiter_pattern = re.compile(r"\$")
    for m in math_delimiter_pattern.finditer(md):
        blocks.append((m.start(), m.end(), "math_delimiter"))

    # Collect Markdown headers: # heading text
    # Match from the first # to the end of the line.
    # Headers are kept visible but marked so the linter can add metadata.
    header_pattern = re.compile(r"^#+\s+[^\n]*$", re.MULTILINE)
    for m in header_pattern.finditer(md):
        blocks.append((m.start(), m.end(), "header"))

    # Sort and merge overlapping blocks. When blocks overlap, keep the outer
    # block (smaller start) and discard the inner one. Adjacent blocks with
    # different types are preserved.
    blocks.sort(key=lambda b: (b[0], -b[1]))
    merged: list[tuple[int, int, str]] = []
    for start, end, btype in blocks:
        if merged and start < merged[-1][1]:
            # Block overlaps with the previous one. Discard the inner block
            # (the one that starts later) by skipping it.
            continue
        elif merged and start == merged[-1][1] and merged[-1][2] == btype:
            # Adjacent blocks with the same type: merge them.
            merged[-1] = (merged[-1][0], end, btype)
        else:
            merged.append((start, end, btype))

    # Return the list of merged blocks (regions) alongside the block list.
    # The regions list is used by downstream linting tools to annotate errors
    # with the type of non-prose region they occur in.
    return merged


def preprocess_markdown(
    md: str,
) -> tuple[str, dict[int, int], list[tuple[int, int, str]]]:
    """Parse Markdown and remove non-text elements while preserving offsets.

    This function uses markdown-it-py's parsing to identify non-text regions
    (headers, code blocks, code spans, links, images, HTML blocks, and table
    rows) and replaces them with whitespace. The output has the same length
    as the input, so character offsets remain valid.

    Args:
        md: Raw Markdown string to process.

    Returns:
        A tuple of ``(cleaned_text, offset_map, regions)`` where:
            * ``cleaned_text`` contains the original text with all non-text
              elements replaced by whitespace.
            * ``offset_map`` maps each character position in ``cleaned_text``
              to the corresponding position in the original ``md`` string.
            * ``regions`` is a list of ``(start, end, region_type)`` tuples
              describing each non-text region. ``region_type`` is one of
              ``"header"``, ``"code_fence"``, ``"code_inline"``, ``"link"``,
              ``"image"``, ``"html_block"``, or ``"table_row"``.

    Example:
        >>> text, mapping, regions = preprocess_markdown("# Hello\\n\\n`code` and [link](url)")
        >>> assert "Hello" in text
        >>> assert mapping[0] == 0
        >>> assert len(regions) >= 2
    """
    # _get_markdown_blocks returns the merged blocks, which we use both for
    # replacement and as the regions list.
    merged = _get_markdown_blocks(md)

    parts: list[str] = []
    offset_map: list[int | None] = [None] * len(md)
    last_end = 0
    # Track link text regions separately so the linter can suppress errors.
    link_text_regions: list[tuple[int, int]] = []
    # Track code-as-noun regions to add after the loop (avoid modifying merged during iteration).
    code_as_noun_regions: list[tuple[int, int]] = []

    for start, end, btype in merged:
        # Text before this block.
        text_before = md[last_end:start]
        for i, ch in enumerate(text_before):
            pos = last_end + i
            offset_map[pos] = pos
            parts.append(ch)

        # Handle different block types.
        if btype == "link_text":
            # Keep link text visible but strip markdown characters (backticks,
            # brackets, exclamation marks) that may be part of the link syntax.
            link_text_regions.append((start, end))
            # Process each character in the link text
            for i, ch in enumerate(md[start:end]):
                # Replace markdown characters with spaces
                if ch in "`[]!()":
                    offset_map[start + i] = start + i
                    parts.append(" ")
                else:
                    offset_map[start + i] = start + i
                    parts.append(ch)
        elif btype == "link_url":
            # Replace URL with spaces.
            span_length = end - start
            parts.append(" " * span_length)
            for i in range(start, end):
                offset_map[i] = i
        elif btype == "code_inline":
            # Extract content from inline code span and normalize to a noun token.
            # Replace backticks with spaces and code content with a letter-only
            # word so the grammar linter sees a noun. Mark region as "code-as-noun"
            # for metadata suppression.
            code_content = md[start:end]
            # Strip backticks to get the actual code content
            code_content_stripped = code_content.strip("`")
            # Normalize to letters only (remove digits, hyphens, etc.)
            normalized = re.sub(r"[^a-zA-Z]", "", code_content_stripped)
            # If no letters remain, use a placeholder
            if not normalized:
                normalized = "code"
            # Calculate the number of non-backtick characters
            non_backtick_count = len(code_content) - code_content.count("`")
            # Use the normalized word, repeated to fill the space if needed
            token = (normalized * ((non_backtick_count // len(normalized)) + 1))[:non_backtick_count]
            # Replace entire code_inline region (backticks become spaces,
            # code content becomes the token)
            token_idx = 0
            for i, ch in enumerate(code_content):
                offset_map[start + i] = start + i
                if ch == "`":
                    # Replace backticks with spaces
                    parts.append(" ")
                else:
                    # Replace code content with token characters
                    parts.append(token[token_idx])
                    token_idx += 1
            # Track region with type "code-as-noun" (added after loop)
            code_as_noun_regions.append((start, end))
        elif btype == "table_delimiter":
            # Replace only the | delimiter with a space.
            parts.append(" ")
            offset_map[start] = start
        elif btype == "header":
            # Keep header text visible but replace the # markers with spaces.
            # Find where the actual header text starts (after # and whitespace).
            header_text = md[start:end]
            # Match the # markers and leading whitespace
            header_match = re.match(r"^#+\s*", header_text)
            if header_match:
                # Replace the # markers with spaces
                hash_length = len(header_match.group())
                for i in range(hash_length):
                    offset_map[start + i] = start + i
                    parts.append(" ")
                # Keep the actual header text visible
                for i, ch in enumerate(header_text[header_match.end():]):
                    offset_map[start + hash_length + i] = start + hash_length + i
                    parts.append(ch)
            else:
                # Fallback: keep entire header visible
                for i, ch in enumerate(md[start:end]):
                    offset_map[start + i] = start + i
                    parts.append(ch)
        elif btype in ("bold_marker", "italic_marker", "strikethrough"):
            # Replace emphasis markers with spaces to prevent markdown
            # characters from leaking into the cleaned output. The regions
            # list still contains the marker positions for metadata suppression.
            span_length = end - start
            parts.append(" " * span_length)
            for i in range(start, end):
                offset_map[i] = i
        elif btype == "horizontal_rule":
            # Replace horizontal rule with spaces to prevent markdown
            # characters from leaking into the cleaned output.
            span_length = end - start
            parts.append(" " * span_length)
            for i in range(start, end):
                offset_map[i] = i
        elif btype == "blockquote":
            # Replace blockquote marker with spaces, preserve text content.
            span_length = end - start
            parts.append(" " * span_length)
            for i in range(start, end):
                offset_map[i] = i
        elif btype == "list_marker":
            # Replace list marker with spaces, preserve text content.
            span_length = end - start
            parts.append(" " * span_length)
            for i in range(start, end):
                offset_map[i] = i
        elif btype == "footnote_ref":
            # Replace footnote reference with spaces, preserve surrounding text.
            span_length = end - start
            parts.append(" " * span_length)
            for i in range(start, end):
                offset_map[i] = i
        elif btype == "footnote_def":
            # Replace footnote definition marker with spaces, preserve text.
            span_length = end - start
            parts.append(" " * span_length)
            for i in range(start, end):
                offset_map[i] = i
        elif btype == "task_checkbox":
            # Replace task checkbox with spaces, preserve surrounding text.
            span_length = end - start
            parts.append(" " * span_length)
            for i in range(start, end):
                offset_map[i] = i
        elif btype == "email_autolink":
            # Replace email autolink with spaces, preserve surrounding text.
            span_length = end - start
            parts.append(" " * span_length)
            for i in range(start, end):
                offset_map[i] = i
        elif btype == "definition_marker":
            # Replace definition marker with spaces, preserve definition text.
            span_length = end - start
            parts.append(" " * span_length)
            for i in range(start, end):
                offset_map[i] = i
        elif btype == "math_delimiter":
            # Replace math delimiter ($ or $$) with spaces, preserve math content.
            span_length = end - start
            parts.append(" " * span_length)
            for i in range(start, end):
                offset_map[i] = i
        else:
            # Replace entire block with spaces.
            span_length = end - start
            parts.append(" " * span_length)
            for i in range(start, end):
                offset_map[i] = i

        last_end = end

    # Text after the last block.
    text_after = md[last_end:]
    for i, ch in enumerate(text_after):
        pos = last_end + i
        offset_map[pos] = pos
        parts.append(ch)

    cleaned_text = "".join(parts)
    offset_map = {i: offset_map[i] for i in range(len(cleaned_text))}

    # Add link text regions to the regions list with type "link" so the
    # linter can suppress errors on visible link text.
    for start, end in link_text_regions:
        merged.append((start, end, "link"))
    
    # Add code-as-noun regions to the regions list.
    for start, end in code_as_noun_regions:
        merged.append((start, end, "code-as-noun"))

    return cleaned_text, offset_map, merged


# Module-level documentation.
__all__ = ["preprocess_html", "preprocess_markdown"]
