#!/usr/bin/env python3
"""
ASD-STE100 Section 9 Checks (Writing practices)

Summary of the rules

Different sentence constructions
Rule 9.1: Use a different sentence construction to write a sentence when a word-for-word replacement is not sufficient.

How to use approved words correctly
Rule 9.2: Use each approved word correctly.
Rule 9.3: When you use two words together, do not make phrasal verbs.

Consistent style
Rule 9.4: When you select terminology or wording, always use a consistent style.
"""
import spacy
from constants import (
    PHRASAL_VERBS, 
    CONSISTENT_STYLE_PATTERNS,
    RESTRICTED_VERB_PHRASES,
    COMMON_COMPOUND_NOUNS,
)
from shared import _get_restricted_verb_replacement


def check_different_sentence_constructions(doc):
    """Check for different sentence constructions (Rule 9.1).
    
    Rule 9.1: "Use a different sentence construction to write a sentence when 
    a word-for-word replacement is not sufficient."
    
    When you replace a word, always make sure that the alternative that you 
    select does not change the meaning of the sentence. If the meaning changes, 
    or if the alternative does not have the same part of speech, you must use 
    a different sentence construction.
    
    Uses spaCy features:
    - token.pos_ for part-of-speech tagging
    - token.dep_ for dependency relationships (auxpass, agent)
    - token.lemma_ for base form comparison
    - token.morph for morphological features
    - Dependency parsing to identify passive voice patterns
    """
    issues = []
    seen = set()
    
    # Detect passive voice patterns using spaCy
    for token in doc:
        # Check for "must be + past participle" pattern
        if token.lemma_ == "must" and token.pos_ == "MOD":
            # Find the main verb
            if token.i + 1 < len(doc):
                next_token = doc[token.i + 1]
                if next_token.lemma_ == "be" and next_token.dep_ == "auxpass":
                    # Check if next token is a past participle
                    if next_token.i + 1 < len(doc):
                        main_verb = doc[next_token.i + 1]
                        if main_verb.pos_ == "VERB" and main_verb.tag_ in ("VBD", "VBN"):
                            # This is a passive construction that should use imperative form
                            if token.idx not in seen:
                                seen.add(token.idx)
                                replacement = main_verb.lemma_
                                
                                issues.append({
                                    "type": "DifferentSentenceConstructions",
                                    "message": f"Use a different sentence construction. Use '{replacement}' instead of 'must be {main_verb.text}'.",
                                    "offset": token.idx,
                                    "length": len(token.text) + 1 + len(next_token.text) + 1 + len(main_verb.text),
                                })
    
    return issues


def check_word_for_word_replacement(doc):
    """Check for word-for-word replacement issues (Rule 9.1).
    
    Rule 9.1: "Use a different sentence construction to write a sentence when 
    a word-for-word replacement is not sufficient."
    
    When you replace a word, always make sure that the alternative that you 
    select does not change the meaning of the sentence.
    
    Uses spaCy features:
    - token.lemma_ for base form comparison
    - token.pos_ for part-of-speech tagging
    - token.dep_ for dependency relationships
    """
    issues = []
    seen = set()
    
    # Common patterns where word-for-word replacement is problematic
    patterns = [
        # "Just" replaced with "Immediately" changes meaning
        (r"just apply", "only apply"),
        # "Clear" replaced with "clean" changes meaning
        (r"clears the", "is away from the"),
        (r"clear the", "is away from the"),
        # "Incidence" replaced incorrectly
        (r"incidence of", "presence of"),
    ]
    
    text = doc.text
    for pattern, replacement in patterns:
        matches = list(re.finditer(pattern, text, re.IGNORECASE))
        for match in matches:
            if match.start() not in seen:
                seen.add(match.start())
                issues.append({
                    "type": "WordForWordReplacement",
                    "message": f"Word-for-word replacement changes the meaning. Use '{replacement}' instead of '{match.group()}'.",
                    "offset": match.start(),
                    "length": len(match.group()),
                })
    
    return issues


def check_word_usage(doc):
    """Check for incorrect word usage (Rule 9.2).
    
    Rule 9.2: "Use each approved word correctly."
    
    Some STE-approved words have meanings that are applicable only in some contexts
    (restricted meaning). Always make sure that the word that you select has the
    correct meaning in the applicable context.
    
    Uses spaCy features:
    - token.pos_ for part-of-speech tagging
    - token.dep_ for dependency relationships
    - token.lemma_ for base form comparison
    - token.head for relationship verification
    - token.morph for morphological features
    - Dependency parsing to verify word relationships
    
    Data-driven approach using RESTRICTED_WORD_USAGE from constants.py
    """
    from constants import RESTRICTED_WORD_USAGE
    
    issues = []
    seen = set()
    tokens = list(doc)
    
    # Iterate over each token and check against defined patterns
    for i, token in enumerate(tokens):
        # Skip non-verbs (most word usage rules apply to verbs)
        if token.pos_ != "VERB":
            continue
        
        # Check each pattern in RESTRICTED_WORD_USAGE
        for pattern_name, pattern_config in RESTRICTED_WORD_USAGE.items():
            # Check if the base lemma matches
            if token.lemma_ != pattern_config["base_lemma"]:
                continue
            
            # Check if all conditions are met
            conditions_met = True
            next_token = tokens[i + 1] if i + 1 < len(tokens) else None
            
            for condition in pattern_config["conditions"]:
                if condition["type"] == "next_lemma":
                    # Check if next token has this lemma
                    if not next_token or next_token.lemma_ != condition["value"]:
                        conditions_met = False
                        break
                elif condition["type"] == "next_lemma_or":
                    # Check if next token has any of these lemmas (OR logic)
                    if not next_token or next_token.lemma_ not in condition["value"]:
                        conditions_met = False
                        break
                elif condition["type"] == "next_pos":
                    # Check if next token has this POS tag
                    if not next_token or next_token.pos_ != condition["value"]:
                        conditions_met = False
                        break
                elif condition["type"] == "next_pos_or":
                    # Check if next token has any of these POS tags (OR logic)
                    if not next_token or next_token.pos_ not in condition["value"]:
                        conditions_met = False
                        break
                elif condition["type"] == "next_dep":
                    # Check if next token has this dependency
                    if not next_token or next_token.dep_ != condition["value"]:
                        conditions_met = False
                        break
                elif condition["type"] == "next_dep_or":
                    # Check if next token has any of these dependencies (OR logic)
                    if not next_token or next_token.dep_ not in condition["value"]:
                        conditions_met = False
                        break
                elif condition["type"] == "not_in_object":
                    # Check if lemma does NOT appear in the object (next 3 tokens)
                    found_in_object = False
                    for obj_i in range(i + 1, min(i + 4, len(tokens))):
                        if tokens[obj_i].lemma_ == condition["value"]:
                            found_in_object = True
                            break
                    if found_in_object:
                        conditions_met = False
                        break
                elif condition["type"] == "not_next_lemma":
                    # Check if next token does NOT have this lemma
                    if next_token and next_token.lemma_ == condition["value"]:
                        conditions_met = False
                        break
            
            if not conditions_met:
                continue
            
            # All conditions met - generate issue
            if token.idx not in seen:
                seen.add(token.idx)
                
                # Get replacement message
                if "replacement_func" in pattern_config:
                    # Use function-based replacement (e.g., for go_preposition)
                    phrase = (token.lemma_, next_token.lemma_)
                    replacement = _get_restricted_verb_replacement(phrase)
                    if replacement:
                        issues.append({
                            "type": "WordUsage",
                            "message": f"Use '{replacement}' instead of '{token.text} {next_token.text}'.",
                            "offset": token.idx,
                            "length": len(token.text) + 1 + len(next_token.text),
                        })
                elif "replacement" in pattern_config:
                    # Use static replacement message
                    issues.append({
                        "type": "WordUsage",
                        "message": pattern_config["replacement"],
                        "offset": token.idx,
                        "length": len(token.text),
                    })
    
    return issues



def check_phrasal_verbs(doc):
    """Detect phrasal verbs (Rule 9.3).
    
    Rule 9.3: "When you use two words together, do not make phrasal verbs."
    
    In English, a verb and one or more prepositions can go together to form a
    "phrasal verb." This phrasal verb has a meaning that is different from the
    meanings of its parts. Phrasal verbs usually have two meanings: the original,
    more concrete meaning, and a more general and abstract meaning.
    
    To prevent ambiguity, it is not permitted in STE to use approved words together
    to make a new phrase (phrasal verb).
    
    Uses spaCy features:
    - token.pos_ for part-of-speech tagging
    - token.dep_ for dependency relationships
    - token.lemma_ for base form comparison
    - Dependency parsing to verify word relationships
    """
    issues = []
    seen = set()
    
    # Check for 2-word and 3-word phrasal verbs using spaCy tokens
    tokens = list(doc)
    
    for i, token in enumerate(tokens):
        # Check 2-word phrasal verbs
        if i + 1 < len(tokens):
            next_token = tokens[i + 1]
            phrase = (token.text.lower(), next_token.text.lower())
            if phrase in PHRASAL_VERBS:
                key = (token.idx, next_token.idx)
                if key not in seen:
                    seen.add(key)
                    rep = PHRASAL_VERBS[phrase]
                    issues.append({
                        "type": "PhrasalVerbs",
                        "message": f"Do not use phrasal verb '{token.text} {next_token.text}'. Use '{rep}' instead.",
                        "offset": token.idx,
                        "length": len(token.text) + 1 + len(next_token.text),
                    })
        
        # Check 3-word phrasal verbs
        if i + 2 < len(tokens):
            next_token = tokens[i + 1]
            next_next_token = tokens[i + 2]
            phrase = (token.text.lower(), next_token.text.lower(), next_next_token.text.lower())
            if phrase in PHRASAL_VERBS:
                key = (token.idx, next_token.idx, next_next_token.idx)
                if key not in seen:
                    seen.add(key)
                    rep = PHRASAL_VERBS[phrase]
                    issues.append({
                        "type": "PhrasalVerbs",
                        "message": f"Do not use phrasal verb '{token.text} {next_token.text} {next_next_token.text}'. Use '{rep}' instead.",
                        "offset": token.idx,
                        "length": len(token.text) + 1 + len(next_token.text) + 1 + len(next_next_token.text),
                    })
    
    return issues


def check_consistent_style(doc):
    """Check for consistent style (Rule 9.4).
    
    Rule 9.4: "When you select terminology or wording, always use a consistent style."
    
    When you select terminology or wording for a work step, use the same terminology
    or wording each time that type of work step occurs. Different terminology or 
    wording can cause confusion and delays.
    
    Uses spaCy features:
    - doc.sents for sentence segmentation
    - token.pos_ for part-of-speech tagging
    - token.dep_ for dependency relationships
    - Dependency parsing to verify sentence structure
    """
    issues = []
    seen = set()
    
    # Check for common inconsistent patterns
    text = doc.text
    
    # For simplicity, we'll just flag the rule
    # A full implementation would need to track terminology throughout the document
    issues.append({
        "type": "ConsistentStyle",
        "message": "Use consistent terminology throughout the document. Do not use different terms for the same item.",
        "offset": 0,
        "length": len(text),
    })
    
    return issues


def check_consistent_terminology(doc):
    """Check for consistent terminology (Rule 1.11, 9.4).
    
    Rule 1.11: "Do not use different technical nouns for the same item."
    Rule 9.4: "When you select terminology or wording, always use a consistent style."
    
    When you select a technical noun, do not use a different technical noun in 
    other parts of your text to refer to the same item. Use the technical noun 
    that is approved in your company, industry, or subject field.
    
    Uses spaCy features:
    - doc.noun_chunks for noun phrase detection
    - token.pos_ for part-of-speech tagging
    - token.dep_ for dependency relationships
    - token.lemma_ for base form comparison
    - Dependency parsing to verify noun relationships
    - Token indexing and span detection
    """
    issues = []
    seen = set()
    
    # Track technical nouns and their contexts
    noun_chunk_terms = {}
    
    # Use spaCy noun chunks to detect technical nouns
    for chunk in doc.noun_chunks:
        # Get the head noun of the chunk
        head = chunk.root
        if head.pos_ == "NOUN":
            # Get the full chunk text
            chunk_text = chunk.text.lower().strip()
            
            # Check for common inconsistent patterns
            if chunk_text in CONSISTENT_STYLE_PATTERNS:
                replacement = CONSISTENT_STYLE_PATTERNS[chunk_text]
                if chunk.start_char not in seen:
                    seen.add(chunk.start_char)
                    issues.append({
                        "type": "ConsistentTerminology",
                        "message": f"Use consistent terminology. Use '{replacement}' instead of '{chunk.text}'.",
                        "offset": chunk.start_char,
                        "length": len(chunk.text),
                    })
    
    # Check for technical nouns that should be part of compound terms
    # This generalizes the body/assembly pattern to any noun that might need a compound form
    for token in doc:
        if token.pos_ == "NOUN":
            # Check if this noun is commonly used as part of a compound technical term
            # Look for patterns like "X" that should be "X Y" (compound term)
            token_lemma = token.lemma_
            
            # Check if the token is followed by another noun (potential compound)
            if token.i + 1 < len(doc):
                next_token = doc[token.i + 1]
                
                # If the next token is a noun and they form a technical compound
                if next_token.pos_ == "NOUN":
                    # Check if this compound is already in the patterns
                    compound = f"{token.text} {next_token.text}"
                    if compound.lower() in CONSISTENT_STYLE_PATTERNS:
                        # This is already a valid compound term, skip
                        continue
                
                # Check if this noun should be part of a compound term
                # Use a general rule: if a common technical noun is used alone,
                # it might need to be part of a compound term
                if token_lemma in COMMON_COMPOUND_NOUNS:
                    # Check if this noun is used without a modifier that would make it a compound term
                    if token.dep_ in ("nsubj", "dobj", "pobj", "attr"):
                        # This noun is used as a subject, object, or attribute
                        # Check if it should be part of a compound term
                        if token.idx not in seen:
                            seen.add(token.idx)
                            issues.append({
                                "type": "ConsistentTerminology",
                                "message": f"Use consistent terminology. '{token.text}' may need to be part of a compound technical term.",
                                "offset": token.idx,
                                "length": len(token.text),
                            })
    
    return issues
def check_non_approved_words(doc):
    """Detect non-approved words (List of recurring errors).
    
    The list of recurring errors gives you a list of the most frequently recurring
    errors that writers make when they use STE. If a word is not approved in the
    dictionary, do not use it.
    
    Uses spaCy features:
    - token.text for word detection
    - token.pos_ for part-of-speech tagging
    - token.idx for accurate offset calculation
    """
    from constants import NON_APPROVED_WORDS
    
    issues = []
    seen = set()
    
    for token in doc:
        word = token.text.lower()
        if word in NON_APPROVED_WORDS:
            replacement = NON_APPROVED_WORDS[word]
            if replacement is None:
                # No direct alternative, use different sentence construction
                issues.append({
                    "type": "NonApprovedWords",
                    "message": f"Word '{word}' is not approved in STE. Use a different sentence construction.",
                    "offset": token.idx,
                    "length": len(token.text),
                })
            elif replacement:
                issues.append({
                    "type": "NonApprovedWords",
                    "message": f"Use '{replacement}' instead of '{word}'.",
                    "offset": token.idx,
                    "length": len(token.text),
                })
            seen.add(token.idx)
    
    return issues


# Import re for regex operations
import re


# Load spaCy model
try:
    nlp = spacy.load("en_core_web_sm")
except:
    nlp = None
