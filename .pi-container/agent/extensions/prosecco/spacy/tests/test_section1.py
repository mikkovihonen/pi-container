#!/usr/bin/env python3
"""Tests for Section 1 (Words) checks."""
import pytest
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

@pytest.fixture
def nlp_model():
    """Load spaCy model once for all tests."""
    return spacy.load("en_core_web_sm")

class TestCheckApprovedWords:
    """Tests for check_approved_words function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The filter is clean.")
        issues = check_approved_words(doc)
        assert isinstance(issues, list)
    
    def test_empty_doc(self, nlp_model):
        """Test with empty document."""
        doc = nlp_model("")
        issues = check_approved_words(doc)
        assert issues == []
    
    def test_single_word(self, nlp_model):
        """Test with single word."""
        doc = nlp_model("Filter")
        issues = check_approved_words(doc)
        assert isinstance(issues, list)

class TestCheckPartOfSpeech:
    """Tests for check_part_of_speech function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The quick brown fox jumps over the lazy dog.")
        issues = check_part_of_speech(doc)
        assert isinstance(issues, list)
    
    def test_valid_pos(self, nlp_model):
        """Test with valid POS tags."""
        doc = nlp_model("Check the filter.")
        issues = check_part_of_speech(doc)
        assert isinstance(issues, list)

class TestCheckApprovedMeaning:
    """Tests for check_approved_meaning function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The filter is clean.")
        issues = check_approved_meaning(doc)
        assert isinstance(issues, list)
    
    def test_multiple_words(self, nlp_model):
        """Test with multiple words."""
        doc = nlp_model("The filter is clean and dry.")
        issues = check_approved_meaning(doc)
        assert isinstance(issues, list)

class TestCheckApprovedForms:
    """Tests for check_approved_forms function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The temperature is going up.")
        issues = check_approved_forms(doc)
        assert isinstance(issues, list)
    
    def test_simple_past(self, nlp_model):
        """Test with simple past tense."""
        doc = nlp_model("The filter cleaned.")
        issues = check_approved_forms(doc)
        assert isinstance(issues, list)

class TestCheckTechnicalNounCategory:
    """Tests for check_technical_noun_category function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The body assembly is secure.")
        issues = check_technical_noun_category(doc)
        assert isinstance(issues, list)
    
    def test_single_noun(self, nlp_model):
        """Test with single noun."""
        doc = nlp_model("The filter.")
        issues = check_technical_noun_category(doc)
        assert isinstance(issues, list)

class TestCheckNonApprovedAsTechnical:
    """Tests for check_non_approved_as_technical function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The widget is broken.")
        issues = check_non_approved_as_technical(doc)
        assert isinstance(issues, list)
    
    def test_valid_technical_noun(self, nlp_model):
        """Test with valid technical noun."""
        doc = nlp_model("The filter assembly.")
        issues = check_non_approved_as_technical(doc)
        assert isinstance(issues, list)

class TestCheckTechnicalNounAsVerb:
    """Tests for check_technical_noun_as_verb function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("Please filter the fluid.")
        issues = check_technical_noun_as_verb(doc)
        assert isinstance(issues, list)
    
    def test_normal_verb(self, nlp_model):
        """Test with normal verb."""
        doc = nlp_model("Check the filter.")
        issues = check_technical_noun_as_verb(doc)
        assert isinstance(issues, list)

class TestCheckTechnicalNounApproval:
    """Tests for check_technical_noun_approval function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The pump assembly is leaky.")
        issues = check_technical_noun_approval(doc)
        assert isinstance(issues, list)

class TestCheckRegionalSlangJargon:
    """Tests for check_regional_slang_jargon function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The fix is bollocks.")
        issues = check_regional_slang_jargon(doc)
        assert isinstance(issues, list)
    
    def test_normal_text(self, nlp_model):
        """Test with normal text."""
        doc = nlp_model("Check the filter.")
        issues = check_regional_slang_jargon(doc)
        assert isinstance(issues, list)

class TestCheckConsistentTechnicalNouns:
    """Tests for check_consistent_technical_nouns function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The body is secure. The hull is tight.")
        issues = check_consistent_technical_nouns(doc)
        assert isinstance(issues, list)

class TestCheckTechnicalVerbCategory:
    """Tests for check_technical_verb_category function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The system processes data.")
        issues = check_technical_verb_category(doc)
        assert isinstance(issues, list)

class TestCheckTechnicalVerbAsNoun:
    """Tests for check_technical_verb_as_noun function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The run was successful.")
        issues = check_technical_verb_as_noun(doc)
        assert isinstance(issues, list)

class TestCheckBritishEnglish:
    """Tests for check_british_english function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The colour is red.")
        issues = check_british_english(doc)
        assert isinstance(issues, list)
    
    def test_american_english(self, nlp_model):
        """Test with American English."""
        doc = nlp_model("The color is red.")
        issues = check_british_english(doc)
        assert isinstance(issues, list)
