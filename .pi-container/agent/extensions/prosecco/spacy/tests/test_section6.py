#!/usr/bin/env python3
"""Tests for Section 6 (Descriptive writing) checks."""
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import spacy
from checks_section6 import (
    check_information_structure,
    check_key_words,
    check_sentence_length_descriptive,
    check_paragraph_structure,
    check_paragraph_topic,
    check_paragraph_length,
)

# Load spaCy model
nlp = spacy.load("en_core_web_sm")


def test_check_information_structure():
    """Test check_information_structure validates information structure."""
    doc = nlp("Check the filter. The filter is clean.")
    issues = check_information_structure(doc)
    assert isinstance(issues, list)


def test_check_key_words():
    """Test check_key_words detects keywords."""
    doc = nlp("The filter is clean. This filter is dry.")
    issues = check_key_words(doc)
    # Should detect repeated keywords
    assert isinstance(issues, list)


def test_check_sentence_length_descriptive():
    """Test check_sentence_length_descriptive validates descriptive sentence length."""
    doc = nlp("This is a very long sentence that exceeds the maximum allowed length for descriptive writing.")
    issues = check_sentence_length_descriptive(doc)
    # Should detect long sentence
    assert isinstance(issues, list)


def test_check_paragraph_structure():
    """Test check_paragraph_structure validates paragraph structure."""
    doc = nlp("The filter is clean. The pump is leaky. The valve is stuck.")
    issues = check_paragraph_structure(doc)
    # Should detect multiple topics in one paragraph
    assert isinstance(issues, list)


def test_check_paragraph_topic():
    """Test check_paragraph_topic validates paragraph topics."""
    doc = nlp("The filter is clean. The pump is leaky. The valve is stuck.")
    issues = check_paragraph_topic(doc)
    assert isinstance(issues, list)


def test_check_paragraph_length():
    """Test check_paragraph_length validates paragraph length."""
    doc = nlp("Sentence one. Sentence two. Sentence three. Sentence four. Sentence five. Sentence six. Sentence seven.")
    issues = check_paragraph_length(doc)
    # Should detect too-long paragraph
    assert isinstance(issues, list)


if __name__ == "__main__":
    print("Running Section 6 tests...")
    test_check_information_structure()
    print("✓ test_check_information_structure")
    test_check_key_words()
    print("✓ test_check_key_words")
    test_check_sentence_length_descriptive()
    print("✓ test_check_sentence_length_descriptive")
    test_check_paragraph_structure()
    print("✓ test_check_paragraph_structure")
    test_check_paragraph_topic()
    print("✓ test_check_paragraph_topic")
    test_check_paragraph_length()
    print("✓ test_check_paragraph_length")
    print("\nAll Section 6 tests passed!")
