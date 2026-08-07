#!/usr/bin/env python3
"""Tests for Section 3 (Verbs) checks."""
import pytest
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

@pytest.fixture
def nlp_model():
    """Load spaCy model once for all tests."""
    return spacy.load("en_core_web_sm")

class TestCheckVerbForms:
    """Tests for check_verb_forms function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The temperature is going up.")
        issues = check_verb_forms(doc)
        assert isinstance(issues, list)
    
    def test_simple_present(self, nlp_model):
        """Test with simple present tense."""
        doc = nlp_model("The filter cleans.")
        issues = check_verb_forms(doc)
        assert isinstance(issues, list)
    
    def test_simple_past(self, nlp_model):
        """Test with simple past tense."""
        doc = nlp_model("The filter cleaned.")
        issues = check_verb_forms(doc)
        assert isinstance(issues, list)

class TestCheckVerbTenses:
    """Tests for check_verb_tenses function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The filter has been cleaned.")
        issues = check_verb_tenses(doc)
        assert isinstance(issues, list)
    
    def test_present_perfect(self, nlp_model):
        """Test with present perfect tense."""
        doc = nlp_model("The system has processed the data.")
        issues = check_verb_tenses(doc)
        assert isinstance(issues, list)

class TestCheckPastParticipleAsAdjective:
    """Tests for check_past_participle_as_adjective function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The cleaned filter is dry.")
        issues = check_past_participle_as_adjective(doc)
        assert isinstance(issues, list)
    
    def test_normal_adjective(self, nlp_model):
        """Test with normal adjective."""
        doc = nlp_model("The clean filter is dry.")
        issues = check_past_participle_as_adjective(doc)
        assert isinstance(issues, list)

class TestCheckPassiveVoice:
    """Tests for check_passive_voice function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The filter is cleaned by the operator.")
        issues = check_passive_voice(doc)
        assert isinstance(issues, list)
    
    def test_active_voice(self, nlp_model):
        """Test with active voice."""
        doc = nlp_model("The operator cleans the filter.")
        issues = check_passive_voice(doc)
        assert isinstance(issues, list)

class TestCheckPassiveVoiceWithAgent:
    """Tests for check_passive_voice_with_agent function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The filter is cleaned by the technician.")
        issues = check_passive_voice_with_agent(doc)
        assert isinstance(issues, list)

class TestCheckIngForms:
    """Tests for check_ing_forms function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The operating temperature is high.")
        issues = check_ing_forms(doc)
        assert isinstance(issues, list)
    
    def test_normal_verb(self, nlp_model):
        """Test with normal verb."""
        doc = nlp_model("The temperature is high.")
        issues = check_ing_forms(doc)
        assert isinstance(issues, list)

class TestCheckNounAsVerb:
    """Tests for check_noun_as_verb function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("Please pump the fluid.")
        issues = check_noun_as_verb(doc)
        assert isinstance(issues, list)
    
    def test_normal_verb(self, nlp_model):
        """Test with normal verb."""
        doc = nlp_model("Check the filter.")
        issues = check_noun_as_verb(doc)
        assert isinstance(issues, list)
