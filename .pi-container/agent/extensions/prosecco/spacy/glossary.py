#!/usr/bin/env python3
"""
ASD-STE100 constants and configuration for spaCy-based grammar checking.

This module loads constants from asd-ste100_base.jsonl and re-exports them for
backward compatibility with existing imports.

Constants are organized by ASD-STE100 rule category:
- Rule 1.x: Words (technical nouns, verb approvals, false friends, etc.)
- Rule 2.x: Multi-word nouns
- Rule 3.x: Verbs (forms, tenses, passive voice, etc.)
- Rule 4.x: Sentences (contractions, connecting words, articles, etc.)
- Rule 5.x: Procedural writing (sentence length, imperatives, etc.)
- Rule 6.x: Descriptive writing (keywords, paragraph structure, etc.)
- Rule 7.x: Safety instructions
- Rule 8.x: Punctuation and word count
- Rule 9.x: Writing practices (phrasal verbs, word usage, consistent style, etc.)
- GR-1 to GR-8: General recommendations

All constants are imported by the check modules (checks_section*.py) and used
in the data-driven pattern matching approach.

For advanced usage with namespaces and cardinality, use glossary_loader directly:
    from glossary_loader import ConstantsLoader
    loader = ConstantsLoader()
    loader.load('asd-ste100_base.jsonl')
    loader.load('company_glossary.jsonl')  # Override
"""
import os
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union

# Path to asd-ste100_base.jsonl
_CONSTANTS_DIR = Path(__file__).parent
_CONSTANTS_FILE = _CONSTANTS_DIR / 'asd-ste100_base.jsonl'

# Namespace mapping for constants
_NAMESPACE_MAP = {
    'APPROVED_ING_FORMS': 'verbs',
    'APPROVED_ING_WORDS': 'verbs',
    'APPROVED_VERB_TAGS': 'verbs',
    'BE_VERBS': 'verbs',
    'BRITISH_ENGLISH': 'general',
    'COMMON_ABBREVIATIONS': 'punctuation',
    'COMMON_COMPOUND_NOUNS': 'words',
    'COMMON_DETERMINERS': 'descriptive',
    'COMMON_HYPHENATED_TERMS': 'punctuation',
    'COMMON_UNITS': 'punctuation',
    'CONDITIONAL_WORDS': 'procedural',
    'CONNECTING_WORDS': 'sentences',
    'CONSISTENT_STYLE_PATTERNS': 'writing',
    'CONTRACTIONS': 'sentences',
    'EXPLANATION_WORDS': 'punctuation',
    'FALSE_FRIENDS': 'general',
    'FORBIDDEN_MODALS': 'verbs',
    'FORBIDDEN_PUNCTUATION': 'punctuation',
    'GENDER_PRONOUNS': 'general',
    'HIGH_RISK_SAFETY_KEYWORDS': 'safety',
    'IMPERATIVE_VERB_LEMMAS': 'procedural',
    'INCONSISTENT_TECHNICAL_NOUN_PATTERNS': 'multiword',
    'LATIN_ABBREVIATIONS': 'general',
    'LONG_TECHNICAL_NOUN_PATTERNS': 'multiword',
    'NON_APPROVED_WORDS': 'words',
    'NOUN_AS_VERB_PATTERNS': 'verbs',
    'PARENTHESES_ALLOWED_CONTEXTS': 'punctuation',
    'PASSIVE_EXCEPTIONS': 'verbs',
    'PHRASAL_VERBS': 'writing',
    'REGIONAL_SLANG_JARGON': 'words',
    'RESTRICTED_VERB_PHRASES': 'writing',
    'RESTRICTED_WORDS': 'writing',
    'RESTRICTED_WORDS_MEANING': 'words',
    'RESTRICTED_WORDS_POS': 'words',
    'RESTRICTED_WORD_USAGE': 'writing',
    'RISK_INDICATORS': 'safety',
    'SAFETY_KEYWORDS': 'safety',
    'TECHNICAL_NOUNS_NOT_AS_VERBS': 'words',
    'TECHNICAL_VERBS_NOT_AS_NOUNS': 'words',
}


def _load_constants() -> Dict[str, Any]:
    """Load all constants from asd-ste100_base.jsonl."""
    constants = {}
    
    if not _CONSTANTS_FILE.exists():
        raise FileNotFoundError(f"asd-ste100_base.jsonl not found at {_CONSTANTS_FILE}")
    
    with open(_CONSTANTS_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            
            obj = json.loads(line)
            name = obj['name']
            data = obj['data']
            
            # Convert tuple keys in mappings back to tuples for backward compatibility
            if obj.get('type') == 'mapping_tuple_keys':
                converted = {}
                for key_val_pair in data:
                    key = tuple(key_val_pair[0])
                    value = key_val_pair[1]
                    converted[key] = value
                data = converted
            
            constants[name] = data
    
    return constants


# Load constants at module import time
_constants = _load_constants()


# Re-export all constants for backward compatibility
APPROVED_ING_FORMS = _constants['APPROVED_ING_FORMS']
APPROVED_ING_WORDS = _constants['APPROVED_ING_WORDS']
APPROVED_VERB_TAGS = _constants['APPROVED_VERB_TAGS']
BE_VERBS = _constants['BE_VERBS']
BRITISH_ENGLISH = _constants['BRITISH_ENGLISH']
COMMON_ABBREVIATIONS = _constants['COMMON_ABBREVIATIONS']
COMMON_COMPOUND_NOUNS = _constants['COMMON_COMPOUND_NOUNS']
COMMON_DETERMINERS = _constants['COMMON_DETERMINERS']
COMMON_HYPHENATED_TERMS = _constants['COMMON_HYPHENATED_TERMS']
COMMON_UNITS = _constants['COMMON_UNITS']
CONDITIONAL_WORDS = _constants['CONDITIONAL_WORDS']
CONNECTING_WORDS = _constants['CONNECTING_WORDS']
CONSISTENT_STYLE_PATTERNS = _constants['CONSISTENT_STYLE_PATTERNS']
CONTRACTIONS = _constants['CONTRACTIONS']
EXPLANATION_WORDS = _constants['EXPLANATION_WORDS']
FALSE_FRIENDS = _constants['FALSE_FRIENDS']
FORBIDDEN_MODALS = _constants['FORBIDDEN_MODALS']
FORBIDDEN_PUNCTUATION = _constants['FORBIDDEN_PUNCTUATION']
GENDER_PRONOUNS = _constants['GENDER_PRONOUNS']
HIGH_RISK_SAFETY_KEYWORDS = _constants['HIGH_RISK_SAFETY_KEYWORDS']
IMPERATIVE_VERB_LEMMAS = _constants['IMPERATIVE_VERB_LEMMAS']
INCONSISTENT_TECHNICAL_NOUN_PATTERNS = _constants['INCONSISTENT_TECHNICAL_NOUN_PATTERNS']
LATIN_ABBREVIATIONS = _constants['LATIN_ABBREVIATIONS']
LONG_TECHNICAL_NOUN_PATTERNS = _constants['LONG_TECHNICAL_NOUN_PATTERNS']
NON_APPROVED_WORDS = _constants['NON_APPROVED_WORDS']
NOUN_AS_VERB_PATTERNS = _constants['NOUN_AS_VERB_PATTERNS']
PARENTHESES_ALLOWED_CONTEXTS = _constants['PARENTHESES_ALLOWED_CONTEXTS']
PASSIVE_EXCEPTIONS = _constants['PASSIVE_EXCEPTIONS']
PHRASAL_VERBS = _constants['PHRASAL_VERBS']
REGIONAL_SLANG_JARGON = _constants['REGIONAL_SLANG_JARGON']
RESTRICTED_VERB_PHRASES = _constants['RESTRICTED_VERB_PHRASES']
RESTRICTED_WORDS = _constants['RESTRICTED_WORDS']
RESTRICTED_WORDS_MEANING = _constants['RESTRICTED_WORDS_MEANING']
RESTRICTED_WORDS_POS = _constants['RESTRICTED_WORDS_POS']
RESTRICTED_WORD_USAGE = _constants['RESTRICTED_WORD_USAGE']
RISK_INDICATORS = _constants['RISK_INDICATORS']
SAFETY_KEYWORDS = _constants['SAFETY_KEYWORDS']
TECHNICAL_NOUNS_NOT_AS_VERBS = _constants['TECHNICAL_NOUNS_NOT_AS_VERBS']
TECHNICAL_VERBS_NOT_AS_NOUNS = _constants['TECHNICAL_VERBS_NOT_AS_NOUNS']


def get_namespace(constant_name: str) -> str:
    """
    Get the namespace for a constant.
    
    Args:
        constant_name: The constant name (e.g., 'NON_APPROVED_WORDS')
        
    Returns:
        The namespace (e.g., 'words')
    """
    return _NAMESPACE_MAP.get(constant_name, 'general')


def get_all_constants() -> Dict[str, Any]:
    """
    Get all loaded constants.
    
    Returns:
        Dictionary of all constants
    """
    return _constants.copy()


def reload_constants():
    """
    Reload constants from asd-ste100_base.jsonl.
    
    This is useful after modifying the JSONL file or loading overrides.
    """
    global _constants
    _constants = _load_constants()
    
    # Re-export all constants
    global APPROVED_ING_FORMS, APPROVED_ING_WORDS, APPROVED_VERB_TAGS, BE_VERBS
    global BRITISH_ENGLISH, COMMON_ABBREVIATIONS, COMMON_COMPOUND_NOUNS, COMMON_DETERMINERS
    global COMMON_HYPHENATED_TERMS, COMMON_UNITS, CONDITIONAL_WORDS, CONNECTING_WORDS
    global CONSISTENT_STYLE_PATTERNS, CONTRACTIONS, EXPLANATION_WORDS, FALSE_FRIENDS
    global FORBIDDEN_MODALS, FORBIDDEN_PUNCTUATION, GENDER_PRONOUNS, HIGH_RISK_SAFETY_KEYWORDS
    global IMPERATIVE_VERB_LEMMAS, INCONSISTENT_TECHNICAL_NOUN_PATTERNS, LATIN_ABBREVIATIONS
    global LONG_TECHNICAL_NOUN_PATTERNS, NON_APPROVED_WORDS, NOUN_AS_VERB_PATTERNS
    global PARENTHESES_ALLOWED_CONTEXTS, PASSIVE_EXCEPTIONS, PHRASAL_VERBS
    global REGIONAL_SLANG_JARGON, RESTRICTED_VERB_PHRASES, RESTRICTED_WORDS
    global RESTRICTED_WORDS_MEANING, RESTRICTED_WORDS_POS, RESTRICTED_WORD_USAGE
    global RISK_INDICATORS, SAFETY_KEYWORDS, TECHNICAL_NOUNS_NOT_AS_VERBS, TECHNICAL_VERBS_NOT_AS_NOUNS
    
    APPROVED_ING_FORMS = _constants['APPROVED_ING_FORMS']
    APPROVED_ING_WORDS = _constants['APPROVED_ING_WORDS']
    APPROVED_VERB_TAGS = _constants['APPROVED_VERB_TAGS']
    BE_VERBS = _constants['BE_VERBS']
    BRITISH_ENGLISH = _constants['BRITISH_ENGLISH']
    COMMON_ABBREVIATIONS = _constants['COMMON_ABBREVIATIONS']
    COMMON_COMPOUND_NOUNS = _constants['COMMON_COMPOUND_NOUNS']
    COMMON_DETERMINERS = _constants['COMMON_DETERMINERS']
    COMMON_HYPHENATED_TERMS = _constants['COMMON_HYPHENATED_TERMS']
    COMMON_UNITS = _constants['COMMON_UNITS']
    CONDITIONAL_WORDS = _constants['CONDITIONAL_WORDS']
    CONNECTING_WORDS = _constants['CONNECTING_WORDS']
    CONSISTENT_STYLE_PATTERNS = _constants['CONSISTENT_STYLE_PATTERNS']
    CONTRACTIONS = _constants['CONTRACTIONS']
    EXPLANATION_WORDS = _constants['EXPLANATION_WORDS']
    FALSE_FRIENDS = _constants['FALSE_FRIENDS']
    FORBIDDEN_MODALS = _constants['FORBIDDEN_MODALS']
    FORBIDDEN_PUNCTUATION = _constants['FORBIDDEN_PUNCTUATION']
    GENDER_PRONOUNS = _constants['GENDER_PRONOUNS']
    HIGH_RISK_SAFETY_KEYWORDS = _constants['HIGH_RISK_SAFETY_KEYWORDS']
    IMPERATIVE_VERB_LEMMAS = _constants['IMPERATIVE_VERB_LEMMAS']
    INCONSISTENT_TECHNICAL_NOUN_PATTERNS = _constants['INCONSISTENT_TECHNICAL_NOUN_PATTERNS']
    LATIN_ABBREVIATIONS = _constants['LATIN_ABBREVIATIONS']
    LONG_TECHNICAL_NOUN_PATTERNS = _constants['LONG_TECHNICAL_NOUN_PATTERNS']
    NON_APPROVED_WORDS = _constants['NON_APPROVED_WORDS']
    NOUN_AS_VERB_PATTERNS = _constants['NOUN_AS_VERB_PATTERNS']
    PARENTHESES_ALLOWED_CONTEXTS = _constants['PARENTHESES_ALLOWED_CONTEXTS']
    PASSIVE_EXCEPTIONS = _constants['PASSIVE_EXCEPTIONS']
    PHRASAL_VERBS = _constants['PHRASAL_VERBS']
    REGIONAL_SLANG_JARGON = _constants['REGIONAL_SLANG_JARGON']
    RESTRICTED_VERB_PHRASES = _constants['RESTRICTED_VERB_PHRASES']
    RESTRICTED_WORDS = _constants['RESTRICTED_WORDS']
    RESTRICTED_WORDS_MEANING = _constants['RESTRICTED_WORDS_MEANING']
    RESTRICTED_WORDS_POS = _constants['RESTRICTED_WORDS_POS']
    RESTRICTED_WORD_USAGE = _constants['RESTRICTED_WORD_USAGE']
    RISK_INDICATORS = _constants['RISK_INDICATORS']
    SAFETY_KEYWORDS = _constants['SAFETY_KEYWORDS']
    TECHNICAL_NOUNS_NOT_AS_VERBS = _constants['TECHNICAL_NOUNS_NOT_AS_VERBS']
    TECHNICAL_VERBS_NOT_AS_NOUNS = _constants['TECHNICAL_VERBS_NOT_AS_NOUNS']
