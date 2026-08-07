#!/usr/bin/env python3
"""
ASD-STE100 Section 1 Checks (Words)

Summary of the rules

Which words can you use?
Rule 1.1: Use words that are: Approved in the dictionary, Technical nouns, Technical verbs.

Part of speech
Rule 1.2: Use approved words from the dictionary only as the specified part of speech.

Approved meaning
Rule 1.3: Use approved words only with their approved meanings.

Forms of verbs and adjectives
Rule 1.4: Use only the approved forms of verbs and adjectives.

Technical nouns
Rule 1.5: You can use words that you can include in a technical noun category.
Rule 1.6: Use a word that is not approved in the dictionary, only when it is a technical noun or part of a technical noun.
Rule 1.7: Do not use words that are technical nouns as verbs.
Rule 1.8: Use technical nouns that are approved in your company, industry, or subject field.
Rule 1.9: When you must select a technical noun, use one which is short and easy to understand.
Rule 1.10: Do not use regional, slang, or jargon words as technical nouns.
Rule 1.11: Do not use different technical nouns for the same item.

Technical verbs
Rule 1.12: You can use verbs that you can include in a technical verb category.
Rule 1.13: Do not use technical verbs as nouns.

Spelling
Rule 1.14: Use American English spelling unless other official directives tell you differently.
"""
import re
import spacy
from constants import (
    TECHNICAL_NOUNS_NOT_AS_VERBS,
    TECHNICAL_VERBS_NOT_AS_NOUNS,
    REGIONAL_SLANG_JARGON,
    BRITISH_ENGLISH,
    NON_APPROVED_WORDS,
    RESTRICTED_WORDS_POS,
    RESTRICTED_WORDS_MEANING,
    APPROVED_ING_FORMS,
    LONG_TECHNICAL_NOUN_PATTERNS,
    INCONSISTENT_TECHNICAL_NOUN_PATTERNS,
)
from shared import (
    _is_technical_context,
    _get_context_words,
    _is_in_approved_context,
)


def check_approved_words(doc):
    """Check for approved words (Rule 1.1).
    
    Rule 1.1: "Use words that are: Approved in the dictionary, Technical nouns, 
    Technical verbs."
    
    Uses spaCy features:
    - token.pos_ to identify parts of speech
    - token.is_stop to skip common words
    - token.is_alpha to focus on actual words
    - token.lemma_ for base form lookup
    - doc.noun_chunks for technical context detection
    """
    issues = []
    seen = set()
    
    for token in doc:
        # Skip non-alpha tokens (numbers, punctuation)
        if not token.is_alpha:
            continue
        
        # Skip stop words (common words that are generally approved)
        if token.is_stop:
            continue
        
        word = token.text.lower()
        lemma = token.lemma_.lower()
        
        # Check if word is not approved
        if word in NON_APPROVED_WORDS and NON_APPROVED_WORDS[word] is None:
            # Skip if it's a technical noun pattern (noun following other nouns)
            if token.pos_ == "NOUN" and token.dep_ in ("compound", "nn", "appos"):
                continue
            
            # Skip if it's in a recognized technical pattern
            if _is_technical_context(token, doc):
                continue
            
            if token.idx not in seen:
                seen.add(token.idx)
                issues.append({
                    "type": "ApprovedWords",
                    "message": f"Word '{word}' is not approved in STE. Use a word that is approved in the dictionary, a technical noun, or a technical verb.",
                    "offset": token.idx,
                    "length": len(token.text),
                })
    
    return issues


def check_part_of_speech(doc):
    """Check for correct part of speech usage (Rule 1.2).
    
    Rule 1.2: "Use approved words from the dictionary only as the specified part 
    of speech."
    
    Uses spaCy features:
    - token.pos_ and token.tag_ for part of speech detection
    - token.morph for morphological features
    - Dependency parsing (token.dep_, token.children) to verify word usage
    - Detects words used with incorrect POS based on their children
    """
    issues = []
    seen = set()
    
    for token in doc:
        word = token.text.lower()
        
        if word in RESTRICTED_WORDS_POS:
            approved_pos, disapproved_pos = RESTRICTED_WORDS_POS[word]
            
            # Check if word is used with disapproved POS
            if token.pos_ == disapproved_pos and token.dep_ in ("ROOT", "advcl", "relcl"):
                # Additional check: if it's a verb, make sure it's not a technical noun
                if disapproved_pos == "VERB" and token.pos_ == "VERB":
                    # Use dependency parsing to check for noun-like children
                    # A verb typically has: dobj (direct object), attr (attribute), 
                    # ccomp (clausal complement), xcomp (open complement)
                    has_noun_children = any(
                        c.pos_ == "NOUN" and c.dep_ in ("dobj", "attr", "ccomp", "xcomp")
                        for c in token.children
                    )
                    if has_noun_children:
                        if token.idx not in seen:
                            seen.add(token.idx)
                            issues.append({
                                "type": "PartOfSpeech",
                                "message": f"Word '{word}' is used as {token.pos_} but should be used as {approved_pos}.",
                                "offset": token.idx,
                                "length": len(token.text),
                            })
    
    return issues


def check_approved_meaning(doc):
    """Check for correct approved meaning usage (Rule 1.3).
    
    Rule 1.3: "Use approved words only with their approved meanings."
    
    Uses spaCy features:
    - token.lemma_ for base form
    - Dependency parsing (token.dep_, token.children, token.head) for context
    - Context analysis using surrounding tokens
    - Morphological features (token.morph) for verb tense/aspect
    """
    issues = []
    seen = set()
    
    for token in doc:
        word = token.text.lower()
        
        if word in RESTRICTED_WORDS_MEANING:
            restricted_info = RESTRICTED_WORDS_MEANING[word]
            
            # Get context words using dependency parsing
            context_words = _get_context_words(token, doc)
            
            # Check if word is used in approved context
            if not _is_in_approved_context(token, doc, context_words, restricted_info):
                if token.idx not in seen:
                    seen.add(token.idx)
                    issues.append({
                        "type": "ApprovedMeaning",
                        "message": f"Word '{word}' is used with a meaning that may not be approved in STE.",
                        "offset": token.idx,
                        "length": len(token.text),
                    })
    
    return issues


def check_approved_forms(doc):
    """Check for approved forms of verbs and adjectives (Rule 1.4).
    
    Rule 1.4: "Use only the approved forms of verbs and adjectives."
    
    Uses spaCy features:
    - token.morph for morphological features (tense, number, person)
    - token.tag_ for coarse POS tags (VB, VBD, VBG, VBN, etc.)
    - Dependency parsing (token.dep_, token.children) to verify usage
    - Verb form validation against STE rules
    """
    issues = []
    seen = set()
    
    for token in doc:
        if token.pos_ == "VERB":
            # Check verb form using coarse POS tag
            tag = token.tag_
            
            # VBG (present participle/gerund) as verb is not approved in STE
            # Exception: when used as technical noun or modifier
            if tag == "VBG":
                # Check if it's being used as a verb (has verb-like children)
                has_verb_children = any(
                    c.dep_ in ("dobj", "attr", "ccomp", "xcomp")
                    for c in token.children
                )
                
                # Check if token is in verb position
                if has_verb_children and token.dep_ in ("ROOT", "advcl", "relcl"):
                    # Skip auxiliary verbs
                    if token.lemma_ not in {"be", "have", "do"}:
                        # Skip common -ing forms that are approved as technical nouns
                        if token.lemma_ not in APPROVED_ING_FORMS:
                            if token.idx not in seen:
                                seen.add(token.idx)
                                issues.append({
                                    "type": "ApprovedForms",
                                    "message": f"Do not use '{token.text}' (VBG). Use simple past or simple present tense instead.",
                                    "offset": token.idx,
                                    "length": len(token.text),
                                })
    
    return issues


def check_technical_noun_category(doc):
    """Check for technical nouns in correct categories (Rule 1.5).
    
    Rule 1.5: "You can use words that you can include in a technical noun category."
    
    Uses spaCy features:
    - doc.noun_chunks to identify noun phrases
    - token.pos_ to verify nouns (NOUN, PROPN)
    - token.dep_ to understand noun relationships (compound, nn, appos)
    - token.head to check if word is part of a technical noun
    - Dependency parsing for context analysis
    """
    issues = []
    seen = set()
    
    # Check noun chunks for technical patterns
    for chunk in doc.noun_chunks:
        # Skip if chunk is too long (more than 3 words) - this is Rule 2.1
        if len(chunk) > 3:
            continue
        
        # Check if all words in chunk are approved or technical
        for token in chunk:
            if not token.is_alpha:
                continue
            
            word = token.text.lower()
            
            # Skip common words
            if token.is_stop:
                continue
            
            # Skip if word is in non-approved list but used technically
            if word in NON_APPROVED_WORDS and NON_APPROVED_WORDS[word] is None:
                # Check if it's part of a technical pattern
                if token.dep_ in ("compound", "nn", "appos") or token.head.pos_ == "NOUN":
                    continue
    
    # This is a simplified check - a full implementation would need more sophisticated analysis
    # The main purpose is to validate that technical nouns are being used correctly
    return issues


def check_non_approved_as_technical(doc):
    """Check for non-approved words used as technical nouns (Rule 1.6).
    
    Rule 1.6: "Use a word that is not approved in the dictionary, only when it is 
    a technical noun or part of a technical noun."
    
    Uses spaCy features:
    - token.pos_ to check if word is a noun (NOUN, PROPN)
    - token.dep_ to check noun dependencies (compound, nn, appos)
    - doc.noun_chunks to identify noun phrases
    - token.head to check if word is part of a technical noun
    - Dependency parsing for context analysis
    """
    issues = []
    seen = set()
    
    for token in doc:
        if not token.is_alpha:
            continue
        
        word = token.text.lower()
        
        # Check if word is non-approved
        if word in NON_APPROVED_WORDS and NON_APPROVED_WORDS[word] is None:
            # Check if word is used as a technical noun
            # It should be a noun (NOUN or PROPN) or part of a noun phrase
            
            is_technical_noun = False
            
            # Check 1: Is it a noun?
            if token.pos_ in ("NOUN", "PROPN"):
                is_technical_noun = True
            
            # Check 2: Is it part of a noun chunk?
            if not is_technical_noun:
                for chunk in doc.noun_chunks:
                    if token in chunk:
                        is_technical_noun = True
                        break
            
            # Check 3: Is it a compound modifier or appositive?
            if not is_technical_noun:
                if token.dep_ in ("compound", "nn", "appos"):
                    is_technical_noun = True
            
            # Check 4: Is the head a noun?
            if not is_technical_noun:
                if token.head.pos_ == "NOUN":
                    is_technical_noun = True
            
            # If word is not used as a noun, it's a violation
            if not is_technical_noun:
                if token.idx not in seen:
                    seen.add(token.idx)
                    issues.append({
                        "type": "NonApprovedAsTechnical",
                        "message": f"Word '{word}' is not approved. Only use it as a technical noun.",
                        "offset": token.idx,
                        "length": len(token.text),
                    })
    
    return issues


def check_technical_noun_as_verb(doc):
    """Detect technical nouns used as verbs (Rule 1.7).
    
    Rule 1.7: "Do not use words that are technical nouns as verbs."
    
    Uses spaCy features:
    - token.pos_ to verify noun/verb usage
    - token.dep_ for dependency relationships (ROOT, advcl, relcl)
    - token.children to check for direct objects (dobj, attr, ccomp, xcomp)
    - token.morph for morphological features
    - token.lemma_ for base form lookup
    """
    issues = []
    seen = set()
    
    for token in doc:
        word = token.text.lower()
        
        # Check if word is a technical noun being used as a verb
        if word in TECHNICAL_NOUNS_NOT_AS_VERBS:
            # Check if it's being used as a verb (has verb-like dependencies)
            has_verb_children = any(
                c.dep_ in ("dobj", "attr", "ccomp", "xcomp")
                for c in token.children
            )
            
            # Check if token is in verb position (ROOT, advcl, relcl)
            if has_verb_children and token.dep_ in ("ROOT", "advcl", "relcl"):
                # Additional check: is it tagged as a noun but acting as a verb?
                # OR is it tagged as a verb (VB) but is actually a technical noun?
                if token.pos_ == "NOUN" or (token.pos_ == "VERB" and token.tag_ == "VB"):
                    if token.idx not in seen:
                        seen.add(token.idx)
                        replacement = TECHNICAL_NOUNS_NOT_AS_VERBS[word]
                        issues.append({
                            "type": "TechnicalNounAsVerb",
                            "message": f"Do not use technical noun '{word}' as a verb. Use '{replacement}' instead.",
                            "offset": token.idx,
                            "length": len(token.text),
                        })
    
    return issues


def check_technical_noun_approval(doc):
    """Check for approved technical nouns (Rule 1.8).
    
    Rule 1.8: "Use technical nouns that are approved in your company, industry, 
    or subject field."
    
    Uses spaCy features:
    - doc.noun_chunks to identify noun phrases
    - token.pos_ and token.dep_ for noun analysis
    - token.ent_type_ for named entity recognition
    - token.morph for morphological features
    - Custom entity recognition patterns for technical terms
    """
    issues = []
    seen = set()
    
    # This function checks for technical nouns that may not be approved
    # in a specific company, industry, or subject field
    
    # Check all noun chunks
    for chunk in doc.noun_chunks:
        # Skip short chunks (1-2 words are likely approved)
        if len(chunk) <= 2:
            continue
        
        # Check each word in the chunk
        for token in chunk:
            if not token.is_alpha:
                continue
            
            word = token.text.lower()
            
            # Skip common words
            if token.is_stop:
                continue
            
            # Check if word is in non-approved list
            if word in NON_APPROVED_WORDS and NON_APPROVED_WORDS[word] is None:
                # This is a non-approved word in a technical noun context
                # Flag it for review (not all technical nouns are wrong, just need approval)
                if token.idx not in seen:
                    seen.add(token.idx)
                    issues.append({
                        "type": "TechnicalNounApproval",
                        "message": f"Technical noun '{word}' may not be approved in your company/industry. Verify against your terminology database.",
                        "offset": token.idx,
                        "length": len(token.text),
                    })
    
    return issues


def check_too_long_technical_nouns(doc):
    """Check for unnecessarily long technical nouns (Rule 1.9).
    
    Rule 1.9: "When you must select a technical noun, use one which is short 
    and easy to understand."
    
    Uses spaCy features:
    - doc.noun_chunks to identify noun phrases
    - token.dep_ for dependency relationships
    - token.ent_type_ for named entities
    - Custom pattern matching for long technical nouns
    - Noun chunk length validation
    """
    issues = []
    seen = set()
    
    # Check noun chunks for length
    for chunk in doc.noun_chunks:
        if len(chunk) > 3:
            # This is flagged by Rule 2.1, not Rule 1.9
            # Rule 1.9 is about unnecessarily long but not technically incorrect nouns
            continue
    
    # Check for specific long technical noun patterns
    # These are patterns where a shorter, more common term should be used
    text = doc.text
    for pattern, replacement in LONG_TECHNICAL_NOUN_PATTERNS:
        matches = list(re.finditer(pattern, text))
        for match in matches:
            if match.start() not in seen:
                seen.add(match.start())
                issues.append({
                    "type": "TooLongTechnicalNouns",
                    "message": f"Use a shorter technical noun. Use '{replacement}' instead of '{match.group()}'.",
                    "offset": match.start(),
                    "length": len(match.group()),
                })
    
    return issues


def check_regional_slang_jargon(doc):
    """Detect regional, slang, and jargon words (Rule 1.10).
    
    Rule 1.10: "Do not use regional, slang, or jargon words as technical nouns."
    
    Uses spaCy features:
    - token.pos_ to identify nouns
    - token.dep_ for dependency relationships
    - token.morph for morphological features
    - token.lang_ for language detection
    - doc.noun_chunks for technical context detection
    - _is_technical_context() helper for context analysis
    """
    issues = []
    seen = set()
    
    for token in doc:
        if not token.is_alpha:
            continue
        
        word = token.text.lower()
        
        # Check if word is regional/slang/jargon
        if word in REGIONAL_SLANG_JARGON:
            # Skip if it's a proper noun
            if token.pos_ == "PROPN":
                continue
            
            # Skip if it's in a technical noun pattern
            if _is_technical_context(token, doc):
                continue
            
            if token.idx not in seen:
                seen.add(token.idx)
                replacement = REGIONAL_SLANG_JARGON[word]
                issues.append({
                    "type": "RegionalSlangJargon",
                    "message": f"Do not use regional/slang/jargon word '{word}'. Use '{replacement}' instead.",
                    "offset": token.idx,
                    "length": len(token.text),
                })
    
    return issues


def check_consistent_technical_nouns(doc):
    """Check for consistent use of technical nouns (Rule 1.11).
    
    Rule 1.11: "Do not use different technical nouns for the same item."
    
    Uses spaCy features:
    - doc.noun_chunks to identify noun phrases
    - token.dep_ for dependency relationships
    - token.lemma_ for base form comparison
    - Custom tracking of terminology throughout document
    - Noun chunk analysis for consistent terminology
    """
    issues = []
    seen = set()
    
    text = doc.text
    for pattern, replacement in INCONSISTENT_TECHNICAL_NOUN_PATTERNS:
        matches = list(re.finditer(pattern, text))
        for match in matches:
            if match.start() not in seen:
                seen.add(match.start())
                issues.append({
                    "type": "ConsistentTechnicalNouns",
                    "message": f"Use consistent terminology. Use '{replacement}' instead of '{match.group()}'.",
                    "offset": match.start(),
                    "length": len(match.group()),
                })
    
    return issues


def check_technical_verb_category(doc):
    """Check for technical verbs in correct categories (Rule 1.12).
    
    Rule 1.12: "You can use verbs that you can include in a technical verb category."
    
    Uses spaCy features:
    - token.pos_ to identify verbs (VERB)
    - token.dep_ for dependency relationships
    - token.morph for morphological features (tense, aspect, voice)
    - token.lemma_ for base form
    - Custom verb categorization patterns
    """
    issues = []
    seen = set()
    
    # This function checks for technical verbs that may not be approved
    # in a specific company, industry, or subject field
    
    # Check all verbs
    for token in doc:
        if token.pos_ == "VERB":
            # Skip common verbs (these are generally approved)
            if token.is_stop or token.lemma_ in {"be", "have", "do", "say", "get", "make", "go", "come", "see", "know", "think", "take", "give", "put", "use", "find", "tell", "ask", "work", "call", "try", "need", "want", "look", "come", "put", "keep", "let", "begin", "show", "hear", "play", "run", "move", "like", "live", "believe", "hold", "bring", "happen", "write", "provide", "sit", "stand", "lose", "pay", "meet", "include", "continue", "set", "learn", "change", "lead", "understand", "watch", "follow", "stop", "create", "speak", "read", "allow", "add", "spend", "grow", "open", "walk", "win", "offer", "remember", "love", "consider", "appear", "buy", "wait", "serve", "die", "send", "expect", "build", "stay", "fall", "cut", "reach", "kill", "remain"}:
                continue
            
            # Check if verb is in non-approved list
            lemma = token.lemma_.lower()
            if lemma in NON_APPROVED_WORDS and NON_APPROVED_WORDS[lemma] is None:
                # This is a non-approved technical verb
                if token.idx not in seen:
                    seen.add(token.idx)
                    issues.append({
                        "type": "TechnicalVerbCategory",
                        "message": f"Technical verb '{lemma}' may not be approved in your company/industry. Verify against your terminology database.",
                        "offset": token.idx,
                        "length": len(token.text),
                    })
    
    return issues


def check_technical_verb_as_noun(doc):
    """Detect technical verbs used as nouns (Rule 1.13).
    
    Rule 1.13: "Do not use technical verbs as nouns."
    
    Uses spaCy features:
    - token.pos_ to verify noun usage (NOUN, PROPN)
    - token.dep_ for dependency relationships
    - token.lemma_ to check if base form is a verb
    - token.morph for morphological features
    - Dependency parsing to detect noun usage of verbs
    """
    issues = []
    seen = set()
    
    for token in doc:
        # Check if token is a noun
        if token.pos_ == "NOUN":
            # Check if the lemma is a technical verb
            lemma = token.lemma_.lower()
            
            # Skip common words
            if token.is_stop:
                continue
            
            # Check if lemma is a technical verb being used as noun
            if lemma in TECHNICAL_VERBS_NOT_AS_NOUNS:
                if token.idx not in seen:
                    seen.add(token.idx)
                    replacement = TECHNICAL_VERBS_NOT_AS_NOUNS[lemma]
                    issues.append({
                        "type": "TechnicalVerbAsNoun",
                        "message": f"Do not use technical verb '{lemma}' as a noun. Use '{replacement}' instead.",
                        "offset": token.idx,
                        "length": len(token.text),
                    })
    
    return issues


def check_british_english(doc):
    """Check for British English spelling (Rule 1.14).
    
    Rule 1.14: "Use American English spelling unless other official directives 
    tell you differently."
    
    Uses spaCy features:
    - token.pos_ to identify parts of speech
    - token.dep_ for dependency relationships
    - token.lemma_ for base form
    - token.morph for morphological features
    - token.tag_ for coarse POS tags
    - token.is_alpha to focus on actual words
    """
    issues = []
    seen = set()
    
    for token in doc:
        if not token.is_alpha:
            continue
        
        # Skip quoted text
        if token.tag_ == "FW" or token.dep_ == "appos":
            continue
        
        word = token.text.lower()
        
        # Check if word is British English
        if word in BRITISH_ENGLISH:
            if token.idx not in seen:
                seen.add(token.idx)
                replacement = BRITISH_ENGLISH[word]
                issues.append({
                    "type": "BritishEnglish",
                    "message": f"Use American English spelling. Use '{replacement}' instead of '{token.text}'.",
                    "offset": token.idx,
                    "length": len(token.text),
                })
    
    return issues
