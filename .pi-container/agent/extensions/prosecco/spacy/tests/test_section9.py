#!/usr/bin/env python3
"""Tests for Section 9 (Writing practices) checks."""
import pytest
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

@pytest.fixture
def nlp_model():
    """Load spaCy model once for all tests."""
    return spacy.load("en_core_web_sm")

class TestCheckWordUsage:
    """Tests for check_word_usage function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The temperature go up by 10 degrees.")
        issues = check_word_usage(doc)
        assert isinstance(issues, list)
    
    def test_normal_text(self, nlp_model):
        """Test with normal text."""
        doc = nlp_model("Check the filter.")
        issues = check_word_usage(doc)
        assert isinstance(issues, list)

class TestCheckConsistentStyle:
    """Tests for check_consistent_style function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The body is secure. The hull is tight.")
        issues = check_consistent_style(doc)
        assert isinstance(issues, list)
    
    def test_empty_doc(self, nlp_model):
        """Test with empty document."""
        doc = nlp_model("")
        issues = check_consistent_style(doc)
        assert isinstance(issues, list)

class TestCheckPhrasalVerbs:
    """Tests for check_phrasal_verbs function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The temperature go up by 10 degrees.")
        issues = check_phrasal_verbs(doc)
        assert isinstance(issues, list)
    
    def test_normal_text(self, nlp_model):
        """Test with normal text."""
        doc = nlp_model("Check the filter.")
        issues = check_phrasal_verbs(doc)
        assert isinstance(issues, list)

class TestCheckConsistentTerminology:
    """Tests for check_consistent_terminology function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The body assembly is secure.")
        issues = check_consistent_terminology(doc)
        assert isinstance(issues, list)

class TestCheckDifferentSentenceConstructions:
    """Tests for check_different_sentence_constructions function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The filter is clean. The pump is leaky.")
        issues = check_different_sentence_constructions(doc)
        assert isinstance(issues, list)

class TestCheckWordForWordReplacement:
    """Tests for check_word_for_word_replacement function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The filter is clean. The filter is dry.")
        issues = check_word_for_word_replacement(doc)
        assert isinstance(issues, list)
    
    def test_different_words(self, nlp_model):
        """Test with different words."""
        doc = nlp_model("The filter is clean. The pump is dry.")
        issues = check_word_for_word_replacement(doc)
        assert isinstance(issues, list)

class TestCheckNonApprovedWords:
    """Tests for check_non_approved_words function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The bollocks is broken.")
        issues = check_non_approved_words(doc)
        assert isinstance(issues, list)
    
    def test_normal_text(self, nlp_model):
        """Test with normal text."""
        doc = nlp_model("Check the filter.")
        issues = check_non_approved_words(doc)
        assert isinstance(issues, list)
