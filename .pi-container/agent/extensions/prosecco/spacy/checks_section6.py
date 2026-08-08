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
import numpy as np
from glossary import COMMON_DETERMINERS

# Load sense2vec model once at module level for reuse
try:
    from sense2vec import Sense2Vec
    s2v_model = Sense2Vec().from_disk(".pi-container/agent/extensions/prosecco/spacy/s2v")
except Exception:
    s2v_model = None


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


def _get_dependency_signature(token):
    """Get a dependency signature for a token.

    Uses spaCy dependency parsing to create a signature based on:
    - POS tag (NOUN, VERB, etc.)
    - Dependency relation (nsubj, dobj, compound, etc.)
    - Head POS tag
    - Children dependency relations (sorted for consistency)

    Args:
        token: spaCy Token object

    Returns:
        tuple: Dependency signature
    """
    sig = [
        token.pos_,
        token.dep_,
        token.head.pos_,
        tuple(sorted([c.dep_ for c in token.children if c.dep_ not in ('det', 'punct', 'poss', 'amod', 'nummod', 'quantmod', 'nn')]))
    ]
    return tuple(sig)


def _get_noun_chunk_modifiers(token):
    """Get the modifier pattern for a noun token.

    Uses spaCy noun chunk analysis to extract the modifier pattern.

    Args:
        token: spaCy Token object (must be a NOUN)

    Returns:
        tuple: Modifier pattern (sorted list of modifier types)
    """
    if token.pos_ != "NOUN":
        return ()

    # Get the head of the noun chunk (if this token is part of a chunk)
    chunk_head = token
    for chunk in token.doc.noun_chunks:
        if token in chunk:
            chunk_head = chunk.root
            break

    # Return the modifier pattern of the chunk head
    chunk_modifiers = sorted([
        c.dep_ for c in chunk_head.children
        if c.dep_ not in ('det', 'punct', 'poss', 'amod', 'nummod', 'quantmod', 'nn')
    ])

    return tuple(chunk_modifiers)


def _get_wordnet_synset_count(lemma):
    """Get the number of WordNet synsets for a lemma.

    Uses nltk wordnet to determine how many distinct meanings a word has.
    Higher counts indicate more polysemous words.

    Args:
        lemma: str - word lemma to check

    Returns:
        int - number of synsets
    """
    try:
        import nltk
        from nltk.corpus import wordnet as wn
        return len(wn.synsets(lemma))
    except Exception:
        return 0


def _get_sense2vec_embedding(token):
    """Get the sense2vec embedding for a token.

    Uses sense2vec to get word sense embeddings that distinguish between
    different meanings of the same word.

    Args:
        token: spaCy Token object

    Returns:
        numpy array or None - embedding vector or None if not found
    """
    try:
        key = f"{token.lemma_.lower()}|{token.pos_.lower()}"
        if key in s2v_model:
            return s2v_model[key]
    except Exception:
        pass
    return None


def _is_polysemous(term, tokens):
    """Check if a term is used with different meanings (polysemous).

    Uses 5 detection methods:
    1. POS tagging consistency - different POS tags suggest different meanings
    2. Dependency role analysis - different syntactic roles suggest different meanings
    3. Collocation/phrase clustering - different noun phrase structures suggest different meanings
    4. Semantic role/argument structure - WordNet synset counts indicate polysemy
    5. Embedding similarity - sense2vec vectors distinguish word senses

    Args:
        term: str - lemma being checked
        tokens: list of spaCy Token objects

    Returns:
        bool - True if the term appears polysemous
    """
    if len(tokens) < 2:
        return False

    # Get POS tags for all occurrences
    pos_tags = [t.pos_ for t in tokens]
    unique_pos = set(pos_tags)

    # Method 1: POS tagging consistency
    # If the same lemma appears as different POS tags, it's polysemous
    if len(unique_pos) > 1:
        return True

    # Method 4: WordNet synset count
    # Words with many synsets are inherently polysemous
    # Only flag if the word has significantly more synsets than average
    # and appears in different syntactic contexts
    synset_count = _get_wordnet_synset_count(term)
    if synset_count > 20:  # Very high polysemy threshold
        dep_sigs = [_get_dependency_signature(t) for t in tokens]
        if len(set(dep_sigs)) > 2:  # Require multiple different contexts
            return True

    # Method 5: sense2vec embeddings
    # Different embeddings for the same lemma indicate different meanings
    if s2v_model is not None:
        embeddings = []
        for token in tokens:
            vec = _get_sense2vec_embedding(token)
            if vec is not None:
                embeddings.append(vec)

        if len(embeddings) >= 2:
            # Compare all pairs of embeddings
            # If any pair has low similarity, the term is polysemous
            for i in range(len(embeddings)):
                for j in range(i + 1, len(embeddings)):
                    vec1 = np.array(embeddings[i])
                    vec2 = np.array(embeddings[j])
                    # Cosine similarity
                    similarity = np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2))
                    if similarity < 0.7:  # Low similarity threshold
                        return True

    # Methods 2 & 3: Dependency and collocation analysis
    # These require more sophisticated analysis and are not yet implemented

    return False


def check_key_words(doc):
    """Check for key words and key phrases consistency (Rule 6.2).

    Rule 6.2: "Use key words and key phrases to give your text a logical structure."

    Key words are words that occur in a text to connect different ideas, and key
    phrases are phrases that have the same function. These key words and key phrases
    show how information in a text is related.

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
            # Check for important content tokens (nouns, adjectives, verbs)
            # We include ADJ and VERB to detect polysemy (e.g., "light" NOUN vs ADJ)
            # Skip proper nouns (PROPN) as they're names, not polysemous terms
            if token.pos_ in ("NOUN", "ADJ", "VERB") and token.pos_ != "PROPN" and not token.is_stop:
                lemma = token.lemma_.lower()

                # Skip common determiners
                if lemma in COMMON_DETERMINERS:
                    continue

                # Skip single-character symbols and non-alphabetic tokens
                # (SpaCy misparses markdown symbols like #, <, | as NOUN)
                if len(lemma) <= 1 or not lemma.isalpha():
                    continue

                # Track term frequency
                if lemma not in key_terms:
                    key_terms[lemma] = []
                key_terms[lemma].append(token)

    # Check for polysemous terms
    for term, tokens in key_terms.items():
        # If a term appears multiple times, check for polysemy
        if len(tokens) > 1:
            if _is_polysemous(term, tokens) and tokens[0].idx not in seen:
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
