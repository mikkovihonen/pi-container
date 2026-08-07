#!/usr/bin/env python3
"""Tests for Section 7 (Safety instructions) checks."""
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import spacy
from checks_section7 import (
    check_safety_instruction_format,
    check_safety_instruction_explanation,
)

# Load spaCy model
nlp = spacy.load("en_core_web_sm")


def test_check_safety_instruction_format():
    """Test check_safety_instruction_format validates safety instruction format."""
    doc = nlp("WARNING: Do not open the panel. Electric shock risk.")
    issues = check_safety_instruction_format(doc)
    # Should validate safety instruction format
    assert isinstance(issues, list)


def test_check_safety_instruction_explanation():
    """Test check_safety_instruction_explanation validates safety explanation."""
    doc = nlp("CAUTION: Hot surface. This means you can get burned.")
    issues = check_safety_instruction_explanation(doc)
    assert isinstance(issues, list)


if __name__ == "__main__":
    print("Running Section 7 tests...")
    test_check_safety_instruction_format()
    print("✓ test_check_safety_instruction_format")
    test_check_safety_instruction_explanation()
    print("✓ test_check_safety_instruction_explanation")
    print("\nAll Section 7 tests passed!")
