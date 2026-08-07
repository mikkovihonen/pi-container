#!/usr/bin/env python3
"""Tests for Section 2 (Multi-word nouns) checks."""
import pytest
import spacy
from checks_section2 import (
    check_multi_word_nouns,
    check_too_long_technical_nouns,
    check_technical_noun_clarity,
)

@pytest.fixture
def nlp_model():
    """Load spaCy model once for all tests."""
    return spacy.load("en_core_web_sm")

class TestCheckMultiWordNouns:
    """Tests for check_multi_word_nouns function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The quick brown fox jumps over the lazy dog.")
        issues = check_multi_word_nouns(doc)
        assert isinstance(issues, list)
    
    def test_empty_doc(self, nlp_model):
        """Test with empty document."""
        doc = nlp_model("")
        issues = check_multi_word_nouns(doc)
        assert issues == []
    
    def test_single_word(self, nlp_model):
        """Test with single word."""
        doc = nlp_model("Filter")
        issues = check_multi_word_nouns(doc)
        assert isinstance(issues, list)

class TestCheckTooLongTechnicalNouns:
    """Tests for check_too_long_technical_nouns function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The hydraulic pressure relief valve assembly is loose.")
        issues = check_too_long_technical_nouns(doc)
        assert isinstance(issues, list)
    
    def test_short_noun(self, nlp_model):
        """Test with short technical noun."""
        doc = nlp_model("The filter is clean.")
        issues = check_too_long_technical_nouns(doc)
        assert isinstance(issues, list)

class TestCheckTechnicalNounClarity:
    """Tests for check_technical_noun_clarity function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The filter is clogged.")
        issues = check_technical_noun_clarity(doc)
        assert isinstance(issues, list)
    
    def test_multiple_sentences(self, nlp_model):
        """Test with multiple sentences."""
        doc = nlp_model("The filter is clogged. Clean it monthly.")
        issues = check_technical_noun_clarity(doc)
        assert isinstance(issues, list)
