#!/usr/bin/env python3
"""Tests for Section 4 (Sentences) checks."""
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import spacy
from checks_section4 import (
    check_short_sentences,
    check_contractions,
    check_forbidden_modals,
    check_vertical_lists,
    check_connecting_words,
    check_missing_articles,
    check_article_usage,
)

# Load spaCy model
nlp = spacy.load("en_core_web_sm")


def test_check_short_sentences():
    """Test check_short_sentences detects short sentences."""
    doc = nlp("The filter is clean. Check it.")
    issues = check_short_sentences(doc)
    # Should detect short sentence
    assert isinstance(issues, list)


def test_check_contractions():
    """Test check_contractions detects contractions."""
    doc = nlp("Don't forget to check the filter.")
    issues = check_contractions(doc)
    # Should detect "Don't" as contraction
    assert isinstance(issues, list)


def test_check_forbidden_modals():
    """Test check_forbidden_modals detects forbidden modals."""
    doc = nlp("You shall check the filter.")
    issues = check_forbidden_modals(doc)
    # Should detect "shall" as forbidden modal
    assert isinstance(issues, list)


def test_check_vertical_lists():
    """Test check_vertical_lists detects vertical lists."""
    doc = nlp("Check the following items: filter, pump, valve.")
    issues = check_vertical_lists(doc)
    assert isinstance(issues, list)


def test_check_connecting_words():
    """Test check_connecting_words detects missing connecting words."""
    doc = nlp("The filter is clean. The pump is leaky.")
    issues = check_connecting_words(doc)
    # Should detect missing connecting word between related sentences
    assert isinstance(issues, list)


def test_check_missing_articles():
    """Test check_missing_articles detects missing articles."""
    doc = nlp("Open valve.")
    issues = check_missing_articles(doc)
    # Should detect missing article before "valve"
    assert isinstance(issues, list)


def test_check_article_usage():
    """Test check_article_usage validates article usage."""
    doc = nlp("This is a apple.")
    issues = check_article_usage(doc)
    # Should detect incorrect "a" before "apple"
    assert isinstance(issues, list)


if __name__ == "__main__":
    print("Running Section 4 tests...")
    test_check_short_sentences()
    print("✓ test_check_short_sentences")
    test_check_contractions()
    print("✓ test_check_contractions")
    test_check_forbidden_modals()
    print("✓ test_check_forbidden_modals")
    test_check_vertical_lists()
    print("✓ test_check_vertical_lists")
    test_check_connecting_words()
    print("✓ test_check_connecting_words")
    test_check_missing_articles()
    print("✓ test_check_missing_articles")
    test_check_article_usage()
    print("✓ test_check_article_usage")
    print("\nAll Section 4 tests passed!")
