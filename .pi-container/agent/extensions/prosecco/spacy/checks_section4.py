#!/usr/bin/env python3
"""
ASD-STE100 Section 4 Checks (Sentences)

Summary of the rules

Short sentences and clear sentence structures
Rule 4.1: Write short and clear sentences.
Rule 4.2: Do not omit words or use contractions to make your sentences shorter.

Vertical lists
Rule 4.3: Use a vertical list for complex texts.

Connecting words and connecting phrases
Rule 4.4: Use connecting words and connecting phrases to connect sentences that contain related topics.

Articles and demonstrative adjectives
Rule 4.5: When applicable, use an article (the, a, an) or a demonstrative adjective (this, these) before a noun or a multi-word noun.
"""
import re
import spacy
from constants import (
    CONTRACTIONS,
    FORBIDDEN_MODALS,
    CONNECTING_WORDS,
)
from shared import (
    _get_sentence_depth,
    _get_token_depth,
    _get_first_content_word,
    _has_connecting_word,
    _are_sentences_related,
    _is_common_pattern_without_article,
)


def check_short_sentences(doc):
    """Check for short and clear sentences (Rule 4.1).
    
    Rule 4.1: "Write short and clear sentences."
    
    Write short and clear sentences. Short sentences are easier to understand 
    than long sentences. A sentence should have only one idea.
    
    Uses spaCy features:
    - doc.sents for sentence segmentation
    - token.pos_ to count words (excluding punctuation)
    - token.dep_ to check sentence structure complexity
    - Dependency parsing to identify complex constructions
    - token.head to check sentence depth
    """
    issues = []
    
    for sent in doc.sents:
        # Count words in the sentence (excluding punctuation)
        word_count = sum(1 for t in sent if t.pos_ != "PUNCT")
        
        # Check for sentences with too many words (complex sentences)
        if word_count > 20:
            # Calculate sentence complexity based on dependency depth
            max_depth = _get_sentence_depth(sent)
            
            # Flag sentences that are both long and complex
            if max_depth > 3 or word_count > 25:
                issues.append({
                    "type": "ShortSentences",
                    "message": f"Keep sentences short and clear. This sentence has {word_count} words and a dependency depth of {max_depth}. Break it into shorter sentences.",
                    "offset": sent.start_char,
                    "length": len(sent.text),
                })
    
    return issues


def check_contractions(doc):
    """Detect contractions (Rule 4.2).
    
    Rule 4.2: "Do not omit words or use contractions to make your sentences shorter."
    
    Examples: "don't", "isn't", "aren't"
    
    Uses spaCy features:
    - token.text to identify contraction parts
    - token.idx for offset calculation
    - CONTRACTIONS constant for full form lookup
    """
    issues = []
    
    for i, token in enumerate(doc):
        if token.text == "n't" and i > 0:
            prev = doc[i - 1]
            contraction = prev.text + "n't"
            if contraction.lower() in CONTRACTIONS:
                issues.append({
                    "type": "Contractions",
                    "message": f"Do not use '{contraction}'. Write the full form.",
                    "offset": prev.idx,
                    "length": len(contraction),
                })
    
    return issues


def check_vertical_lists(doc):
    """Check for proper use of vertical lists (Rule 4.3).
    
    Rule 4.3: "Use a vertical list for complex texts."
    
    When your sentence is long and you must include many different items
    (for example, a list of components, parts, or documents) or actions,
    you can put them in a vertical list.
    
    Uses spaCy features:
    - doc.text for line-by-line analysis
    - token.dep_ to check for list-like structures
    - Dependency parsing to identify enumeration patterns
    """
    issues = []
    seen = set()
    
    # Check for sentences with many items (likely should be a vertical list)
    for sent in doc.sents:
        # Count conjunctions (and, or) which indicate multiple items
        conjunction_count = sum(1 for t in sent if t.text.lower() in ("and", "or"))
        
        # Count nouns in the sentence
        noun_count = sum(1 for t in sent if t.pos_ == "NOUN")
        
        # If a sentence has many items, suggest a vertical list
        if conjunction_count >= 2 and noun_count >= 4:
            if sent.start_char not in seen:
                seen.add(sent.start_char)
                issues.append({
                    "type": "VerticalLists",
                    "message": f"This sentence has {conjunction_count} conjunctions and {noun_count} nouns. Consider using a vertical list instead.",
                    "offset": sent.start_char,
                    "length": len(sent.text),
                })
    
    return issues


def check_connecting_words(doc):
    """Check for missing connecting words and phrases (Rule 4.4).
    
    Rule 4.4: "Use connecting words and connecting phrases to connect sentences 
    that contain related topics."
    
    Connecting words and connecting phrases connect a topic in one sentence with 
    an idea in a sentence that follows. In a descriptive text, connecting words 
    and connecting phrases give your writing a logical structure and give 
    information that is easy to understand.
    
    Uses spaCy features:
    - doc.sents for sentence segmentation
    - token.dep_ to check for connecting words
    - token.lemma_ for base form comparison
    - token.is_stop for common connecting words
    - Dependency parsing to identify related topics
    """
    issues = []
    seen = set()
    
    sentences = list(doc.sents)
    
    # Check consecutive sentences for missing connecting words
    for i in range(len(sentences) - 1):
        sent1 = sentences[i]
        sent2 = sentences[i + 1]
        
        # Get the first content word of each sentence
        sent1_first_content = _get_first_content_word(sent1)
        sent2_first_content = _get_first_content_word(sent2)
        
        if not sent1_first_content or not sent2_first_content:
            continue
        
        # Check if the second sentence starts with a connecting word
        has_connecting_word = _has_connecting_word(sent2, CONNECTING_WORDS)
        
        # Check if the sentences are related (same topic)
        are_related = _are_sentences_related(sent1, sent2)
        
        # If sentences are related but don't have a connecting word, flag it
        if are_related and not has_connecting_word and sent1.start_char not in seen:
            seen.add(sent1.start_char)
            issues.append({
                "type": "ConnectingWords",
                "message": f"Consider adding a connecting word or phrase between these related sentences.",
                "offset": sent1.end_char,
                "length": len(sent1.text) + len(sent2.text),
            })
    
    return issues


def check_missing_articles(doc):
    """Check for missing articles before nouns (Rule 4.5).
    
    Rule 4.5: "When applicable, use an article (the, a, an) or a demonstrative 
    adjective (this, these) before a noun or a multi-word noun."
    
    Articles and demonstrative adjectives show the position of nouns and 
    multi-word nouns in the sentence. Use articles and demonstrative adjectives 
    correctly and do not omit them to make the text shorter.
    
    Uses spaCy features:
    - token.pos_ to identify nouns (NOUN, PROPN)
    - token.dep_ to check for determiners (det)
    - Dependency parsing to verify article usage
    - token.head to check noun relationships
    """
    issues = []
    seen = set()
    
    for token in doc:
        # Check for nouns that don't have a determiner
        if token.pos_ == "NOUN" and token.dep_ != "compound":
            # Check if the noun has a determiner (article or demonstrative)
            has_determiner = any(
                c.dep_ == "det" and c.pos_ == "DET"
                for c in token.children
            )
            
            # Skip proper nouns (these don't need articles)
            if token.pos_ == "PROPN":
                continue
            
            # Skip if the noun is part of a technical noun
            if token.dep_ in ("compound", "nn", "appos"):
                continue
            
            # Skip if the noun is in a common pattern that doesn't need articles
            if _is_common_pattern_without_article(token, doc):
                continue
            
            # If the noun doesn't have a determiner and is not a proper noun, flag it
            if not has_determiner and token.idx not in seen:
                seen.add(token.idx)
                issues.append({
                    "type": "MissingArticles",
                    "message": f"Add article or demonstrative adjective before '{token.text}'. Use 'the', 'a', 'an', 'this', or 'these' before the noun.",
                    "offset": token.idx,
                    "length": len(token.text),
                })
    
    return issues


def check_article_usage(doc):
    """Check for incorrect article usage (Rule 4.5).
    
    Rule 4.5: "When applicable, use an article (the, a, an) or a demonstrative 
    adjective (this, these) before a noun or a multi-word noun."
    
    This function checks for incorrect article usage (using "a" instead of "an",
    or using the wrong demonstrative adjective).
    
    Uses spaCy features:
    - token.pos_ to identify determiners (DET)
    - token.dep_ to check determiner relationships
    - token.text to check article form
    - Dependency parsing to verify article-noun relationships
    """
    issues = []
    seen = set()
    
    for token in doc:
        # Check for determiners (articles and demonstratives)
        if token.pos_ == "DET":
            # Check for incorrect "a" vs "an" usage
            if token.text.lower() == "a":
                # Check the next word
                if token.i + 1 < len(doc):
                    next_token = doc[token.i + 1]
                    if next_token.pos_ == "NOUN" or next_token.pos_ == "ADJ":
                        # Check if the next word starts with a vowel sound
                        if next_token.text.lower().startswith(("a", "e", "i", "o", "u")):
                            if token.idx not in seen:
                                seen.add(token.idx)
                                issues.append({
                                    "type": "ArticleUsage",
                                    "message": f"Use 'an' instead of 'a' before '{next_token.text}'.",
                                    "offset": token.idx,
                                    "length": len(token.text),
                                })
            
            # Check for incorrect demonstrative adjective usage
            elif token.text.lower() in ("this", "that", "these", "those"):
                # Check the noun it modifies
                if token.dep_ == "det":
                    head = token.head
                    if head.pos_ == "NOUN":
                        # Check if singular/plural matches
                        if token.text.lower() in ("this", "that"):
                            # Singular demonstrative
                            if head.tag_ == "NNS":  # Plural noun
                                if token.idx not in seen:
                                    seen.add(token.idx)
                                    issues.append({
                                        "type": "ArticleUsage",
                                        "message": f"Use '{token.text.replace('this', 'these').replace('that', 'those')}' instead of '{token.text}' for plural noun '{head.text}'.",
                                        "offset": token.idx,
                                        "length": len(token.text),
                                    })
                        elif token.text.lower() in ("these", "those"):
                            # Plural demonstrative
                            if head.tag_ == "NN" and not head.tag_ == "NNS":  # Singular noun
                                if token.idx not in seen:
                                    seen.add(token.idx)
                                    issues.append({
                                        "type": "ArticleUsage",
                                        "message": f"Use '{token.text.replace('these', 'this').replace('those', 'that')}' instead of '{token.text}' for singular noun '{head.text}'.",
                                        "offset": token.idx,
                                        "length": len(token.text),
                                    })
    
    return issues


def check_sentence_length(doc):
    """Check sentence length using spaCy sentence segmentation.
    
    Rule 5.1 (procedures): max 20 words per sentence.
    Rule 6.3 (descriptive): max 25 words per sentence.
    
    We use the stricter limit (20 words) as the default check.
    
    Uses spaCy features:
    - doc.sents for sentence segmentation
    - token.pos_ to count words (excluding punctuation)
    - token.dep_ to verify sentence boundaries
    """
    issues = []
    for sent in doc.sents:
        word_count = sum(1 for t in sent if t.pos_ != "PUNCT")
        if word_count > 20:
            issues.append({
                "type": "SentenceLength",
                "message": f"Keep sentences short. This sentence has {word_count} words. Aim for 20 words or fewer.",
                "offset": sent.start_char,
                "length": len(sent.text),
            })
    return issues


def check_forbidden_modals(doc):
    """Detect forbidden modal verbs (shall, should, may).
    
    List of recurring errors:
    - shall → must
    - should → must
    - may → can
    
    Uses spaCy features:
    - token.pos_ to identify auxiliary verbs (AUX)
    - token.tag_ for coarse POS tags (MD)
    - token.dep_ for dependency relationships
    - token.children to check for negation
    - FORBIDDEN_MODALS constant for replacement lookup
    """
    issues = []
    seen = set()
    
    for token in doc:
        if token.text.lower() in FORBIDDEN_MODALS:
            # Must be a modal auxiliary
            if token.pos_ == "AUX" and token.tag_ == "MD":
                # Skip if negated (e.g., "may not", "should not")
                has_neg = any(c.dep_ == "neg" for c in token.children)
                if not has_neg and token.idx not in seen:
                    seen.add(token.idx)
                    rep = FORBIDDEN_MODALS[token.text.lower()]
                    issues.append({
                        "type": "Shall",
                        "message": f"Use '{rep}' instead of '{token.text}'",
                        "offset": token.idx,
                        "length": len(token.text),
                    })
    
    return issues
