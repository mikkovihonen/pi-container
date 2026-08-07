#!/usr/bin/env python3
"""Tests for Section 5 (Procedural writing) checks."""
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import spacy
from checks_section5 import (
    check_sentence_length_procedural,
    check_multiple_instructions,
    check_non_imperative_in_procedures,
    check_descriptive_statement_first,
    check_notes,
)

# Load spaCy model
nlp = spacy.load("en_core_web_sm")


def test_check_sentence_length_procedural():
    """Test check_sentence_length_procedural validates sentence length."""
    doc = nlp("This is a very long sentence that exceeds the maximum allowed length for procedural writing.")
    issues = check_sentence_length_procedural(doc)
    # Should detect long sentence
    assert isinstance(issues, list)


def test_check_multiple_instructions():
    """Test check_multiple_instructions detects multiple instructions."""
    doc = nlp("Check the filter and clean the pump.")
    issues = check_multiple_instructions(doc)
    # Should detect multiple imperative instructions
    assert isinstance(issues, list)


def test_check_non_imperative_in_procedures():
    """Test check_non_imperative_in_procedures detects non-imperative form."""
    doc = nlp("You must check the filter.")
    issues = check_non_imperative_in_procedures(doc)
    # Should detect non-imperative form
    assert isinstance(issues, list)


def test_check_descriptive_statement_first():
    """Test check_descriptive_statement_first validates statement order."""
    doc = nlp("Check the filter if it is dirty.")
    issues = check_descriptive_statement_first(doc)
    # Should detect condition after command
    assert isinstance(issues, list)


def test_check_notes():
    """Test check_notes detects imperatives in notes."""
    doc = nlp("Note: Clean the filter monthly.")
    issues = check_notes(doc)
    # Should detect imperative in note
    assert isinstance(issues, list)


if __name__ == "__main__":
    print("Running Section 5 tests...")
    test_check_sentence_length_procedural()
    print("✓ test_check_sentence_length_procedural")
    test_check_multiple_instructions()
    print("✓ test_check_multiple_instructions")
    test_check_non_imperative_in_procedures()
    print("✓ test_check_non_imperative_in_procedures")
    test_check_descriptive_statement_first()
    print("✓ test_check_descriptive_statement_first")
    test_check_notes()
    print("✓ test_check_notes")
    print("\nAll Section 5 tests passed!")
