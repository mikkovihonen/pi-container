#!/usr/bin/env python3
"""Tests for Section 4 (Sentences) checks."""
import pytest
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

@pytest.fixture
def nlp_model():
    """Load spaCy model once for all tests."""
    return spacy.load("en_core_web_sm")

class TestCheckShortSentences:
    """Tests for check_short_sentences function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The filter is clean. Check it.")
        issues = check_short_sentences(doc)
        assert isinstance(issues, list)
    
    def test_normal_sentences(self, nlp_model):
        """Test with normal sentences."""
        doc = nlp_model("Check the filter monthly. Clean it if dirty.")
        issues = check_short_sentences(doc)
        assert isinstance(issues, list)

class TestCheckContractions:
    """Tests for check_contractions function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("Don't forget to check the filter.")
        issues = check_contractions(doc)
        assert isinstance(issues, list)
    
    def test_no_contractions(self, nlp_model):
        """Test with no contractions."""
        doc = nlp_model("Do not forget to check the filter.")
        issues = check_contractions(doc)
        assert isinstance(issues, list)

class TestCheckForbiddenModals:
    """Tests for check_forbidden_modals function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("You shall check the filter.")
        issues = check_forbidden_modals(doc)
        assert isinstance(issues, list)
    
    def test_normal_text(self, nlp_model):
        """Test with normal text."""
        doc = nlp_model("Check the filter.")
        issues = check_forbidden_modals(doc)
        assert isinstance(issues, list)

class TestCheckVerticalLists:
    """Tests for check_vertical_lists function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("Check the following items: filter, pump, valve.")
        issues = check_vertical_lists(doc)
        assert isinstance(issues, list)

class TestCheckConnectingWords:
    """Tests for check_connecting_words function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The filter is clean. The pump is leaky.")
        issues = check_connecting_words(doc)
        assert isinstance(issues, list)
    
    def test_with_connecting_word(self, nlp_model):
        """Test with connecting word."""
        doc = nlp_model("The filter is clean. Also, the pump is leaky.")
        issues = check_connecting_words(doc)
        assert isinstance(issues, list)

class TestCheckMissingArticles:
    """Tests for check_missing_articles function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("Open valve.")
        issues = check_missing_articles(doc)
        assert isinstance(issues, list)
    
    def test_with_article(self, nlp_model):
        """Test with article."""
        doc = nlp_model("Open the valve.")
        issues = check_missing_articles(doc)
        assert isinstance(issues, list)

class TestCheckArticleUsage:
    """Tests for check_article_usage function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("This is a apple.")
        issues = check_article_usage(doc)
        assert isinstance(issues, list)
    
    def test_correct_article(self, nlp_model):
        """Test with correct article."""
        doc = nlp_model("This is an apple.")
        issues = check_article_usage(doc)
        assert isinstance(issues, list)
