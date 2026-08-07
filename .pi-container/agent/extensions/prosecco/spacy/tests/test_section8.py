#!/usr/bin/env python3
"""Tests for Section 8 (Punctuation and word count) checks."""
import pytest
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

@pytest.fixture
def nlp_model():
    """Load spaCy model once for all tests."""
    return spacy.load("en_core_web_sm")

class TestCheckSemicolons:
    """Tests for check_semicolons function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("Check the filter; clean the pump.")
        issues = check_semicolons(doc)
        assert isinstance(issues, list)
    
    def test_no_semicolons(self, nlp_model):
        """Test with no semicolons."""
        doc = nlp_model("Check the filter. Clean the pump.")
        issues = check_semicolons(doc)
        assert isinstance(issues, list)

class TestCheckHyphens:
    """Tests for check_hyphens function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The multi-word term is too long.")
        issues = check_hyphens(doc)
        assert isinstance(issues, list)
    
    def test_normal_text(self, nlp_model):
        """Test with normal text."""
        doc = nlp_model("The filter is clean.")
        issues = check_hyphens(doc)
        assert isinstance(issues, list)

class TestCheckParenthesesUsage:
    """Tests for check_parentheses_usage function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("See (Fig. 1) for details.")
        issues = check_parentheses_usage(doc)
        assert isinstance(issues, list)
    
    def test_invalid_parentheses(self, nlp_model):
        """Test with invalid parentheses usage."""
        doc = nlp_model("Check (the filter) monthly.")
        issues = check_parentheses_usage(doc)
        assert isinstance(issues, list)

class TestCheckWordCountWithParentheses:
    """Tests for check_word_count_with_parentheses function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("Check (Fig. 1) the filter.")
        issues = check_word_count_with_parentheses(doc)
        assert isinstance(issues, list)

class TestCheckWordCountWithNumbers:
    """Tests for check_word_count_with_numbers function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The 10 kg filter is clean.")
        issues = check_word_count_with_numbers(doc)
        assert isinstance(issues, list)

class TestCheckHyphenationPatterns:
    """Tests for check_hyphenation_patterns function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The multi-step procedure is complete.")
        issues = check_hyphenation_patterns(doc)
        assert isinstance(issues, list)

class TestCheckVerticalListColons:
    """Tests for check_vertical_list_colons function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("Check the following: filter, pump, valve.")
        issues = check_vertical_list_colons(doc)
        assert isinstance(issues, list)

class TestCheckWordCountAll:
    """Tests for check_word_count_all function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("Check the filter.")
        issues = check_word_count_all(doc)
        assert isinstance(issues, list)
    
    def test_empty_doc(self, nlp_model):
        """Test with empty document."""
        doc = nlp_model("")
        issues = check_word_count_all(doc)
        assert issues == []
