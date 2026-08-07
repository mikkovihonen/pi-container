#!/usr/bin/env python3
"""Tests for General Recommendations (GR-1 to GR-8) checks."""
import pytest
import spacy
from checks_gr_recommendations import (
    check_conjunction_that,
    check_ambiguous_with,
    check_ambiguous_pronouns,
    check_ambiguous_this,
    check_false_friends,
    check_latin_abbreviations,
    check_gender_pronouns,
    check_possessive_form,
)

@pytest.fixture
def nlp_model():
    """Load spaCy model once for all tests."""
    return spacy.load("en_core_web_sm")

class TestCheckConjunctionThat:
    """Tests for check_conjunction_that function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("Make sure the filter is clean.")
        issues = check_conjunction_that(doc)
        assert isinstance(issues, list)
    
    def test_with_that(self, nlp_model):
        """Test with 'that'."""
        doc = nlp_model("Make sure that the filter is clean.")
        issues = check_conjunction_that(doc)
        assert isinstance(issues, list)

class TestCheckAmbiguousWith:
    """Tests for check_ambiguous_with function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("Check the filter with the wrench.")
        issues = check_ambiguous_with(doc)
        assert isinstance(issues, list)

class TestCheckAmbiguousPronouns:
    """Tests for check_ambiguous_pronouns function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The filter is clean. It is dry.")
        issues = check_ambiguous_pronouns(doc)
        assert isinstance(issues, list)
    
    def test_clear_pronoun(self, nlp_model):
        """Test with clear pronoun reference."""
        doc = nlp_model("Check the filter. It is clean.")
        issues = check_ambiguous_pronouns(doc)
        assert isinstance(issues, list)

class TestCheckAmbiguousThis:
    """Tests for check_ambiguous_this function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The filter is clean. This is dry.")
        issues = check_ambiguous_this(doc)
        assert isinstance(issues, list)

class TestCheckFalseFriends:
    """Tests for check_false_friends function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The actual temperature is high.")
        issues = check_false_friends(doc)
        assert isinstance(issues, list)
    
    def test_normal_text(self, nlp_model):
        """Test with normal text."""
        doc = nlp_model("The current temperature is high.")
        issues = check_false_friends(doc)
        assert isinstance(issues, list)

class TestCheckLatinAbbreviations:
    """Tests for check_latin_abbreviations function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("See i.e. the filter for details.")
        issues = check_latin_abbreviations(doc)
        assert isinstance(issues, list)
    
    def test_normal_text(self, nlp_model):
        """Test with normal text."""
        doc = nlp_model("Check the filter for details.")
        issues = check_latin_abbreviations(doc)
        assert isinstance(issues, list)

class TestCheckGenderPronouns:
    """Tests for check_gender_pronouns function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The operator he checks the filter.")
        issues = check_gender_pronouns(doc)
        assert isinstance(issues, list)
    
    def test_neutral_pronoun(self, nlp_model):
        """Test with neutral pronoun."""
        doc = nlp_model("The operator they check the filter.")
        issues = check_gender_pronouns(doc)
        assert isinstance(issues, list)

class TestCheckPossessiveForm:
    """Tests for check_possessive_form function."""
    
    def test_returns_list(self, nlp_model):
        """Test that function returns a list."""
        doc = nlp_model("The filter's condition is good.")
        issues = check_possessive_form(doc)
        assert isinstance(issues, list)
    
    def test_normal_text(self, nlp_model):
        """Test with normal text."""
        doc = nlp_model("The condition of the filter is good.")
        issues = check_possessive_form(doc)
        assert isinstance(issues, list)
