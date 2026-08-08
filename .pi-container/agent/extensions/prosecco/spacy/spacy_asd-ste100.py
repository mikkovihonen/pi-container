#!/usr/bin/env python3
"""
ASD-STE100 checker using spaCy directly (no HTTP server needed).
Reads from file or stdin, outputs results in Vale-compatible format.

This is the main entry point that imports all check functions from section-specific modules.
"""
import sys
import spacy

# Load spaCy model
nlp = spacy.load("en_core_web_sm")

# Import check functions from Section 1 (Words)
from checks_section1 import (
    check_approved_words,
    check_part_of_speech,
    check_approved_meaning,
    check_approved_forms,
    check_technical_noun_category,
    check_non_approved_as_technical,
    check_technical_noun_as_verb,
    check_technical_noun_approval,
    check_too_long_technical_nouns,
    check_regional_slang_jargon,
    check_consistent_technical_nouns,
    check_technical_verb_category,
    check_technical_verb_as_noun,
    check_british_english,
)

# Import check functions from Section 2 (Multi-word nouns)
from checks_section2 import (
    check_multi_word_nouns,
    check_technical_noun_clarity,
)

# Import check functions from Section 3 (Verbs)
from checks_section3 import (
    check_verb_forms,
    check_verb_tenses,
    check_past_participle_as_adjective,
    check_passive_voice,
    check_passive_voice_with_agent,
    check_ing_forms,
    check_noun_as_verb,
)

# Import check functions from Section 4 (Sentences)
from checks_section4 import (
    check_short_sentences,
    check_contractions,
    check_forbidden_modals,
    check_vertical_lists,
    check_connecting_words,
    check_missing_articles,
    check_article_usage,
)

# Import check functions from Section 5 (Procedural writing)
from checks_section5 import (
    check_sentence_length_procedural,
    check_multiple_instructions,
    check_non_imperative_in_procedures,
    check_descriptive_statement_first,
    check_notes,
)

# Import check functions from Section 6 (Descriptive writing)
from checks_section6 import (
    check_information_structure,
    check_key_words,
    check_sentence_length_descriptive,
    check_paragraph_structure,
    check_paragraph_topic,
    check_paragraph_length,
)

# Import check functions from Section 7 (Safety instructions)
from checks_section7 import (
    check_safety_instruction_format,
    check_safety_instruction_explanation,
)

# Import check functions from Section 8 (Punctuation and word count)
from checks_section8 import (
    check_semicolons,
    check_hyphens,
    check_parentheses_usage,
    check_word_count_with_parentheses,
    check_word_count_with_numbers,
    check_hyphenation_patterns,
    check_vertical_list_colons,
    check_word_count_all,
)

# Import check functions from Section 9 (Writing practices)
from checks_section9 import (
    check_word_usage,
    check_consistent_style,
    check_phrasal_verbs,
    check_consistent_terminology,
    check_different_sentence_constructions,
    check_word_for_word_replacement,
    check_non_approved_words,
)

# Import check functions from General Recommendations
from checks_gr_recommendations import (
    check_conjunction_that,
    check_ambiguous_with,
    check_ambiguous_pronouns,
    check_ambiguous_this,
    check_false_friends,
    check_latin_abbreviations,
    check_gender_pronouns,
    check_possessive_form,
)

def main():
    """Main entry point."""
    # Parse command-line arguments.
    # --include-all: Show all warnings, including those with metadata annotations (e.g. [bold_marker], [header]).
    include_all = "--include-all" in sys.argv
    # Filter argv to remove our custom flag before passing to the rest of the script.
    sys.argv = [arg for arg in sys.argv if arg != "--include-all"]

    # Read input from file or stdin
    filepath = None
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                text = f.read()
        except FileNotFoundError:
            print(f"Error: File '{filepath}' not found.", file=sys.stderr)
            sys.exit(1)
    else:
        text = sys.stdin.read()
        filepath = "stdin"

    if not text.strip():
        print("No problems found.")
        return

    # Preprocess the markdown to strip non-prose elements (code blocks,
    # links, images, HTML blocks, table rows, headers). The preprocessor
    # returns cleaned text (same length as input), an offset map that maps
    # cleaned-text positions back to original-text positions, and a list of
    # non-text regions with their types.
    from preprocess_text import preprocess_markdown
    cleaned_text, offset_map, regions = preprocess_markdown(text)

    doc = nlp(cleaned_text)

    # Helper to look up the region type for a cleaned-text position.
    # Returns the region type string (e.g. "header", "table_row") or None.
    def _get_region_type(cleaned_offset: int) -> str | None:
        for start, end, rtype in regions:
            if start <= cleaned_offset < end:
                return rtype
        return None

    # Helper to check if an error span overlaps with a link region.
    # Link regions contain visible link text that should be suppressed.
    # We suppress errors whose span overlaps with a link region.
    def _is_in_link(cleaned_offset: int, length: int) -> bool:
        error_end = cleaned_offset + length
        for start, end, rtype in regions:
            if rtype == "link":
                # Check if the error span overlaps with the link region.
                if not (error_end < start or cleaned_offset > end):
                    return True
        return False

    # Build a tree structure from regions for suppression traversal.
    # Each node has: start, end, type, children, suppress (list of issue types)
    def _build_metadata_tree():
        nodes = []
        for start, end, rtype in regions:
            node = {
                "start": start,
                "end": end,
                "type": rtype,
                "children": [],
                "suppress": _get_suppression_types(rtype),
            }
            nodes.append(node)

        # Link parent-child relationships (parent contains child)
        for node in nodes:
            for other in nodes:
                if node is other:
                    continue
                if (node["start"] <= other["start"] and
                    node["end"] >= other["end"] and
                    (node["start"] != other["start"] or node["end"] != other["end"])):
                    node["children"].append(other)

        # Return root nodes (not contained in any other)
        roots = []
        for node in nodes:
            is_child = False
            for other in nodes:
                if other is node:
                    continue
                if (other["start"] <= node["start"] and
                    other["end"] >= node["end"] and
                    (other["start"] != node["start"] or other["end"] != node["end"])):
                    is_child = True
                    break
            if not is_child:
                roots.append(node)

        return roots

    def _is_suppressed_in_tree(tree_root: dict, issue_type: str) -> bool:
        """Check if an issue type is suppressed by this node or any ancestor."""
        if issue_type in tree_root.get("suppress", []):
            return True
        for child in tree_root.get("children", []):
            if _is_suppressed_in_tree(child, issue_type):
                return True
        return False

    def _find_node_at_position(tree_roots: list, cleaned_offset: int):
        """Find the innermost node containing the given position."""
        best_match = None
        best_size = float('inf')
        for root in tree_roots:
            node = _find_node_in_tree(root, cleaned_offset)
            if node:
                size = node["end"] - node["start"]
                if size < best_size:
                    best_match = node
                    best_size = size
        return best_match

    def _find_node_in_tree(node: dict, cleaned_offset: int):
        """Recursively find the innermost node containing the position."""
        if node["start"] <= cleaned_offset < node["end"]:
            # Check children first
            for child in node["children"]:
                child_match = _find_node_in_tree(child, cleaned_offset)
                if child_match:
                    return child_match
            return node
        return None

    def _is_suppressed_by_metadata(cleaned_offset: int, issue_type: str, cleaned_text: str) -> bool:
        """Check if an issue is suppressed by metadata (bold labels, headers, etc.)."""
        # Check if position is on a bold label (between opening and closing **)
        if issue_type == "MissingArticles":
            # Find bold_marker regions and pair them
            bold_regions = sorted([(s, e) for s, e, r in regions if r == "bold_marker"])
            # Check each pair of adjacent bold markers
            for i in range(0, len(bold_regions) - 1, 2):
                if i + 1 < len(bold_regions):
                    s1, e1 = bold_regions[i]
                    s2, e2 = bold_regions[i + 1]
                    # Check if they're on the same line (no newline between them)
                    if '\n' not in cleaned_text[e1:s2]:
                        # Check if position is between the closing of first and opening of second
                        if e1 <= cleaned_offset < s2:
                            return True

        # Check tree-based suppression
        tree_roots = _build_metadata_tree()
        node = _find_node_at_position(tree_roots, cleaned_offset)
        if node and issue_type in node.get("suppress", []):
            return True
        return False

    def _get_suppression_types(region_type: str) -> list[str]:
        """Get the list of issue types that should be suppressed for a region type."""
        suppression_map = {
            "bold_marker": ["MissingArticles"],
            "header": ["MissingArticles", "ConnectingWords"],
            "table_delimiter": [],
            "code_inline": [],
            "link_text": [],
            "link_url": [],
            "image": [],
            "html_block": [],
            "code_fence": [],
            "blockquote": [],
            "list_marker": [],
            "footnote_ref": [],
            "footnote_def": [],
            "task_checkbox": [],
            "email_autolink": [],
            "math_delimiter": [],
            "definition_marker": [],
            "horizontal_rule": [],
            "strikethrough": [],
            "italic_marker": [],
        }
        return suppression_map.get(region_type, [])

    # Run all ASD-STE100 checks (Section 1: Words)
    all_issues = []
    all_issues.extend(check_approved_words(doc))
    all_issues.extend(check_part_of_speech(doc))
    all_issues.extend(check_approved_meaning(doc))
    all_issues.extend(check_approved_forms(doc))
    all_issues.extend(check_technical_noun_category(doc))
    all_issues.extend(check_non_approved_as_technical(doc))
    all_issues.extend(check_technical_noun_as_verb(doc))
    all_issues.extend(check_technical_noun_approval(doc))
    all_issues.extend(check_too_long_technical_nouns(doc))
    all_issues.extend(check_regional_slang_jargon(doc))
    all_issues.extend(check_consistent_technical_nouns(doc))
    all_issues.extend(check_technical_verb_category(doc))
    all_issues.extend(check_technical_verb_as_noun(doc))
    all_issues.extend(check_british_english(doc))

    # Section 2: Multi-word nouns
    all_issues.extend(check_multi_word_nouns(doc))
    all_issues.extend(check_technical_noun_clarity(doc))

    # Section 3: Verbs
    all_issues.extend(check_verb_forms(doc))
    all_issues.extend(check_verb_tenses(doc))
    all_issues.extend(check_past_participle_as_adjective(doc))
    all_issues.extend(check_passive_voice(doc))
    all_issues.extend(check_passive_voice_with_agent(doc))
    all_issues.extend(check_ing_forms(doc))
    all_issues.extend(check_noun_as_verb(doc))

    # Section 4: Sentences
    all_issues.extend(check_short_sentences(doc))
    all_issues.extend(check_contractions(doc))
    all_issues.extend(check_forbidden_modals(doc))
    all_issues.extend(check_vertical_lists(doc))
    all_issues.extend(check_connecting_words(doc))
    all_issues.extend(check_missing_articles(doc))
    all_issues.extend(check_article_usage(doc))

    # Section 5: Procedural writing
    all_issues.extend(check_sentence_length_procedural(doc))
    all_issues.extend(check_multiple_instructions(doc))
    all_issues.extend(check_non_imperative_in_procedures(doc))
    all_issues.extend(check_descriptive_statement_first(doc))
    all_issues.extend(check_notes(doc))

    # Section 6: Descriptive writing
    all_issues.extend(check_information_structure(doc))
    all_issues.extend(check_key_words(doc))
    all_issues.extend(check_sentence_length_descriptive(doc))
    all_issues.extend(check_paragraph_structure(doc))
    all_issues.extend(check_paragraph_topic(doc))
    all_issues.extend(check_paragraph_length(doc))

    # Section 7: Safety instructions
    all_issues.extend(check_safety_instruction_format(doc))
    all_issues.extend(check_safety_instruction_explanation(doc))

    # Section 8: Punctuation and word count
    all_issues.extend(check_semicolons(doc))
    all_issues.extend(check_hyphens(doc))
    all_issues.extend(check_parentheses_usage(doc))
    all_issues.extend(check_word_count_with_parentheses(doc))
    all_issues.extend(check_word_count_with_numbers(doc))
    all_issues.extend(check_hyphenation_patterns(doc))
    all_issues.extend(check_vertical_list_colons(doc))
    all_issues.extend(check_word_count_all(doc))

    # Section 9: Writing practices
    all_issues.extend(check_word_usage(doc))
    all_issues.extend(check_consistent_style(doc))
    all_issues.extend(check_phrasal_verbs(doc))
    all_issues.extend(check_consistent_terminology(doc))
    all_issues.extend(check_different_sentence_constructions(doc))
    all_issues.extend(check_word_for_word_replacement(doc))
    all_issues.extend(check_non_approved_words(doc))

    # General Recommendations (GR-1 to GR-8)
    all_issues.extend(check_conjunction_that(doc))
    all_issues.extend(check_ambiguous_with(doc))
    all_issues.extend(check_ambiguous_pronouns(doc))
    all_issues.extend(check_ambiguous_this(doc))
    all_issues.extend(check_false_friends(doc))
    all_issues.extend(check_latin_abbreviations(doc))
    all_issues.extend(check_gender_pronouns(doc))
    all_issues.extend(check_possessive_form(doc))

    # Sort by offset
    all_issues.sort(key=lambda x: x["offset"])

    # Deduplicate exact duplicate warnings (same type, message, and offset).
    # Multiple checks can emit identical warnings for the same issue.
    seen_issues = set()
    unique_issues = []
    for issue in all_issues:
        # Create a dedup key from issue type, message, and offset.
        dedup_key = (issue["type"], issue["message"], issue["offset"])
        if dedup_key not in seen_issues:
            seen_issues.add(dedup_key)
            unique_issues.append(issue)
    all_issues = unique_issues

    # Output in Vale-compatible format: file:line:col CheckName:message
    # Map cleaned-text offsets back to original-text positions via the
    # offset_map, then convert to line:col in the original file.
    # Annotate each error with the region type (header, table_row, etc.)
    # when the error occurs in a non-prose region.
    original_lines = text.split('\n')

    for issue in all_issues:
        cleaned_offset = issue["offset"]
        error_length = issue.get("length", 1)

        # Skip errors in link regions (visible link text).
        if _is_in_link(cleaned_offset, error_length):
            continue

        original_offset = offset_map.get(cleaned_offset, cleaned_offset)
        region_type = _get_region_type(cleaned_offset)

        # Skip errors suppressed by metadata (bold labels, headers, etc.)
        if _is_suppressed_by_metadata(cleaned_offset, issue["type"], cleaned_text):
            continue

        # Calculate line and column from original character offset.
        line = 1
        col = 1
        current_offset = 0
        for i, line_text in enumerate(original_lines):
            line_end = current_offset + len(line_text) + 1  # +1 for newline
            if current_offset <= original_offset < line_end:
                line = i + 1
                col = original_offset - current_offset + 1
                break
            current_offset = line_end

        # Skip errors with metadata annotations unless --include-all is set.
        if not include_all and region_type:
            continue

        # Collapse consecutive whitespace in the message to a single space.
        message = ' '.join(issue['message'].split())

        # Add region context to the message if the error is in a non-prose region.
        if region_type:
            print(f"{filepath}:{line}:{col} STE100.{issue['type']}: [{region_type}] {message}")
        else:
            print(f"{filepath}:{line}:{col} STE100.{issue['type']}: {message}")

    if not all_issues:
        print("No ASD-STE100 issues found.")


if __name__ == "__main__":
    main()
