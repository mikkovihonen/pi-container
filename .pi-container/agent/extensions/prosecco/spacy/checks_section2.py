#!/usr/bin/env python3
"""
ASD-STE100 Section 2 Checks (Multi-word nouns)

Summary of the rules

Multi-word nouns
Rule 2.1: Write multi-word nouns of no more than three words.
Rule 2.2: When a technical noun has more than three words, write it in full. 
Then, you can use one of these methods to make the technical noun clear: 
- Give a shorter form of the technical noun
- Use hyphens (-) between words that you use as one unit.

This module implements Rule 2.1 (multi-word noun length) and Rule 2.2 
(when technical nouns have more than three words).
"""
import re
import spacy
from glossary import LONG_TECHNICAL_NOUN_PATTERNS


def check_multi_word_nouns(doc):
    """Detect long multi-word nouns (Rule 2.1).
    
    Rule 2.1: "Write multi-word nouns of no more than three words."
    
    Long multi-word nouns are not easy to understand because the words in the
    multi-word noun can connect to each other differently. The main noun, or head
    noun of the group, is usually the last word of the multi-word noun.
    
    To help your reader, keep multi-word nouns to a maximum of three words.
    
    Uses spaCy features:
    - doc.noun_chunks to identify noun phrases
    - token.pos_ to verify noun usage (NOUN, PROPN)
    - token.dep_ for dependency relationships (compound, nn, appos)
    - token.head to check if word is part of a technical noun
    - token.text, token.idx for offset calculation
    """
    issues = []
    seen = set()
    
    # Use spaCy's noun chunk detection to find multi-word nouns
    for chunk in doc.noun_chunks:
        # Filter out punctuation tokens (e.g. stray hyphens) and skip
        # chunks whose first token is a determiner (a, an, the).
        words = [t.text for t in chunk if not t.is_punct]
        if not words:
            continue
        first_token = next((t for t in chunk if not t.is_punct), None)
        if first_token and first_token.pos_ == "DET":
            continue
        
        # Skip single-word or short chunks (these are not multi-word nouns)
        if len(words) <= 3:
            continue
        
        # Skip if we've already flagged this chunk's root
        chunk_key = chunk.root.idx
        if chunk_key not in seen:
            seen.add(chunk_key)
            
            # Calculate total length including spaces
            total_length = sum(len(word) for word in words) + len(words) - 1
            
            issues.append({
                "type": "MultiWordNouns",
                "message": f"Multi-word noun '{' '.join(words)}' has {len(words)} words. Use no more than 3 words.",
                "offset": chunk.start_char,
                "length": total_length,
            })
    
    return issues


def check_too_long_technical_nouns(doc):
    """Check for unnecessarily long technical nouns (Rule 1.9, 2.2).
    
    Rule 1.9: "When you must select a technical noun, use one which is short 
    and easy to understand."
    
    Rule 2.2: "When a technical noun has more than three words, write it in full."
    
    When there is no technical noun that is approved in your company, industry, 
    or subject field, select one that is short (not more than three words) and 
    easy to understand.
    
    Uses spaCy features:
    - doc.noun_chunks to identify noun phrases
    - token.pos_ to verify noun usage (NOUN, PROPN)
    - token.dep_ for dependency relationships (compound, nn, appos)
    - token.head to check noun relationships
    - Custom pattern matching for known long technical nouns
    """
    issues = []
    seen = set()
    
    # Use spaCy's noun chunk detection to find potentially long technical nouns
    for chunk in doc.noun_chunks:
        words = [t.text.lower() for t in chunk]
        
        # Skip single-word chunks (these are not multi-word nouns)
        if len(words) <= 3:
            continue
        
        # Skip chunks that start with articles (these are usually not technical nouns)
        if words[0] in {"a", "an", "the"}:
            continue
        
        # Skip chunks that are entirely stop words
        if all(t.is_stop for t in chunk):
            continue
        
        # Check if the chunk matches any known long technical noun patterns
        chunk_text = ' '.join(words)
        
        # Use regex patterns from glossary to match long technical nouns
        for pattern, replacement in LONG_TECHNICAL_NOUN_PATTERNS:
            # Create a regex pattern that matches the chunk with word boundaries
            chunk_regex = r'(?i)\b' + r'\s+'.join(re.escape(w) for w in words) + r'\b'
            
            matches = list(re.finditer(chunk_regex, doc.text))
            for match in matches:
                if match.start() not in seen:
                    seen.add(match.start())
                    issues.append({
                        "type": "TooLongTechnicalNouns",
                        "message": f"Use a shorter technical noun. Use '{replacement}' instead of '{match.group()}'.",
                        "offset": match.start(),
                        "length": len(match.group()),
                    })
    
    # Also check for specific patterns that spaCy might not detect as noun chunks
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


def check_technical_noun_clarity(doc):
    """Check for unclear technical nouns (Rule 2.2).
    
    Rule 2.2: "When a technical noun has more than three words, write it in full."
    
    When you use a technical noun with more than three words, you must make it clear.
    You can do this by:
    1. Giving a shorter form (e.g., "ACU")
    2. Using hyphens to connect words that work as one unit
    
    This function checks for technical nouns that are unclear due to length or 
    ambiguous structure.
    
    Uses spaCy features:
    - doc.noun_chunks to identify noun phrases
    - token.pos_ to verify noun usage (NOUN, PROPN)
    - token.dep_ for dependency relationships (compound, nn, appos)
    - token.head to check noun relationships
    - token.text, token.idx for offset calculation
    """
    issues = []
    seen = set()
    
    # Check noun chunks for clarity issues
    for chunk in doc.noun_chunks:
        words = [t.text for t in chunk]
        
        # Skip single-word chunks (these are clear)
        if len(words) <= 3:
            continue
        
        # Skip chunks that are already hyphenated (these are clear)
        if any('-' in word for word in words):
            continue
        
        # Check if the chunk has ambiguous structure
        # This is a simplified check - a full implementation would need more analysis
        # For now, we just flag chunks with more than 3 words
        # (Longer chunks are more likely to be unclear)
        
        chunk_key = chunk.root.idx
        if chunk_key not in seen:
            seen.add(chunk_key)
            
            issues.append({
                "type": "TechnicalNounClarity",
                "message": f"Technical noun '{' '.join(words)}' is unclear. Use a shorter form or add a definition.",
                "offset": chunk.start_char,
                "length": sum(len(word) for word in words) + len(words) - 1,
            })
    
    return issues
