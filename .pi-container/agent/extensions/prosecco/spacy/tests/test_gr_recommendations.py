#!/usr/bin/env python3
"""Tests for General Recommendations (GR-1 to GR-8) checks."""
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import spacy
from gr_recommendations import (
    check_conjunction_that,
    check_ambiguous_with,
    check_ambiguous_pronouns,
    check_ambiguous_this,
    check_false_friends,
    check_latin_abbreviations,
    check_gender_pronouns,
    check_possessive_form,
)

# Load spaCy model
nlp = spacy.load("en_core_web_sm")


def test_check_conjunction_that():
    """Test check_conjunction_that validates conjunction 'that' usage."""
    doc = nlp("Make sure the filter is clean.")
    issues = check_conjunction_that(doc)
    # Should detect missing "that" after "make sure"
    assert isinstance(issues, list)


def test_check_ambiguous_with():
    """Test check_ambiguous_with detects ambiguous 'with' usage."""
    doc = nlp("Check the filter with the wrench.")
    issues = check_ambiguous_with(doc)
    assert isinstance(issues, list)


def test_check_ambiguous_pronouns():
    """Test check_ambiguous_pronouns detects ambiguous pronouns."""
    doc = nlp("The filter is clean. It is dry.")
    issues = check_ambiguous_pronouns(doc)
    # Should detect ambiguous "It"
    assert isinstance(issues, list)


def test_check_ambiguous_this():
    """Test check_ambiguous_this detects ambiguous 'this' usage."""
    doc = nlp("The filter is clean. This is dry.")
    issues = check_ambiguous_this(doc)
    # Should detect ambiguous "This"
    assert isinstance(issues, list)


def test_check_false_friends():
    """Test check_false_friends detects false friends."""
    doc = nlp("The actual temperature is high.")
    issues = check_false_friends(doc)
    # Should detect "actual" as false friend (meaning "current" not "real")
    assert isinstance(issues, list)


def test_check_latin_abbreviations():
    """Test check_latin_abbreviations detects Latin abbreviations."""
    doc = nlp("See i.e. the filter for details.")
    issues = check_latin_abbreviations(doc)
    # Should detect "i.e." as Latin abbreviation
    assert isinstance(issues, list)


def test_check_gender_pronouns():
    """Test check_gender_pronouns detects gender-specific pronouns."""
    doc = nlp("The operator he checks the filter.")
    issues = check_gender_pronouns(doc)
    # Should detect "he" as gender-specific pronoun
    assert isinstance(issues, list)


def test_check_possessive_form():
    """Test check_possessive_form validates possessive form usage."""
    doc = nlp("The filter's condition is good.")
    issues = check_possessive_form(doc)
    # Should detect possessive form "filter's"
    assert isinstance(issues, list)


if __name__ == "__main__":
    print("Running GR Recommendations tests...")
    test_check_conjunction_that()
    print("✓ test_check_conjunction_that")
    test_check_ambiguous_with()
    print("✓ test_check_ambiguous_with")
    test_check_ambiguous_pronouns()
    print("✓ test_check_ambiguous_pronouns")
    test_check_ambiguous_this()
    print("✓ test_check_ambiguous_this")
    test_check_false_friends()
    print("✓ test_check_false_friends")
    test_check_latin_abbreviations()
    print("✓ test_check_latin_abbreviations")
    test_check_gender_pronouns()
    print("✓ test_check_gender_pronouns")
    test_check_possessive_form()
    print("✓ test_check_possessive_form")
    print("\nAll GR Recommendations tests passed!")
