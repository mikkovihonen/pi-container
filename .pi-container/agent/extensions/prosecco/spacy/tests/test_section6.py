#!/usr/bin/env python3
"""Tests for Section 6 (Descriptive writing) checks."""
import pytest
import spacy
from checks_section6 import (
    check_information_structure,
    check_key_words,
    check_sentence_length_descriptive,
    check_paragraph_structure,
    check_paragraph_topic,
    check_paragraph_length,
)

@pytest.fixture
def nlp_model():
    """Load spaCy model once for all tests."""
    return spacy.load("en_core_web_sm")

class TestCheckInformationStructure:
    """Tests for check_information_structure function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("Check the filter. The filter is clean.")
        issues = check_information_structure(doc)
        assert isinstance(issues, list)

class TestCheckKeyWords:
    """Tests for check_key_words function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The filter is clean. This filter is dry.")
        issues = check_key_words(doc)
        assert isinstance(issues, list)
    
    def test_different_keywords(self, nlp_model):
        """Test with different keywords."""
        doc = nlp_model("The filter is clean. The pump is dry.")
        issues = check_key_words(doc)
        assert isinstance(issues, list)

class TestCheckSentenceLengthDescriptive:
    """Tests for check_sentence_length_descriptive function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("This is a very long sentence that exceeds the maximum allowed length for descriptive writing.")
        issues = check_sentence_length_descriptive(doc)
        assert isinstance(issues, list)
    
    def test_short_sentence(self, nlp_model):
        """Test with short sentence."""
        doc = nlp_model("The filter is clean.")
        issues = check_sentence_length_descriptive(doc)
        assert isinstance(issues, list)

class TestCheckParagraphStructure:
    """Tests for check_paragraph_structure function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The filter is clean. The pump is leaky. The valve is stuck.")
        issues = check_paragraph_structure(doc)
        assert isinstance(issues, list)

class TestCheckParagraphTopic:
    """Tests for check_paragraph_topic function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The filter is clean. The pump is leaky. The valve is stuck.")
        issues = check_paragraph_topic(doc)
        assert isinstance(issues, list)

class TestCheckParagraphLength:
    """Tests for check_paragraph_length function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("Sentence one. Sentence two. Sentence three. Sentence four. Sentence five. Sentence six. Sentence seven.")
        issues = check_paragraph_length(doc)
        assert isinstance(issues, list)
    
    def test_short_paragraph(self, nlp_model):
        """Test with short paragraph."""
        doc = nlp_model("Sentence one. Sentence two. Sentence three.")
        issues = check_paragraph_length(doc)
        assert isinstance(issues, list)
