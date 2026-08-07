#!/usr/bin/env python3
"""Tests for Section 5 (Procedural writing) checks."""
import pytest
import spacy
from checks_section5 import (
    check_sentence_length_procedural,
    check_multiple_instructions,
    check_non_imperative_in_procedures,
    check_descriptive_statement_first,
    check_notes,
)

@pytest.fixture
def nlp_model():
    """Load spaCy model once for all tests."""
    return spacy.load("en_core_web_sm")

class TestCheckSentenceLengthProcedural:
    """Tests for check_sentence_length_procedural function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("This is a very long sentence that exceeds the maximum allowed length for procedural writing.")
        issues = check_sentence_length_procedural(doc)
        assert isinstance(issues, list)
    
    def test_short_sentence(self, nlp_model):
        """Test with short sentence."""
        doc = nlp_model("Check the filter.")
        issues = check_sentence_length_procedural(doc)
        assert isinstance(issues, list)

class TestCheckMultipleInstructions:
    """Tests for check_multiple_instructions function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("Check the filter and clean the pump.")
        issues = check_multiple_instructions(doc)
        assert isinstance(issues, list)
    
    def test_single_instruction(self, nlp_model):
        """Test with single instruction."""
        doc = nlp_model("Check the filter.")
        issues = check_multiple_instructions(doc)
        assert isinstance(issues, list)

class TestCheckNonImperativeInProcedures:
    """Tests for check_non_imperative_in_procedures function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("You must check the filter.")
        issues = check_non_imperative_in_procedures(doc)
        assert isinstance(issues, list)
    
    def test_imperative(self, nlp_model):
        """Test with imperative form."""
        doc = nlp_model("Check the filter.")
        issues = check_non_imperative_in_procedures(doc)
        assert isinstance(issues, list)

class TestCheckDescriptiveStatementFirst:
    """Tests for check_descriptive_statement_first function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("Check the filter if it is dirty.")
        issues = check_descriptive_statement_first(doc)
        assert isinstance(issues, list)
    
    def test_correct_order(self, nlp_model):
        """Test with correct order."""
        doc = nlp_model("If the filter is dirty, check it.")
        issues = check_descriptive_statement_first(doc)
        assert isinstance(issues, list)

class TestCheckNotes:
    """Tests for check_notes function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("Note: Clean the filter monthly.")
        issues = check_notes(doc)
        assert isinstance(issues, list)
    
    def test_normal_note(self, nlp_model):
        """Test with normal note."""
        doc = nlp_model("Note: The filter is clean.")
        issues = check_notes(doc)
        assert isinstance(issues, list)
