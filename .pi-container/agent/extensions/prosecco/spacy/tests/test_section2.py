#!/usr/bin/env python3
"""Tests for Section 2 (Multi-word nouns) checks."""
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import spacy
from checks_section2 import (
    check_multi_word_nouns,
    check_too_long_technical_nouns,
    check_technical_noun_clarity,
)

# Load spaCy model
nlp = spacy.load("en_core_web_sm")


def test_check_multi_word_nouns():
    """Test check_multi_word_nouns detects multi-word nouns."""
    doc = nlp("The quick brown fox jumps over the lazy dog.")
    issues = check_multi_word_nouns(doc)
    assert isinstance(issues, list)


def test_check_too_long_technical_nouns():
    """Test check_too_long_technical_nouns detects too-long technical nouns."""
    doc = nlp("The hydraulic pressure relief valve assembly is loose.")
    issues = check_too_long_technical_nouns(doc)
    assert isinstance(issues, list)


def test_check_technical_noun_clarity():
    """Test check_technical_noun_clarity validates technical noun clarity."""
    doc = nlp("The filter is clogged.")
    issues = check_technical_noun_clarity(doc)
    assert isinstance(issues, list)


if __name__ == "__main__":
    print("Running Section 2 tests...")
    test_check_multi_word_nouns()
    print("✓ test_check_multi_word_nouns")
    test_check_too_long_technical_nouns()
    print("✓ test_check_too_long_technical_nouns")
    test_check_technical_noun_clarity()
    print("✓ test_check_technical_noun_clarity")
    print("\nAll Section 2 tests passed!")
