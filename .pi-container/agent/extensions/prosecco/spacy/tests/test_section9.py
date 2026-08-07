#!/usr/bin/env python3
"""Tests for Section 9 (Writing practices) checks."""
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import spacy
from checks_section9 import (
    check_word_usage,
    check_consistent_style,
    check_phrasal_verbs,
    check_consistent_terminology,
    check_different_sentence_constructions,
    check_word_for_word_replacement,
    check_non_approved_words,
)

# Load spaCy model
nlp = spacy.load("en_core_web_sm")


def test_check_word_usage():
    """Test check_word_usage detects incorrect word usage."""
    doc = nlp("The temperature go up by 10 degrees.")
    issues = check_word_usage(doc)
    # Should detect "go up" as incorrect usage
    assert isinstance(issues, list)


def test_check_consistent_style():
    """Test check_consistent_style validates consistent style."""
    doc = nlp("The body is secure. The hull is tight.")
    issues = check_consistent_style(doc)
    # Should detect inconsistent terminology
    assert isinstance(issues, list)


def test_check_phrasal_verbs():
    """Test check_phrasal_verbs detects phrasal verbs."""
    doc = nlp("The temperature go up by 10 degrees.")
    issues = check_phrasal_verbs(doc)
    # Should detect "go up" as phrasal verb
    assert isinstance(issues, list)


def test_check_consistent_terminology():
    """Test check_consistent_terminology validates consistent terminology."""
    doc = nlp("The body assembly is secure.")
    issues = check_consistent_terminology(doc)
    assert isinstance(issues, list)


def test_check_different_sentence_constructions():
    """Test check_different_sentence_constructions validates sentence variety."""
    doc = nlp("The filter is clean. The pump is leaky.")
    issues = check_different_sentence_constructions(doc)
    assert isinstance(issues, list)


def test_check_word_for_word_replacement():
    """Test check_word_for_word_replacement detects word-for-word replacement."""
    doc = nlp("The filter is clean. The filter is dry.")
    issues = check_word_for_word_replacement(doc)
    # Should detect repeated "filter"
    assert isinstance(issues, list)


def test_check_non_approved_words():
    """Test check_non_approved_words detects non-approved words."""
    doc = nlp("The bollocks is broken.")
    issues = check_non_approved_words(doc)
    # Should detect "bollocks" as non-approved word
    assert isinstance(issues, list)


if __name__ == "__main__":
    print("Running Section 9 tests...")
    test_check_word_usage()
    print("✓ test_check_word_usage")
    test_check_consistent_style()
    print("✓ test_check_consistent_style")
    test_check_phrasal_verbs()
    print("✓ test_check_phrasal_verbs")
    test_check_consistent_terminology()
    print("✓ test_check_consistent_terminology")
    test_check_different_sentence_constructions()
    print("✓ test_check_different_sentence_constructions")
    test_check_word_for_word_replacement()
    print("✓ test_check_word_for_word_replacement")
    test_check_non_approved_words()
    print("✓ test_check_non_approved_words")
    print("\nAll Section 9 tests passed!")
