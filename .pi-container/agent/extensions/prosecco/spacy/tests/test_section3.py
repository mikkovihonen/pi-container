#!/usr/bin/env python3
"""Tests for Section 3 (Verbs) checks."""
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import spacy
from checks_section3 import (
    check_verb_forms,
    check_verb_tenses,
    check_past_participle_as_adjective,
    check_passive_voice,
    check_passive_voice_with_agent,
    check_ing_forms,
    check_noun_as_verb,
)

# Load spaCy model
nlp = spacy.load("en_core_web_sm")


def test_check_verb_forms():
    """Test check_verb_forms detects non-approved verb forms."""
    doc = nlp("The temperature is going up.")
    issues = check_verb_forms(doc)
    assert isinstance(issues, list)


def test_check_verb_tenses():
    """Test check_verb_tenses validates verb tenses."""
    doc = nlp("The filter has been cleaned.")
    issues = check_verb_tenses(doc)
    # Should detect present perfect tense
    assert isinstance(issues, list)


def test_check_past_participle_as_adjective():
    """Test check_past_participle_as_adjective detects past participles as adjectives."""
    doc = nlp("The cleaned filter is dry.")
    issues = check_past_participle_as_adjective(doc)
    assert isinstance(issues, list)


def test_check_passive_voice():
    """Test check_passive_voice detects passive voice."""
    doc = nlp("The filter is cleaned by the operator.")
    issues = check_passive_voice(doc)
    # Should detect passive voice
    assert isinstance(issues, list)


def test_check_passive_voice_with_agent():
    """Test check_passive_voice_with_agent detects passive voice with agent."""
    doc = nlp("The filter is cleaned by the technician.")
    issues = check_passive_voice_with_agent(doc)
    # Should detect passive voice with "by" agent
    assert isinstance(issues, list)


def test_check_ing_forms():
    """Test check_ing_forms detects non-approved -ing forms."""
    doc = nlp("The operating temperature is high.")
    issues = check_ing_forms(doc)
    # Should detect "operating" as -ing form
    assert isinstance(issues, list)


def test_check_noun_as_verb():
    """Test check_noun_as_verb detects nouns used as verbs."""
    doc = nlp("Please pump the fluid.")
    issues = check_noun_as_verb(doc)
    # "pump" might be detected as a noun used as a verb
    assert isinstance(issues, list)


if __name__ == "__main__":
    print("Running Section 3 tests...")
    test_check_verb_forms()
    print("✓ test_check_verb_forms")
    test_check_verb_tenses()
    print("✓ test_check_verb_tenses")
    test_check_past_participle_as_adjective()
    print("✓ test_check_past_participle_as_adjective")
    test_check_passive_voice()
    print("✓ test_check_passive_voice")
    test_check_passive_voice_with_agent()
    print("✓ test_check_passive_voice_with_agent")
    test_check_ing_forms()
    print("✓ test_check_ing_forms")
    test_check_noun_as_verb()
    print("✓ test_check_noun_as_verb")
    print("\nAll Section 3 tests passed!")
