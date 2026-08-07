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
from constants import FALSE_FRIENDS, LATIN_ABBREVIATIONS, GENDER_PRONOUNS


def check_conjunction_that(doc):
    """Check for missing conjunction "that" (GR-1).
    
    GR-1: "The conjunction 'that'"
    
    Use the conjunction "that" after verbs like "make sure," "show," and "recommend"
    to prevent ambiguity.
    """
    issues = []
    text = doc.text
    
    # Check for common patterns where "that" is missing
    patterns = [
        (r"make sure\s+(?!that\b)", "make sure that"),
        (r"show\s+(?!that\b)", "show that"),
        (r"recommend\s+(?!that\b)", "recommend that"),
        (r"tell\s+(?!that\b)", "tell that"),
        (r"state\s+(?!that\b)", "state that"),
        (r"indicate\s+(?!that\b)", "indicate that"),
        (r"confirm\s+(?!that\b)", "confirm that"),
        (r"verify\s+(?!that\b)", "verify that"),
        (r"explain\s+(?!that\b)", "explain that"),
        (r"note\s+(?!that\b)", "note that"),
    ]
    
    for pattern, replacement in patterns:
        matches = list(re.finditer(pattern, text, re.IGNORECASE))
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
    
    This function detects common patterns where 'with' causes ambiguity.
    """
    issues = []
    seen = set()
    
    # Common patterns where 'with' causes ambiguity
    patterns = [
        # "with" followed by noun that could be instrument or association
        (r'(install|attach|connect|put|set)\s+.*\s+with\s+(\w+\s+\w+)', 
         "Check for ambiguity. 'with' can mean 'association', 'help', or 'instrument'."),
        # "with" followed by noun that could be instrument or condition
        (r'(operate|use|run|test)\s+.*\s+with\s+(\w+\s+\w+)', 
         "Check for ambiguity. 'with' can mean 'association', 'help', or 'instrument'."),
        # "with" followed by noun that could be instrument or condition
        (r'(make sure|verify|check|test)\s+.*\s+with\s+(\w+\s+\w+)', 
         "Check for ambiguity. 'with' can mean 'association', 'help', or 'instrument'."),
    ]
    
    text = doc.text
    for pattern, message in patterns:
        matches = list(re.finditer(pattern, text, re.IGNORECASE))
        for match in matches:
            if match.start() not in seen:
                seen.add(match.start())
                issues.append({
                    "type": "AmbiguousWith",
                    "message": message,
                    "offset": match.start(),
                    "length": len(match.group(0)),
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
    
    This function detects common patterns where pronouns cause ambiguity.
    """
    issues = []
    seen = set()
    
    # Common patterns where pronouns cause ambiguity
    patterns = [
        # "they" could refer to multiple nouns
        (r'(pins|bolts|nuts|washers|seals|valves|switches|buttons|indicators|gauges|tools|equipment)\s+.*\s+they\s+can\s+', 
         "Replace 'they' with the specific noun it refers to."),
        # "it" could refer to multiple nouns
        (r'(cover|panel|unit|system|component|part|assembly|device|instrument|tool|equipment)\s+.*\s+it\s+can\s+', 
         "Replace 'it' with the specific noun it refers to."),
        # "this" could refer to multiple nouns
        (r'(cover|panel|unit|system|component|part|assembly|device|instrument|tool|equipment)\s+.*\s+this\s+can\s+', 
         "Replace 'this' with the specific noun it refers to."),
    ]
    
    text = doc.text
    for pattern, message in patterns:
        matches = list(re.finditer(pattern, text, re.IGNORECASE))
        for match in matches:
            if match.start() not in seen:
                seen.add(match.start())
                issues.append({
                    "type": "AmbiguousPronouns",
                    "message": message,
                    "offset": match.start(),
                    "length": len(match.group(0)),
                })
    
    return issues


def check_ambiguous_this(doc):
    """Check for ambiguous 'this' pronoun usage (GR-4).
    
    GR-4: "The pronoun 'this'"
    
    When you use the pronoun 'this' in a sentence, make sure that the reader 
    knows the item the pronoun refers to. If 'this' can refer to more than one 
    item, give the applicable context again.
    
    This function detects common patterns where 'this' causes ambiguity.
    """
    issues = []
    seen = set()
    
    # Common patterns where 'this' causes ambiguity
    patterns = [
        # "this" followed by "can cause" or "may cause"
        (r'this\s+can\s+cause', 
         "Replace 'this' with the specific noun it refers to."),
        (r'this\s+may\s+cause', 
         "Replace 'this' with the specific noun it refers to."),
        (r'this\s+will\s+cause', 
         "Replace 'this' with the specific noun it refers to."),
        # "this" followed by "is" or "are"
        (r'this\s+is', 
         "Replace 'this' with the specific noun it refers to."),
        (r'this\s+are', 
         "Replace 'this' with the specific noun it refers to."),
    ]
    
    text = doc.text
    for pattern, message in patterns:
        matches = list(re.finditer(pattern, text, re.IGNORECASE))
        for match in matches:
            if match.start() not in seen:
                seen.add(match.start())
                issues.append({
                    "type": "AmbiguousThis",
                    "message": message,
                    "offset": match.start(),
                    "length": len(match.group(0)),
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
    """
    issues = []
    seen = set()
    
    for token in doc:
        if token.text.lower() in GENDER_PRONOUNS:
            # Check if it's being used as a pronoun (not as part of a compound noun)
            if token.pos_ in ("PRON", "NOUN"):
                if token.idx not in seen:
                    seen.add(token.idx)
                    replacement = GENDER_PRONOUNS[token.text.lower()]
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
