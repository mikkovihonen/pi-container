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
    'CONJUNCTION_THAT_PATTERNS': 'general',
    'AMBIGUOUS_PRONOUNS': 'general',
    'AMBIGUOUS_WITH_VERB_GROUPS': 'general',
    'AMBIGUOUS_THIS_CONTEXTS': 'general',
}


def _load_constants() -> Dict[str, Any]:
    """Load all constants from base and project glossaries according to glossaries.yaml config."""
    import yaml
    from pathlib import Path

    constants = {}

    # Load glossary configuration (two levels up from spacy/)
    config_path = _CONSTANTS_FILE.parent.parent / 'glossaries.yaml'
    if not config_path.exists():
        raise FileNotFoundError(f"glossaries.yaml not found at {config_path}")

    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # Sort glossaries by cardinality (ascending)
    glossaries = sorted(config['glossaries'], key=lambda g: g['cardinality'])

    # Base directory for glossaries (parent of spacy/)
    base_dir = _CONSTANTS_FILE.parent.parent

    # Load each glossary in order (later glossaries override earlier ones)
    for glossary in glossaries:
        glossary_path = base_dir / glossary['path']
        if not glossary_path.exists():
            # Skip missing glossary files (e.g., project_glossary.jsonl can be removed if empty)
            continue

        with open(glossary_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                obj = json.loads(line)
                name = obj['name']
                data = obj['data']

                # Handle __REMOVE__ sentinel values and merging for mappings
                if obj.get('type') == 'mapping' and isinstance(data, dict):
                    keys_to_remove = [k for k, v in data.items() if v == '__REMOVE__']
                    other_keys = {k: v for k, v in data.items() if v != '__REMOVE__'}
                    if name in constants and isinstance(constants[name], dict):
                        # Merge: remove specified keys, then add/update other keys
                        for k in keys_to_remove:
                            constants[name].pop(k, None)
                        constants[name].update(other_keys)
                    else:
                        # No existing constant - store as-is
                        constants[name] = other_keys
                    continue

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
CONJUNCTION_THAT_PATTERNS = _constants.get('CONJUNCTION_THAT_PATTERNS', [])
AMBIGUOUS_PRONOUNS = _constants.get('AMBIGUOUS_PRONOUNS', [])
AMBIGUOUS_WITH_VERB_GROUPS = _constants.get('AMBIGUOUS_WITH_VERB_GROUPS', [])
AMBIGUOUS_THIS_CONTEXTS = _constants.get('AMBIGUOUS_THIS_CONTEXTS', [])



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


def add_to_project_glossary(namespace: str, name: str, key: str, value, project_glossary_path: str = None):
    """
    Add an entry to the project glossary file.

    Args:
        namespace: The namespace (e.g., 'words')
        name: The constant name (e.g., 'NON_APPROVED_WORDS')
        key: The key to add/update (e.g., 'privilege')
        value: The value to set. Use '__REMOVE__' to mark the key for removal from the mapping.
        project_glossary_path: Optional path to project glossary file. If None, uses default.

    Returns:
        'added' if the entry was added/updated, 'unchanged' if it already existed with the same value.
    """
    if project_glossary_path is None:
        # Default to project_glossary.jsonl in the same directory as this file
        project_glossary_path = _CONSTANTS_FILE.parent / 'project_glossary.jsonl'

    # Load existing project glossary entries for this namespace/name
    existing_data = {}
    if os.path.exists(project_glossary_path):
        with open(project_glossary_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get('namespace') == namespace and obj.get('name') == name:
                        existing_data = obj.get('data', {})
                        break
                except json.JSONDecodeError:
                    continue

    # Check if the entry already exists with the same value
    if existing_data.get(key) == value:
        return 'unchanged'

    # Update the data
    existing_data[key] = value

    # Find the existing entry or create a new one
    entries = []
    if os.path.exists(project_glossary_path):
        with open(project_glossary_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    entries.append(obj)
                except json.JSONDecodeError:
                    continue

    # Remove existing entry with same namespace and name
    entries = [e for e in entries if not (e.get('namespace') == namespace and e.get('name') == name)]

    # Add the updated entry
    new_entry = {
        'namespace': namespace,
        'name': name,
        'type': 'mapping',
        'data': existing_data
    }
    entries.append(new_entry)

    # Write back to file
    with open(project_glossary_path, 'w', encoding='utf-8') as f:
        for entry in entries:
            f.write(json.dumps(entry) + '\n')

    return 'added'


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
    global CONJUNCTION_THAT_PATTERNS, AMBIGUOUS_PRONOUNS, AMBIGUOUS_WITH_VERB_GROUPS, AMBIGUOUS_THIS_CONTEXTS

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
    CONJUNCTION_THAT_PATTERNS = _constants.get('CONJUNCTION_THAT_PATTERNS', [])
    AMBIGUOUS_PRONOUNS = _constants.get('AMBIGUOUS_PRONOUNS', [])
    AMBIGUOUS_WITH_VERB_GROUPS = _constants.get('AMBIGUOUS_WITH_VERB_GROUPS', [])
    AMBIGUOUS_THIS_CONTEXTS = _constants.get('AMBIGUOUS_THIS_CONTEXTS', [])
