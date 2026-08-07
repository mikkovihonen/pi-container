#!/usr/bin/env python3
"""
ASD-STE100 General Recommendations Checks (GR-1 through GR-8)

The general recommendations (GR) in this section are not STE rules. They can help you prevent typical errors that writers make.

GR-1: The conjunction "that"
Use the conjunction "that" after verbs like "make sure," "show," and "recommend" to prevent ambiguity.

GR-2: The preposition "with"
The preposition "with" has three approved meanings: "association or relationship," "help or sharing," or "a means or instrument." In some sentences, this word can cause ambiguity.

GR-3: How to use pronouns
Pronouns refer to a person, a location, or an item that is already in a text. Examples of pronouns are "it," "they," "that," "these," and "those." If a pronoun can refer to one or more nouns in a text, it can cause ambiguity in a sentence.

GR-4: The pronoun "this"
When you use the pronoun "this" in a sentence, make sure that the reader knows the item the pronoun refers to. If "this" can refer to more than one item, give the applicable context again.

GR-5: False friends
A false friend is a word or an expression that looks the same as one in a person's native language but that has a different meaning in a different language.

GR-6: Latin abbreviations
STE recommends that you do not use Latin abbreviations because they can confuse your readers if they do not know them. Always use English words to make the text clear.

GR-7: Inclusive language
Inclusive language prevents bias and makes sure that all persons have respect and representation. STE does not include examples of inclusive language, but it fully complies with gender-neutral language requirements. Gender-specific pronouns, for example "he" or "she" are not permitted in STE.

GR-8: Possessive form
The possessive form (also known as the Saxon genitive) adds an apostrophe and "s" to form the possessive. While permitted in STE, use it correctly. If not sure, do not use it.
"""
import re
from glossary import (
    FALSE_FRIENDS,
    LATIN_ABBREVIATIONS,
    GENDER_PRONOUNS,
    CONJUNCTION_THAT_PATTERNS,
    AMBIGUOUS_PRONOUNS,
    AMBIGUOUS_WITH_VERB_GROUPS,
    AMBIGUOUS_THIS_CONTEXTS,
)


def check_conjunction_that(doc):
    """Check for missing conjunction "that" (GR-1).
    
    GR-1: "The conjunction 'that'"
    
    Use the conjunction "that" after verbs like "make sure," "show," and "recommend"
    to prevent ambiguity.
    
    Configurable via CONJUNCTION_THAT_PATTERNS.
    """
    issues = []
    text = doc.text
    
    # Load patterns from configuration
    patterns = CONJUNCTION_THAT_PATTERNS
    
    for pattern_config in patterns:
        pattern = pattern_config['pattern']
        replacement = pattern_config['replacement']
        regex_pattern = rf"{re.escape(pattern)}\s+(?!that\b)"
        
        matches = list(re.finditer(regex_pattern, text, re.IGNORECASE))
        for match in matches:
            issues.append({
                "type": "ConjunctionThat",
                "message": f"Consider using '{replacement}' instead of '{match.group()}' to prevent ambiguity.",
                "offset": match.start(),
                "length": len(match.group()),
            })
    
    return issues


def check_ambiguous_with(doc):
    """Check for ambiguous use of 'with' (GR-2).
    
    GR-2: "The preposition 'with'"
    
    In STE, the preposition 'with' has three approved meanings. It is a function 
    word that shows 'association or relationship,' 'help or sharing,' or 'a means 
    or instrument.' In some sentences, this word can cause ambiguity.
    
    Uses spaCy dependency parsing to find 'with' as a preposition and checks
    if it follows verbs that could create ambiguity.
    """
    issues = []
    seen = set()
    
    for token in doc:
        # Find 'with' as a preposition
        if token.text.lower() == "with" and token.pos_ == "ADP":
            # Check if it follows a verb (potential ambiguity)
            head = token.head
            if head.pos_ == "VERB" and head.idx not in seen:
                seen.add(head.idx)
                issues.append({
                    "type": "AmbiguousWith",
                    "message": f"Check for ambiguity. '{head.text} with' can mean 'association', 'help', or 'instrument'.",
                    "offset": token.idx,
                    "length": len(token.text),
                })
    
    return issues


def check_ambiguous_pronouns(doc):
    """Check for ambiguous pronoun usage (GR-3).
    
    GR-3: "How to use pronouns"
    
    Pronouns refer to a person, a location, or an item that is already in a text. 
    Examples of pronouns are 'it,' 'they,' 'that,' 'these,' and 'those.' If you use 
    the pronouns correctly, your text will be easy to read.
    
    If a pronoun can refer to one or more nouns in a text, it can cause ambiguity 
    in a sentence. If there is ambiguity, replace the pronoun with the word that 
    it refers to.
    
    Uses spaCy noun_chunks to detect pronouns that may refer to multiple nouns.
    Configurable via AMBIGUOUS_PRONOUNS.
    """
    issues = []
    seen = set()
    
    # Load ambiguous pronouns from configuration
    ambiguous_pronouns = set(AMBIGUOUS_PRONOUNS)
    
    # Get all noun chunks in the document
    noun_chunks = list(doc.noun_chunks)
    
    for token in doc:
        # Find pronouns
        if token.pos_ == "PRON" and token.text.lower() in ambiguous_pronouns:
            if token.idx not in seen:
                seen.add(token.idx)
                
                # Check if multiple noun chunks could be the antecedent
                # (simplified check: if there are multiple chunks in the same sentence)
                token_sent = token.sent
                chunks_in_sent = [c for c in noun_chunks if c.start_char >= token_sent.start_char and c.end_char < token_sent.end_char]
                
                if len(chunks_in_sent) > 1:
                    issues.append({
                        "type": "AmbiguousPronouns",
                        "message": f"Replace '{token.text}' with the specific noun it refers to.",
                        "offset": token.idx,
                        "length": len(token.text),
                    })
    
    return issues


def check_ambiguous_this(doc):
    """Check for ambiguous 'this' pronoun usage (GR-4).
    
    GR-4: "The pronoun 'this'"
    
    When you use the pronoun 'this' in a sentence, make sure that the reader 
    knows the item the pronoun refers to. If 'this' can refer to more than one 
    item, give the applicable context again.
    
    Uses spaCy to detect 'this' as a determiner or pronoun that may be ambiguous.
    """
    issues = []
    seen = set()
    
    for token in doc:
        # Find 'this' as a determiner or pronoun
        if token.text.lower() == "this" and token.pos_ in ("DET", "PRON"):
            if token.idx not in seen:
                seen.add(token.idx)
                
                # Check if followed by a verb (potential ambiguity)
                if token.head.pos_ == "VERB" or token.dep_ in ("nsubj", "dobj"):
                    issues.append({
                        "type": "AmbiguousThis",
                        "message": f"Replace 'this' with the specific noun it refers to.",
                        "offset": token.idx,
                        "length": len(token.text),
                    })
    
    return issues


def check_false_friends(doc):
    """Detect false friends (GR-5).
    
    GR-5: "False friends"
    
    A false friend is a word that looks the same as one in a person's native language
    but has a different meaning in English.
    """
    issues = []
    seen = set()
    
    for token in doc:
        word = token.text.lower()
        if word in FALSE_FRIENDS:
            if token.idx not in seen:
                seen.add(token.idx)
                replacement = FALSE_FRIENDS[word]
                issues.append({
                    "type": "FalseFriends",
                    "message": f"Word '{word}' may be a false friend. Consider using '{replacement}' instead.",
                    "offset": token.idx,
                    "length": len(token.text),
                })
    
    return issues


def check_latin_abbreviations(doc):
    """Detect Latin abbreviations (GR-6).
    
    GR-6: "STE recommends that you do not use Latin abbreviations because they can
    confuse your readers if they do not know them. Always use English words to make
    the text clear."
    
    Common Latin abbreviations to detect:
    - e.g. → for example
    - i.e. → that is
    - etc. → and so on
    - viz. → namely
    - ibid. → in the same place
    - op. cit. → work cited
    - vol. → volume
    - vs. → versus
    """
    issues = []
    seen = set()
    
    for token in doc:
        if token.text.lower() in LATIN_ABBREVIATIONS:
            if token.idx not in seen:
                seen.add(token.idx)
                replacement = LATIN_ABBREVIATIONS[token.text.lower()]
                issues.append({
                    "type": "LatinAbbreviations",
                    "message": f"Do not use Latin abbreviation '{token.text}'. Use '{replacement}' instead.",
                    "offset": token.idx,
                    "length": len(token.text),
                })
    
    return issues


def check_gender_pronouns(doc):
    """Detect gender-specific pronouns (GR-7).
    
    GR-7: "STE does not include examples of inclusive language, but it fully complies
    with gender-neutral language requirements. When you write in STE, make sure that
    you always use gender-neutral language. Gender-specific pronouns, for example
    'he' or 'she' are not permitted in STE."
    
    Uses spaCy pronoun detection with gender features.
    """
    issues = []
    seen = set()
    
    for token in doc:
        # Check if token is a personal pronoun with gender features
        if token.pos_ == "PRON" and token.tag_ in ("PRP", "PRP$"):
            # Check morphological features for gender
            morph = token.morph
            if "Gender=Masc" in morph or "Gender=Fem" in morph:
                if token.idx not in seen:
                    seen.add(token.idx)
                    # Get recommended replacement from glossary
                    word_lower = token.text.lower()
                    if word_lower in GENDER_PRONOUNS:
                        replacement = GENDER_PRONOUNS[word_lower]
                    else:
                        # Default gender-neutral replacements
                        if "Masc" in morph:
                            replacement = "they"
                        elif "Fem" in morph:
                            replacement = "they"
                        else:
                            replacement = "they"
                    
                    issues.append({
                        "type": "GenderPronouns",
                        "message": f"Do not use gender-specific pronoun '{token.text}'. Use '{replacement}' instead.",
                        "offset": token.idx,
                        "length": len(token.text),
                    })
    
    return issues


def check_possessive_form(doc):
    """Check possessive form (GR-8).
    
    GR-8: "Possessive form"
    
    The possessive form adds an apostrophe and "s" to form the possessive.
    While permitted in STE, use it correctly. If not sure, do not use it.
    """
    issues = []
    seen = set()
    
    for token in doc:
        # Check for possessive markers
        if token.text in ("'s", "'"):
            # Check if it's a possessive (case dependency)
            if token.dep_ == "case" and token.tag_ == "POS":
                # Find the possessor (the noun before the possessive)
                if token.i > 0:
                    possessor = doc[token.i - 1]
                    if possessor.pos_ in ("PROPN", "NOUN"):
                        key = possessor.idx
                        if key not in seen:
                            seen.add(key)
                            if possessor.text.islower():
                                msg = f"Use possessive form carefully. Consider: '{possessor.text} of ...' instead of '{possessor.text}'s'."
                            else:
                                msg = f"Use possessive form carefully. Consider rewording instead of '{possessor.text}'s'."
                            
                            issues.append({
                                "type": "PossessiveForm",
                                "message": msg,
                                "offset": possessor.idx,
                                "length": len(possessor.text) + 2,  # +2 for 's
                            })
    
    return issues
