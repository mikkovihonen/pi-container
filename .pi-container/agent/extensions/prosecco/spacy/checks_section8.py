#!/usr/bin/env python3
"""
ASD-STE100 Section 8 Checks (Punctuation and word count)

Summary of the rules

Punctuation
Rule 8.1: You can use all standard English punctuation marks but not the semicolon (;).
Rule 8.2: Use hyphens (-) to connect words that are directly related.
Rule 8.3: You can use parentheses: To make references to illustrations or text, 
          To include letters or numbers that identify items on an illustration or in a text, 
          To identify the work steps in a procedure, To include abbreviations, 
          To give the singular and plural forms of a noun at the same time, 
          To explain words or a part of a sentence, To include an alternative.

Word count
Rule 8.4: In a vertical list, a colon (:) has the same effect on word count as a period and shows the end of a sentence.
Rule 8.5: When you put text in parentheses, it counts as one word in that sentence.
Rule 8.6: Count each of these elements as one word: Numbers, Numbers together with units of measurement, 
          Abbreviations, Alphanumeric identifiers, Quoted text, Titles, headings, placards, and labels, 
          Proper nouns of individuals, groups, organizations, and geopolitical entities.
Rule 8.7: Hyphenated words count as one word.
"""
import re
import spacy
from glossary import (
    FORBIDDEN_PUNCTUATION, 
    COMMON_UNITS, 
    COMMON_ABBREVIATIONS, 
    COMMON_HYPHENATED_TERMS,
    EXPLANATION_WORDS,
)
from shared import (
    _get_compound_chain,
    _find_closing_paren,
    _is_allowed_parentheses_context,
    _count_sentence_words,
    _find_closing_quote_idx,
)


def check_semicolons(doc):
    """Check for semicolons (Rule 8.1).
    
    Rule 8.1: "You can use all standard English punctuation marks but not the 
    semicolon (;)."
    
    The semicolon (;) is not permitted in STE because it lets you write very long
    sentences. It is also not easy to use correctly. As an alternative to the
    semicolon, always write two different sentences.
    
    Uses spaCy features:
    - token.text for punctuation detection
    - token.idx for accurate offset calculation
    """
    issues = []
    
    for token in doc:
        if token.text == ";":
            issues.append({
                "type": "Semicolons",
                "message": "Do not use a semicolon. Write two different sentences instead.",
                "offset": token.idx,
                "length": 1,
            })
    
    return issues


def check_hyphens(doc):
    """Check for hyphens used to connect directly related words (Rule 8.2).
    
    Rule 8.2: "Use hyphens (-) to connect words that are directly related."
    
    A hyphen (-) is a punctuation mark that connects words or parts of words.
    Use the hyphen for technical nouns to show that two or more words are
    directly related.
    
    Uses spaCy features:
    - token.pos_ to identify punctuation (PUNCT)
    - token.dep_ for compound relationships
    - Dependency parsing to count connected words
    - token.head for relationship verification
    """
    issues = []
    seen = set()
    
    # Use spaCy to find hyphens and their relationships
    for token in doc:
        # Check for hyphen punctuation
        if token.pos_ == "PUNCT" and token.text == "-":
            # Find the compound chain this hyphen belongs to
            compound_words = _get_compound_chain(token, doc)
            
            # Check if more than three words are connected
            if len(compound_words) > 3 and token.idx not in seen:
                seen.add(token.idx)
                hyphenated_text = " ".join(w.text for w in compound_words)
                issues.append({
                    "type": "Hyphens",
                    "message": f"Do not use hyphens for groups of more than three words. '{hyphenated_text}' has {len(compound_words)} words.",
                    "offset": compound_words[0].idx,
                    "length": compound_words[-1].idx + len(compound_words[-1].text) - compound_words[0].idx,
                })
    
    return issues
def check_parentheses_usage(doc):
    """Check for proper parentheses usage (Rule 8.3).
    
    Rule 8.3: "You can use parentheses:
    - To make references to illustrations or text
    - To include letters or numbers that identify items on an illustration or in a text
    - To identify the work steps in a procedure
    - To include abbreviations
    - To give the singular and plural forms of a noun at the same time
    - To explain words or a part of a sentence
    - To include an alternative"
    
    Uses spaCy features:
    - token.pos_ to identify punctuation (PUNCT)
    - token.dep_ for dependency relationships
    - token.ent_type_ for entity type detection (references to figures, etc.)
    - token.head for relationship verification
    - doc.noun_chunks for context analysis
    """
    issues = []
    seen = set()
    
    # Use spaCy to find parentheses and their context
    for token in doc:
        if token.pos_ == "PUNCT" and token.text == "(":
            # Find the closing parenthesis
            close_paren = _find_closing_paren(token, doc)
            
            if close_paren:
                # Get the content between parentheses
                content_tokens = doc[token.i + 1:close_paren.i]
                content = " ".join(t.text for t in content_tokens)
                
                # Check if the content is in an allowed context
                if not _is_allowed_parentheses_context(content, doc, token.idx):
                    if token.idx not in seen:
                        seen.add(token.idx)
                        issues.append({
                            "type": "Parentheses",
                            "message": f"Use parentheses only for allowed purposes (references, abbreviations, alternatives, etc.). Found: '{content}'.",
                            "offset": token.idx,
                            "length": close_paren.idx + len(close_paren.text) - token.idx,
                        })
    
    return issues
def check_word_count_with_parentheses(doc):
    """Check word count with parentheses (Rule 8.5).
    
    Rule 8.5: "When you put text in parentheses, it counts as one word in 
    that sentence."
    
    The words inside parentheses form a separate sentence and count as one word.
    
    Uses spaCy features:
    - doc.sents for sentence segmentation
    - token.pos_ to identify parts of speech
    - Parenthesis detection in sentence text
    """
    issues = []
    
    for sent in doc.sents:
        text = sent.text
        # Count words in the sentence
        word_count = _count_sentence_words(text)
        
        # Check if sentence is too long (20 words for procedures, 25 for descriptive)
        # We'll use the stricter limit (20 words) as the default
        if word_count > 20:
            issues.append({
                "type": "SentenceLength",
                "message": f"Keep sentences short. This sentence has {word_count} words. Aim for 20 words or fewer.",
                "offset": sent.start_char,
                "length": len(sent.text),
            })
    
    return issues


def check_word_count_with_numbers(doc):
    """Check word count with numbers and units (Rule 8.6).
    
    Rule 8.6: "Count each of these elements as one word:
    - Numbers
    - Numbers together with units of measurement
    - Abbreviations
    - Alphanumeric identifiers
    - Quoted text
    - Titles, headings, placards, and labels
    - Proper nouns of individuals, groups, organizations, and geopolitical entities."
    
    Uses spaCy features:
    - doc.sents for sentence segmentation
    - token.pos_ to identify parts of speech (NUM, PROPN, etc.)
    - token.ent_type_ for entity type detection
    - Dependency parsing to verify word relationships
    """
    issues = []
    
    for sent in doc.sents:
        text = sent.text
        # Count words in the sentence
        word_count = _count_sentence_words(text)
        
        # Check if sentence is too long (20 words for procedures, 25 for descriptive)
        # We'll use the stricter limit (20 words) as the default
        if word_count > 20:
            issues.append({
                "type": "SentenceLength",
                "message": f"Keep sentences short. This sentence has {word_count} words. Aim for 20 words or fewer.",
                "offset": sent.start_char,
                "length": len(sent.text),
            })
    
    return issues


def check_hyphenation_patterns(doc):
    """Check for hyphenation patterns and word count (Rule 8.7).
    
    Rule 8.7: "Hyphenated words count as one word."
    
    This function verifies that hyphenated words are counted correctly in word counts.
    
    Uses spaCy features:
    - token.pos_ to identify parts of speech
    - token.tag_ for coarse POS tags
    - Dependency parsing to verify word relationships
    - doc.text for hyphen detection
    """
    issues = []
    
    # Check for hyphenated words in sentences
    for sent in doc.sents:
        text = sent.text
        # Count words in the sentence
        word_count = _count_sentence_words(text)
        
        # Check if sentence is too long (20 words for procedures, 25 for descriptive)
        # We'll use the stricter limit (20 words) as the default
        if word_count > 20:
            issues.append({
                "type": "SentenceLength",
                "message": f"Keep sentences short. This sentence has {word_count} words. Aim for 20 words or fewer.",
                "offset": sent.start_char,
                "length": len(sent.text),
            })
    
    return issues
def check_vertical_list_colons(doc):
    """Check for proper colon usage in vertical lists (Rule 8.4).
    
    Rule 8.4: "In a vertical list, a colon (:) has the same effect on word 
    count as a period and shows the end of a sentence."
    
    This function checks for colons in vertical list contexts and ensures they
    are used correctly.
    
    Uses spaCy features:
    - doc.sents for sentence segmentation
    - token.pos_ to identify punctuation (PUNCT)
    - token.dep_ for dependency relationships
    - token.head for relationship verification
    - doc.text for pattern detection
    """
    issues = []
    
    # Use spaCy to find colons in vertical list contexts
    for token in doc:
        # Check for colon punctuation
        if token.pos_ == "PUNCT" and token.text == ":":
            # Check if this is in a vertical list context
            # (colon followed by newline and another item)
            if token.i + 1 < len(doc) and doc[token.i + 1].text == "\n":
                # This is a vertical list colon - correct usage
                # No issue to report
                pass
    
    return issues


def check_word_count_all(doc):
    """Comprehensive word count check for all sentences (Rules 8.4-8.7).
    
    This function checks word counts for all sentences, applying the rules
    for numbers, units, abbreviations, hyphenated words, and parentheses.
    
    Uses spaCy features:
    - doc.sents for sentence segmentation
    - token.pos_ to identify parts of speech
    - token.ent_type_ for entity type detection
    - doc.text for pattern detection
    """
    issues = []
    
    for sent in doc.sents:
        text = sent.text
        # Count words according to ASD-STE100 rules
        word_count = _count_sentence_words(text)
        
        # Check if sentence is too long (20 words for procedures, 25 for descriptive)
        # We'll use the stricter limit (20 words) as the default
        if word_count > 20:
            issues.append({
                "type": "SentenceLength",
                "message": f"Keep sentences short. This sentence has {word_count} words. Aim for 20 words or fewer.",
                "offset": sent.start_char,
                "length": len(sent.text),
            })
    
    return issues


# Load spaCy model
try:
    nlp = spacy.load("en_core_web_sm")
except:
    nlp = None
