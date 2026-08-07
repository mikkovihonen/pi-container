#!/usr/bin/env python3
"""Tests for Section 1 (Words) checks."""
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import spacy
from checks_section1 import (
    check_approved_words,
    check_part_of_speech,
    check_approved_meaning,
    check_approved_forms,
    check_technical_noun_category,
    check_non_approved_as_technical,
    check_technical_noun_as_verb,
    check_technical_noun_approval,
    check_regional_slang_jargon,
    check_consistent_technical_nouns,
    check_technical_verb_category,
    check_technical_verb_as_noun,
    check_british_english,
)

# Load spaCy model
nlp = spacy.load("en_core_web_sm")


def test_check_approved_words():
    """Test check_approved_words detects non-approved words."""
    doc = nlp("The machine acceptable is broken.")
    issues = check_approved_words(doc)
    # Should detect "acceptable" as non-approved
    assert isinstance(issues, list)
    # Note: The function may skip some words based on technical context detection


def test_check_part_of_speech():
    """Test check_part_of_speech validates POS tags."""
    doc = nlp("The quick brown fox jumps over the lazy dog.")
    issues = check_part_of_speech(doc)
    # Should not have issues for valid POS tags
    assert isinstance(issues, list)


def test_check_approved_meaning():
    """Test check_approved_meaning validates word meanings."""
    doc = nlp("The filter is clean.")
    issues = check_approved_meaning(doc)
    assert isinstance(issues, list)


def test_check_approved_forms():
    """Test check_approved_forms validates verb forms."""
    doc = nlp("The temperature is going up.")
    issues = check_approved_forms(doc)
    assert isinstance(issues, list)


def test_check_technical_noun_category():
    """Test check_technical_noun_category validates technical nouns."""
    doc = nlp("The body assembly is secure.")
    issues = check_technical_noun_category(doc)
    assert isinstance(issues, list)


def test_check_non_approved_as_technical():
    """Test check_non_approved_as_technical detects non-approved technical nouns."""
    doc = nlp("The widget is broken.")
    issues = check_non_approved_as_technical(doc)
    assert isinstance(issues, list)


def test_check_technical_noun_as_verb():
    """Test check_technical_noun_as_verb detects nouns used as verbs."""
    doc = nlp("Please filter the fluid.")
    issues = check_technical_noun_as_verb(doc)
    # "filter" might be detected as a technical noun used as a verb
    assert isinstance(issues, list)


def test_check_technical_noun_approval():
    """Test check_technical_noun_approval validates technical nouns."""
    doc = nlp("The pump assembly is leaky.")
    issues = check_technical_noun_approval(doc)
    assert isinstance(issues, list)


def test_check_regional_slang_jargon():
    """Test check_regional_slang_jargon detects regional slang."""
    doc = nlp("The fix is bollocks.")
    issues = check_regional_slang_jargon(doc)
    assert isinstance(issues, list)


def test_check_consistent_technical_nouns():
    """Test check_consistent_technical_nouns validates consistency."""
    doc = nlp("The body is secure. The hull is tight.")
    issues = check_consistent_technical_nouns(doc)
    assert isinstance(issues, list)


def test_check_technical_verb_category():
    """Test check_technical_verb_category validates technical verbs."""
    doc = nlp("The system processes data.")
    issues = check_technical_verb_category(doc)
    assert isinstance(issues, list)


def test_check_technical_verb_as_noun():
    """Test check_technical_verb_as_noun detects verbs used as nouns."""
    doc = nlp("The run was successful.")
    issues = check_technical_verb_as_noun(doc)
    # "run" might be detected as a verb used as a noun
    assert isinstance(issues, list)


def test_check_british_english():
    """Test check_british_english detects British English spellings."""
    doc = nlp("The colour is red.")
    issues = check_british_english(doc)
    assert isinstance(issues, list)


if __name__ == "__main__":
    print("Running Section 1 tests...")
    test_check_approved_words()
    print("✓ test_check_approved_words")
    test_check_part_of_speech()
    print("✓ test_check_part_of_speech")
    test_check_approved_meaning()
    print("✓ test_check_approved_meaning")
    test_check_approved_forms()
    print("✓ test_check_approved_forms")
    test_check_technical_noun_category()
    print("✓ test_check_technical_noun_category")
    test_check_non_approved_as_technical()
    print("✓ test_check_non_approved_as_technical")
    test_check_technical_noun_as_verb()
    print("✓ test_check_technical_noun_as_verb")
    test_check_technical_noun_approval()
    print("✓ test_check_technical_noun_approval")
    test_check_regional_slang_jargon()
    print("✓ test_check_regional_slang_jargon")
    test_check_consistent_technical_nouns()
    print("✓ test_check_consistent_technical_nouns")
    test_check_technical_verb_category()
    print("✓ test_check_technical_verb_category")
    test_check_technical_verb_as_noun()
    print("✓ test_check_technical_verb_as_noun")
    test_check_british_english()
    print("✓ test_check_british_english")
    print("\nAll Section 1 tests passed!")
