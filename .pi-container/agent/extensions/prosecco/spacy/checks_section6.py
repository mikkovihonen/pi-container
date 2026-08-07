#!/usr/bin/env python3
"""
ASD-STE100 Section 6 Checks (Descriptive writing)

Summary of the rules

Content structure
Rule 6.1: Give information gradually.
Rule 6.2: Use key words and key phrases to give your text a logical structure.

Sentences
Rule 6.3: Write short sentences. Use a maximum of 25 words in each sentence.

Paragraphs
Rule 6.4: Use paragraphs to show related information.
Rule 6.5: Make sure that each paragraph has only one topic.
Rule 6.6: Make sure that no paragraph has more than six sentences.
"""
import re
import spacy
from glossary import COMMON_DETERMINERS


def check_information_structure(doc):
    """Check for gradual information structure (Rule 6.1).
    
    Rule 6.1: "Give information gradually."
    
    Descriptive writing gives information, not instructions. Thus, the imperative
    form of the verb is not permitted.
    
    In a descriptive text, give information gradually and make sure that each
    sentence contains only one subject.
    
    Uses spaCy features:
    - doc.sents for sentence segmentation
    - token.pos_ to identify verbs (VERB)
    - token.dep_ to check for imperative form (ROOT without subject)
    - token.tag_ for coarse POS tags (VB, VBP)
    - Dependency parsing to verify sentence structure
    """
    issues = []
    seen = set()
    
    for sent in doc.sents:
        # Check if the sentence contains an imperative verb
        for token in sent:
            # Check for imperative form (command form)
            # Imperative verbs are typically at the beginning of a sentence and have no subject
            if token.pos_ == "VERB" and token.dep_ == "ROOT":
                # Check if the verb is in imperative form
                # Imperative verbs are typically base form (VB) or present tense (VBP) without a subject
                has_subject = any(c.dep_ == "nsubj" for c in token.children)
                if not has_subject and token.tag_ in ("VB", "VBP"):
                    if token.idx not in seen:
                        seen.add(token.idx)
                        issues.append({
                            "type": "ImperativeInDescription",
                            "message": "Do not use imperative form in descriptive writing. Use descriptive sentences instead.",
                            "offset": token.idx,
                            "length": len(token.text),
                        })
    
    return issues


def check_key_words(doc):
    """Check for key words and key phrases consistency (Rule 6.2).
    
    Rule 6.2: "Use key words and key phrases to give your text a logical structure."
    
    Key words are words that occur in a text to connect different ideas, and key 
    phrases are phrases that have the same function. These key words and key 
    phrases show how information in a text is related.
    
    Uses spaCy features:
    - doc.sents for sentence segmentation
    - token.lemma_ for base form comparison
    - doc.noun_chunks for noun phrase identification
    - token.pos_ to identify key nouns and nouns
    - Dependency parsing to track terminology throughout document
    """
    issues = []
    seen = set()
    
    # Track key terms and their usage throughout the document
    key_terms = {}
    
    for sent in doc.sents:
        # Extract key terms from each sentence
        for token in sent:
            # Check for important nouns and noun chunks
            if token.pos_ == "NOUN" and not token.is_stop:
                lemma = token.lemma_.lower()
                
                # Skip common determiners
                if lemma in COMMON_DETERMINERS:
                    continue
                
                # Track term frequency
                if lemma not in key_terms:
                    key_terms[lemma] = []
                key_terms[lemma].append(token)
    
    # Check for inconsistent terminology
    # This is a simplified check - a full implementation would compare
    # similar terms throughout the document
    for term, tokens in key_terms.items():
        # If a term appears multiple times, check for consistency
        if len(tokens) > 1:
            # Check if the term is used in different contexts
            contexts = set()
            for token in tokens:
                # Get surrounding context
                start = max(0, token.i - 2)
                end = min(len(token.sent), token.i + 3)
                context = ' '.join(t.lemma_ for t in token.sent[start:end] if t.pos_ != "PUNCT")
                contexts.add(context)
            
            # If the term is used in very different contexts, flag it
            if len(contexts) > 2 and tokens[0].idx not in seen:
                seen.add(tokens[0].idx)
                issues.append({
                    "type": "KeyWords",
                    "message": f"Term '{term}' is used in multiple different contexts. Consider using different terms for clarity.",
                    "offset": tokens[0].idx,
                    "length": len(tokens[0].text),
                })
    
    return issues


def check_sentence_length_descriptive(doc):
    """Check sentence length for descriptive writing (Rule 6.3).
    
    Rule 6.3: "Write short sentences. Use a maximum of 25 words in each sentence."
    
    Short sentences are easier to understand than long sentences. A sentence 
    should have only one idea. Write short and clear sentences.
    
    Uses spaCy features:
    - doc.sents for sentence segmentation
    - token.pos_ to count words (excluding punctuation)
    - token.dep_ to verify sentence boundaries
    """
    issues = []
    
    for sent in doc.sents:
        # Count words in the sentence (excluding punctuation)
        word_count = sum(1 for t in sent if t.pos_ != "PUNCT")
        
        # Check if sentence exceeds 25 words (descriptive limit)
        if word_count > 25:
            issues.append({
                "type": "SentenceLength",
                "message": f"Keep sentences short. This sentence has {word_count} words. Use a maximum of 25 words.",
                "offset": sent.start_char,
                "length": len(sent.text),
            })
    
    return issues


def check_paragraph_structure(doc):
    """Check for proper paragraph structure (Rule 6.4).
    
    Rule 6.4: "Use paragraphs to show related information."
    
    Paragraphs divide a text into logical units and help keep the reader's attention.
    Each paragraph should contain related information about one topic.
    
    Uses spaCy features:
    - doc.text for paragraph-by-paragraph analysis
    - doc.sents for sentence segmentation within paragraphs
    - token.lemma_ for topic identification
    - Dependency parsing to verify paragraph coherence
    """
    issues = []
    seen = set()
    
    # Split text into paragraphs
    paragraphs = doc.text.split('\n\n')
    
    for para_idx, para in enumerate(paragraphs):
        if not para.strip():
            continue
        
        # Calculate paragraph start position
        para_start = sum(len(p) + 2 for p in paragraphs[:para_idx])
        
        # Parse the paragraph
        para_doc = nlp(para)
        
        # Extract key topics from the paragraph
        topics = set()
        for sent in para_doc.sents:
            # Get the main topic of each sentence (subject or first content word)
            for token in sent:
                if token.pos_ == "NOUN" and not token.is_stop and token.dep_ in ("nsubj", "dobj", "pobj", "attr"):
                    topics.add(token.lemma_.lower())
                    break
        
        # If the paragraph has too many different topics, flag it
        if len(topics) > 3 and para_start not in seen:
            seen.add(para_start)
            issues.append({
                "type": "ParagraphStructure",
                "message": f"Paragraph contains {len(topics)} different topics. Use paragraphs to show related information.",
                "offset": para_start,
                "length": len(para),
            })
    
    return issues


def check_paragraph_topic(doc):
    """Check that each paragraph has only one topic (Rule 6.5).
    
    Rule 6.5: "Make sure that each paragraph has only one topic."
    
    Each paragraph should focus on one main idea or topic. If a paragraph 
    contains multiple unrelated topics, divide it into separate paragraphs.
    
    Uses spaCy features:
    - doc.sents for sentence segmentation
    - token.lemma_ for topic identification
    - Dependency parsing to identify main subjects
    - Noun chunk analysis for topic detection
    """
    issues = []
    seen = set()
    
    # Split text into paragraphs
    paragraphs = doc.text.split('\n\n')
    
    for para_idx, para in enumerate(paragraphs):
        if not para.strip():
            continue
        
        # Calculate paragraph start position
        para_start = sum(len(p) + 2 for p in paragraphs[:para_idx])
        
        # Parse the paragraph
        para_doc = nlp(para)
        
        # Count unique main subjects in the paragraph
        subjects = set()
        for sent in para_doc.sents:
            # Find the main subject of each sentence
            for token in sent:
                if token.dep_ == "nsubj" and token.pos_ == "NOUN":
                    subjects.add(token.lemma_.lower())
                elif token.dep_ == "ROOT" and token.pos_ == "VERB":
                    # Find subject of this verb
                    for child in token.children:
                        if child.dep_ == "nsubj" and child.pos_ == "NOUN":
                            subjects.add(child.lemma_.lower())
                            break
        
        # If the paragraph has more than 2 different main subjects, flag it
        if len(subjects) > 2 and para_start not in seen:
            seen.add(para_start)
            issues.append({
                "type": "ParagraphTopic",
                "message": f"Paragraph has {len(subjects)} different topics. Each paragraph should have only one topic.",
                "offset": para_start,
                "length": len(para),
            })
    
    return issues


def check_paragraph_length(doc):
    """Check paragraph length (Rule 6.6).
    
    Rule 6.6: "Make sure that no paragraph has more than six sentences."
    
    Paragraphs divide a text into logical units and help keep the reader's attention.
    If paragraphs are too long, they cannot have this function. Do not put different
    topics in the same paragraph. If a paragraph has more than six sentences, divide
    it into two smaller paragraphs.
    
    Uses spaCy features:
    - doc.text for paragraph splitting
    - doc.sents for sentence counting
    - token.pos_ for sentence boundary detection
    """
    issues = []
    
    # Split text into paragraphs
    paragraphs = doc.text.split('\n\n')
    
    for para_idx, para in enumerate(paragraphs):
        if not para.strip():
            continue
        
        # Count sentences in the paragraph
        para_doc = nlp(para)
        sentence_count = sum(1 for _ in para_doc.sents)
        
        if sentence_count > 6:
            # Find the start of the paragraph in the original text
            para_start = sum(len(p) + 2 for p in paragraphs[:para_idx])  # +2 for \n\n
            issues.append({
                "type": "ParagraphLength",
                "message": f"Paragraph has {sentence_count} sentences. Use no more than 6 sentences per paragraph.",
                "offset": para_start,
                "length": len(para),
            })
    
    return issues


# Load spaCy model for paragraph analysis
try:
    import spacy as spacy_module
    nlp = spacy_module.load("en_core_web_sm")
except:
    nlp = None
