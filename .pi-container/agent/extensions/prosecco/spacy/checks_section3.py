#!/usr/bin/env python3
"""
ASD-STE100 Section 3 Checks (Verbs)

Summary of the rules

Verb forms and tenses of verbs
Rule 3.1: Use only the verb forms that are given in the dictionary.
Rule 3.2: Use only these verb forms and tenses of verbs:
          - The infinitive form
          - The imperative form (command form)
          - The simple present tense
          - The simple past tense
          - The simple future tense
          - The past participle form (as an adjective).
Rule 3.3: Use the past participle form as an adjective.
Rule 3.4: Do not use auxiliary verbs to make complex verb constructions.
Rule 3.5: Use the "-ing" form of a verb only as a technical noun or as a 
          modifier in a technical noun.

Active voice
Rule 3.6: Use the active voice. In descriptive writing, you can use the passive 
          voice only when the agent is unknown.

How to describe an action
Rule 3.7: Use an approved verb to describe an action, not a noun or other 
          parts of speech.
"""
import re
import spacy
from glossary import (
    BE_VERBS,
    APPROVED_ING_WORDS,
    PASSIVE_EXCEPTIONS,
    APPROVED_VERB_TAGS,
    NOUN_AS_VERB_PATTERNS,
)


def check_verb_forms(doc):
    """Check for approved verb forms only (Rule 3.1).
    
    Rule 3.1: "Use only the verb forms that are given in the dictionary."
    
    Use only the verb forms and tenses of verbs that are given in the dictionary.
    You can find the approved forms in the dictionary entry for each verb.
    
    Uses spaCy features:
    - token.pos_ to identify verbs (VERB, AUX)
    - token.tag_ for coarse POS tags (VB, VBD, VBG, VBN, VBZ, VBP, MD)
    - token.morph for morphological features
    - token.lemma_ for base form lookup
    - Dependency parsing (token.dep_, token.children) to verify verb usage
    """
    issues = []
    seen = set()
    
    for token in doc:
        if token.pos_ == "VERB":
            tag = token.tag_
            lemma = token.lemma_.lower()
            
            # Skip auxiliary verbs (these are checked separately)
            if lemma in {"be", "have", "do"}:
                continue
            
            # Check if tag is approved
            if tag not in APPROVED_VERB_TAGS:
                if token.idx not in seen:
                    seen.add(token.idx)
                    issues.append({
                        "type": "VerbForms",
                        "message": f"Do not use '{token.text}' ({tag}). Use an approved verb form instead.",
                        "offset": token.idx,
                        "length": len(token.text),
                    })
    
    return issues


def check_verb_tenses(doc):
    """Check for forbidden verb tenses (Rule 3.2, 3.4).
    
    Rule 3.2: Use only these verb forms and tenses of verbs:
    - The infinitive form (e.g., "install")
    - The imperative form (e.g., "Install the part")
    - The simple present tense (e.g., "The part fits")
    - The simple past tense (e.g., "The part broke")
    - The simple future tense (e.g., "The part will fit")
    - The past participle form (as an adjective, e.g., "the installed part")
    
    Do not use other forms and tenses that are not approved, for example:
    - The present perfect (e.g., "The part has broken")
    - The past perfect (e.g., "The part had broken")
    - The present/past progressive (e.g., "The part is/was breaking")
    - And all other complex verb constructions.
    
    Rule 3.4: Do not use auxiliary verbs to make complex verb constructions.
    
    Uses spaCy features:
    - token.pos_ to identify auxiliary verbs (AUX)
    - token.tag_ for coarse POS tags (VBN, VBG)
    - token.dep_ for dependency relationships
    - token.head to find the main verb
    - Dependency parsing to identify verb tenses
    """
    issues = []
    seen = set()
    
    for token in doc:
        # Check for present perfect (has/have + past participle)
        if token.text.lower() in ("has", "have") and token.pos_ == "AUX":
            head = token.head
            if head.pos_ == "VERB" and head.tag_ == "VBN":
                key = (token.idx, head.idx)
                if key not in seen:
                    seen.add(key)
                    issues.append({
                        "type": "VerbTenses",
                        "message": "Do not use present perfect tense. Use simple past tense instead.",
                        "offset": token.idx,
                        "length": len(token.text) + 1 + len(head.text),
                    })
        
        # Check for past perfect (had + past participle)
        elif token.text.lower() == "had" and token.pos_ == "AUX":
            head = token.head
            if head.pos_ == "VERB" and head.tag_ == "VBN":
                key = (token.idx, head.idx)
                if key not in seen:
                    seen.add(key)
                    issues.append({
                        "type": "VerbTenses",
                        "message": "Do not use past perfect tense. Use simple past tense instead.",
                        "offset": token.idx,
                        "length": len(token.text) + 1 + len(head.text),
                    })
        
        # Check for present/past progressive (is/are/was/were + present participle)
        elif token.text.lower() in ("is", "are", "was", "were") and token.pos_ == "AUX":
            head = token.head
            if head.pos_ == "VERB" and head.tag_ == "VBG":
                key = (token.idx, head.idx)
                if key not in seen:
                    seen.add(key)
                    if token.text.lower() in ("is", "are"):
                        tense = "present progressive"
                    else:
                        tense = "past progressive"
                    issues.append({
                        "type": "VerbTenses",
                        "message": f"Do not use {tense} tense. Use simple present or simple past tense instead.",
                        "offset": token.idx,
                        "length": len(token.text) + 1 + len(head.text),
                    })
        
        # Check for other complex verb constructions
        # Look for auxiliary verbs followed by main verbs with complex tenses
        elif token.pos_ == "AUX" and token.dep_ != "auxpass":
            head = token.head
            if head.pos_ == "VERB" and head.dep_ == "ROOT":
                # Check for modal + base form (this is approved for "will", "can", "may")
                if token.tag_ == "MD":
                    # Skip approved modals (will, can, may)
                    if token.text.lower() in ("will", "can", "may"):
                        continue
                    # Flag other modals (should, could, might, etc.)
                    issues.append({
                        "type": "VerbTenses",
                        "message": f"Do not use '{token.text}'. Use simple present or simple past tense instead.",
                        "offset": token.idx,
                        "length": len(token.text),
                    })
    
    return issues


def check_past_participle_as_adjective(doc):
    """Check for past participle used as adjective (Rule 3.3).
    
    Rule 3.3: "Use the past participle form as an adjective."
    
    You can use the past participle form of a verb as an adjective. For example:
    - the installed part
    - the broken wire
    - the tightened bolt
    
    This function checks that past participles are used correctly as adjectives
    (not as verbs in complex constructions).
    
    Uses spaCy features:
    - token.tag_ to identify past participle (VBN)
    - token.dep_ to check if used as adjective (amod)
    - token.pos_ to verify adjective usage (ADJ)
    - Dependency parsing (token.children, token.head) to verify usage
    - token.morph for morphological features
    """
    issues = []
    seen = set()
    
    for token in doc:
        # Check for past participle (VBN) used as adjective
        if token.tag_ == "VBN":
            # Check if it's used as an adjective (amod - adjectival modifier)
            if token.dep_ == "amod":
                # This is correct usage - past participle as adjective
                continue
            
            # Check if it's part of a noun chunk as modifier
            is_in_noun_chunk = any(token in chunk for chunk in doc.noun_chunks)
            
            # Check if it's the head of a noun (attributive noun)
            is_attributive = token.dep_ == "compound" or token.dep_ == "nn"
            
            # Check if it's used as a noun (det, nsubj, dobj, etc.)
            is_used_as_noun = token.dep_ in ("det", "nsubj", "dobj", "pobj", "nmod")
            
            # If past participle is not used as adjective and not as noun, it might be an error
            # This is a simplified check - in practice, past participles can be used in various ways
            # The main purpose is to ensure they're not used as complex verb constructions
            if not is_in_noun_chunk and not is_attributive and not is_used_as_noun:
                if token.idx not in seen:
                    seen.add(token.idx)
                    issues.append({
                        "type": "PastParticipleAsAdjective",
                        "message": f"Do not use '{token.text}' as a verb. Use it as an adjective or in a simple verb form.",
                        "offset": token.idx,
                        "length": len(token.text),
                    })
    
    return issues


def check_passive_voice(doc):
    """Detect passive voice using dependency parsing (Rule 3.6).
    
    Rule 3.6: "Use the active voice. In descriptive writing, you can use the 
    passive voice only when the agent is unknown."
    
    Use the active voice. The active voice is the normal voice for describing 
    an action. Use the passive voice only in descriptive writing when the agent 
    is unknown.
    
    Uses spaCy features:
    - token.dep_ to identify auxiliary passive (auxpass)
    - token.pos_ to verify verb usage (VERB)
    - token.tag_ for coarse POS tags (VBN, VBD)
    - token.head to find the main verb
    - Dependency parsing to identify passive constructions
    - PASSIVE_EXCEPTIONS constant for phrases like 'is here', 'is there'
    """
    issues = []
    seen = set()
    
    for token in doc:
        # Check for auxiliary passive (auxpass)
        if token.dep_ == "auxpass":
            main_verb = token.head
            if main_verb.pos_ == "VERB" and main_verb.tag_ in ("VBN", "VBD"):
                key = (token.idx, main_verb.idx)
                if key not in seen:
                    seen.add(key)
                    # Check for exceptions
                    verb_phrase = f"{token.text.lower()} {main_verb.text.lower()}"
                    if verb_phrase not in PASSIVE_EXCEPTIONS:
                        issues.append({
                            "type": "PassiveVoice",
                            "message": "Use the active voice.",
                            "offset": token.idx,
                            "length": len(token.text) + 1 + len(main_verb.text),
                        })
    
    # Also check for regex pattern matches that spaCy might miss
    # This catches cases where the participle doesn't have auxpass dependency
    text = doc.text
    passive_pattern = re.compile(
        r'\b(is|are|was|were|be|been|being)\s+(\w+(?:ed|en))\b',
        re.IGNORECASE
    )
    
    for match in passive_pattern.finditer(text):
        phrase = match.group(0).lower()
        if phrase not in PASSIVE_EXCEPTIONS:
            # Avoid duplicates
            match_key = match.start()
            if not any(abs(issue["offset"] - match_key) < 10 for issue in issues):
                issues.append({
                    "type": "PassiveVoice",
                    "message": "Use the active voice.",
                    "offset": match.start(),
                    "length": len(match.group(0)),
                })
    
    return issues


def check_passive_voice_with_agent(doc):
    """Check for passive voice with known agent (Rule 3.6).
    
    Rule 3.6: "Use the active voice. In descriptive writing, you can use the 
    passive voice only when the agent is unknown."
    
    This function checks for passive voice constructions where the agent is
    known (using "by" phrase).
    
    Uses spaCy features:
    - token.text to identify "by" preposition
    - token.pos_ to verify part of speech (ADP)
    - token.dep_ to check dependency relationships (agent)
    - Dependency parsing to find main verb and auxpass
    - token.children to iterate over verb children
    """
    issues = []
    seen = set()
    
    for token in doc:
        # Check for passive voice with "by" agent
        if token.text.lower() == "by" and token.pos_ == "ADP":
            # Check if this is part of a passive construction
            if token.dep_ == "agent":
                # Find the main verb
                for child in token.head.children:
                    if child.pos_ == "VERB" and child.dep_ == "ROOT":
                        # Check if there's an auxpass
                        has_auxpass = any(c.dep_ == "auxpass" for c in child.children)
                        if has_auxpass:
                            key = child.idx
                            if key not in seen:
                                seen.add(key)
                                issues.append({
                                    "type": "PassiveVoice",
                                    "message": "Use the active voice.",
                                    "offset": child.idx,
                                    "length": len(child.text),
                                })
    
    return issues


def check_ing_forms(doc):
    """Detect -ing forms used as verbs (Rule 3.5).
    
    Rule 3.5: "Use the -ing form of a verb only as a technical noun or as a 
    modifier in a technical noun."
    
    You can use the "-ing" form of a verb as a technical noun or as a modifier 
    in a technical noun. For example:
    - the opening of the valve (technical noun)
    - the lighting system (modifier in a technical noun)
    
    But you cannot use the -ing form as a verb. For example:
    - The operator is opening the valve. (incorrect - use "The operator opens")
    - The valve is opening. (incorrect - use "The valve opens")
    
    Uses spaCy features:
    - token.pos_ to identify auxiliary verbs (AUX)
    - token.text.lower() to check for be verbs (is, are, was, were, be, been, being)
    - token.head to find the main verb
    - token.lemma_ for base form extraction
    - APPROVED_ING_WORDS constant for exceptions (technical nouns like "opening", "closing")
    """
    issues = []
    seen = set()
    
    for token in doc:
        if token.pos_ == "AUX" and token.text.lower() in BE_VERBS:
            head = token.head
            if head.pos_ == "VERB" and head.text.endswith("ing"):
                # Skip approved -ing words
                if head.text.lower() in APPROVED_ING_WORDS:
                    continue
                key = (token.idx, head.idx)
                if key not in seen:
                    seen.add(key)
                    base = head.text[:-3]
                    # Determine the correct auxiliary for the replacement
                    aux = token.text.lower()
                    if aux in ("is", "are", "am"):
                        rep_aux = "do"
                    elif aux in ("was", "were"):
                        rep_aux = "did"
                    else:
                        rep_aux = ""
                    
                    if rep_aux:
                        msg = f"Avoid the '-ing' form of a verb. Use: {rep_aux} {base}"
                    else:
                        msg = f"Avoid the '-ing' form of a verb. Use: {base}"
                    
                    issues.append({
                        "type": "IngForms",
                        "message": msg,
                        "offset": token.idx,
                        "length": len(token.text) + 1 + len(head.text),
                    })
    
    return issues


def check_noun_as_verb(doc):
    """Check for using nouns instead of approved verbs (Rule 3.7).
    
    Rule 3.7: "Use an approved verb to describe an action, not a noun or other 
    parts of speech."
    
    There can be different solutions to give the same information in STE. If there 
    is an approved verb that describes an action, use the approved verb. Verbs 
    describe actions more clearly than nouns or other parts of speech.
    
    Uses spaCy features:
    - re.finditer for pattern matching
    - re.IGNORECASE for case-insensitive matching
    - token-level offset calculation for precise positioning
    """
    issues = []
    seen = set()
    
    text = doc.text
    for pattern, replacement in NOUN_AS_VERB_PATTERNS:
        matches = list(re.finditer(pattern, text, re.IGNORECASE))
        for match in matches:
            if match.start() not in seen:
                seen.add(match.start())
                issues.append({
                    "type": "NounAsVerb",
                    "message": f"Use '{replacement}' instead of '{match.group()}'.",
                    "offset": match.start(),
                    "length": len(match.group()),
                })
    
    return issues
