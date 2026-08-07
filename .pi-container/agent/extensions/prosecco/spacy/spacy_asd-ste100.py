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

    # Helper to check if a cleaned-text position is in or near a link region.
    # Link regions contain visible link text that should be suppressed.
    # We suppress errors that overlap with or are within 10 characters of a
    # link region, since errors often start just before the link text.
    LINK_PADDING = 10

    def _is_in_link(cleaned_offset: int) -> bool:
        for start, end, rtype in regions:
            if rtype == "link":
                # Check if the offset is within the link region or within padding.
                if start - LINK_PADDING <= cleaned_offset < end + LINK_PADDING:
                    return True
        return False

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

    # Output in Vale-compatible format: file:line:col CheckName:message
    # Map cleaned-text offsets back to original-text positions via the
    # offset_map, then convert to line:col in the original file.
    # Annotate each error with the region type (header, table_row, etc.)
    # when the error occurs in a non-prose region.
    original_lines = text.split('\n')

    for issue in all_issues:
        cleaned_offset = issue["offset"]
        
        # Skip errors in link regions (visible link text).
        if _is_in_link(cleaned_offset):
            continue
        
        original_offset = offset_map.get(cleaned_offset, cleaned_offset)
        region_type = _get_region_type(cleaned_offset)

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

        # Add region context to the message if the error is in a non-prose region.
        if region_type:
            print(f"{filepath}:{line}:{col} STE100.{issue['type']}: [{region_type}] {issue['message']}")
        else:
            print(f"{filepath}:{line}:{col} STE100.{issue['type']}: {issue['message']}")

    if not all_issues:
        print("No ASD-STE100 issues found.")


if __name__ == "__main__":
    main()
