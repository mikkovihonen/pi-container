#!/usr/bin/env python3
"""Tests for Section 7 (Safety instructions) checks."""
import pytest
import spacy
from checks_section7 import (
    check_safety_instruction_format,
    check_safety_instruction_explanation,
)

@pytest.fixture
def nlp_model():
    """Load spaCy model once for all tests."""
    return spacy.load("en_core_web_sm")

class TestCheckSafetyInstructionFormat:
    """Tests for check_safety_instruction_format function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("WARNING: Do not open the panel. Electric shock risk.")
        issues = check_safety_instruction_format(doc)
        assert isinstance(issues, list)
    
    def test_caution(self, nlp_model):
        """Test with CAUTION."""
        doc = nlp_model("CAUTION: Hot surface. Risk of burn.")
        issues = check_safety_instruction_format(doc)
        assert isinstance(issues, list)
    
    def test_danger(self, nlp_model):
        """Test with DANGER."""
        doc = nlp_model("DANGER: High voltage. Do not touch.")
        issues = check_safety_instruction_format(doc)
        assert isinstance(issues, list)

class TestCheckSafetyInstructionExplanation:
    """Tests for check_safety_instruction_explanation function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("CAUTION: Hot surface. This means you can get burned.")
        issues = check_safety_instruction_explanation(doc)
        assert isinstance(issues, list)
    
    def test_normal_text(self, nlp_model):
        """Test with normal text."""
        doc = nlp_model("The filter is clean.")
        issues = check_safety_instruction_explanation(doc)
        assert isinstance(issues, list)
