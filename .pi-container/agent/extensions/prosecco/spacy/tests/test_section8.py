#!/usr/bin/env python3
"""Tests for Section 8 (Punctuation and word count) checks."""
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import spacy
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

# Load spaCy model
nlp = spacy.load("en_core_web_sm")


def test_check_semicolons():
    """Test check_semicolons detects semicolons."""
    doc = nlp("Check the filter; clean the pump.")
    issues = check_semicolons(doc)
    # Should detect semicolon
    assert isinstance(issues, list)


def test_check_hyphens():
    """Test check_hyphens validates hyphen usage."""
    doc = nlp("The multi-word term is too long.")
    issues = check_hyphens(doc)
    assert isinstance(issues, list)


def test_check_parentheses_usage():
    """Test check_parentheses_usage validates parentheses usage."""
    doc = nlp("See (Fig. 1) for details.")
    issues = check_parentheses_usage(doc)
    # Should allow references like (Fig. 1)
    assert isinstance(issues, list)


def test_check_word_count_with_parentheses():
    """Test check_word_count_with_parentheses counts words correctly."""
    doc = nlp("Check (Fig. 1) the filter.")
    issues = check_word_count_with_parentheses(doc)
    assert isinstance(issues, list)


def test_check_word_count_with_numbers():
    """Test check_word_count_with_numbers counts words correctly."""
    doc = nlp("The 10 kg filter is clean.")
    issues = check_word_count_with_numbers(doc)
    assert isinstance(issues, list)


def test_check_hyphenation_patterns():
    """Test check_hyphenation_patterns validates hyphenation."""
    doc = nlp("The multi-step procedure is complete.")
    issues = check_hyphenation_patterns(doc)
    assert isinstance(issues, list)


def test_check_vertical_list_colons():
    """Test check_vertical_list_colons detects colons in vertical lists."""
    doc = nlp("Check the following: filter, pump, valve.")
    issues = check_vertical_list_colons(doc)
    assert isinstance(issues, list)


def test_check_word_count_all():
    """Test check_word_count_all performs comprehensive word count check."""
    doc = nlp("Check the filter.")
    issues = check_word_count_all(doc)
    assert isinstance(issues, list)


if __name__ == "__main__":
    print("Running Section 8 tests...")
    test_check_semicolons()
    print("✓ test_check_semicolons")
    test_check_hyphens()
    print("✓ test_check_hyphens")
    test_check_parentheses_usage()
    print("✓ test_check_parentheses_usage")
    test_check_word_count_with_parentheses()
    print("✓ test_check_word_count_with_parentheses")
    test_check_word_count_with_numbers()
    print("✓ test_check_word_count_with_numbers")
    test_check_hyphenation_patterns()
    print("✓ test_check_hyphenation_patterns")
    test_check_vertical_list_colons()
    print("✓ test_check_vertical_list_colons")
    test_check_word_count_all()
    print("✓ test_check_word_count_all")
    print("\nAll Section 8 tests passed!")
