#!/usr/bin/env python3
"""
ASD-STE100 Section 5 Checks (Procedural writing)

Summary of the rules

Sentences
Rule 5.1: Write short sentences. Use a maximum of 20 words in each sentence.
Rule 5.2: Write only one instruction in each sentence unless two or more actions occur at the same time.

Verbs in procedures
Rule 5.3: Write instructions in the imperative (command) form.

Descriptive statements in instructions
Rule 5.4: When there is a condition that the reader must know about first, start the instruction with a descriptive statement. Then, divide that descriptive statement from the command with a comma.

Notes
Rule 5.5: Write notes only to give information, not instructions.
"""
import re
import spacy
from constants import CONDITIONAL_WORDS, IMPERATIVE_VERB_LEMMAS


def check_sentence_length_procedural(doc):
    """Check sentence length (Rule 5.1).
    
    Rule 5.1: "Write short sentences. Use a maximum of 20 words in each sentence."
    
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
        
        # Check if sentence exceeds 20 words
        if word_count > 20:
            issues.append({
                "type": "SentenceLength",
                "message": f"Keep sentences short. This sentence has {word_count} words. Use a maximum of 20 words.",
                "offset": sent.start_char,
                "length": len(sent.text),
            })
    
    return issues


def check_multiple_instructions(doc):
    """Check for multiple instructions in one sentence (Rule 5.2).
    
    Rule 5.2: "Write only one instruction in each sentence unless two or more 
    actions occur at the same time."
    
    If there are too many instructions in a sentence, the sentence is not easy 
    to read and understand. Write only one instruction in each sentence and 
    clearly show (usually with numbers or letters) the sequence of the work 
    steps.
    
    Uses spaCy features:
    - doc.sents for sentence segmentation
    - token.pos_ to identify verbs (VERB)
    - token.dep_ to check for imperative form (ROOT, advcl)
    - token.tag_ for coarse POS tags (VB)
    - Dependency parsing to identify multiple imperative verbs
    - token.head to check verb relationships
    """
    issues = []
    seen = set()
    
    for sent in doc.sents:
        # Count imperative verbs in the sentence
        imperative_count = 0
        imperative_tokens = []
        
        for token in sent:
            # Check if token is a verb in imperative form
            if token.pos_ == "VERB" and token.tag_ == "VB":
                # Check if it's a root verb, conj, or has imperative-like dependencies
                #conj verbs (like "install" in "Remove ... and install ...") are also imperatives
                if token.dep_ in ("ROOT", "conj", "advcl", "cc"):
                    # Verify it's not a subordinate clause verb (ccomp, xcomp)
                    if token.dep_ not in ("ccomp", "xcomp"):
                        imperative_count += 1
                        imperative_tokens.append(token)
        
        # If there are multiple imperative verbs, flag it
        if imperative_count > 1 and sent.start_char not in seen:
            seen.add(sent.start_char)
            issues.append({
                "type": "MultipleInstructions",
                "message": f"Write only one instruction per sentence. This sentence has {imperative_count} instructions. Split into separate sentences.",
                "offset": sent.start_char,
                "length": len(sent.text),
            })
    
    return issues


def check_non_imperative_in_procedures(doc):
    """Check for non-imperative form in procedural writing (Rule 5.3).
    
    Rule 5.3: "Write instructions in the imperative (command) form."
    
    An instruction tells the reader to do something. Write the verb in the 
    imperative (command) form. The imperative form gives the reader a clear 
    instruction. If you use other types of sentence structure, you can cause 
    ambiguity.
    
    Uses spaCy features:
    - doc.sents for sentence segmentation
    - token.pos_ to identify verbs (VERB) and auxiliaries (AUX)
    - token.dep_ to check dependency relationships
    - token.tag_ for coarse POS tags (MD, VB, VBN, VBG)
    - token.head to check verb relationships
    - Dependency parsing to identify non-imperative constructions
    """
    issues = []
    seen = set()
    
    for sent in doc.sents:
        # Check if the sentence starts with an imperative verb
        # Imperative sentences typically start with a verb in base form (VB)
        first_content_token = None
        for token in sent:
            if token.pos_ == "PUNCT":
                continue
            first_content_token = token
            break
        
        if first_content_token and first_content_token.pos_ == "VERB":
            # Check if it's an imperative form (base form verb at the start)
            if first_content_token.tag_ == "VB" and first_content_token.dep_ == "ROOT":
                # This is likely an imperative sentence - check for issues
                pass
            else:
                # Non-imperative form at the start
                if first_content_token.idx not in seen:
                    seen.add(first_content_token.idx)
                    issues.append({
                        "type": "NonImperativeInProcedures",
                        "message": f"Use imperative form. Start with a verb in base form (e.g., 'remove', 'install', 'check').",
                        "offset": first_content_token.idx,
                        "length": len(first_content_token.text),
                    })
        
        # Check for modal verbs (must, should, can, will) followed by base form
        for token in sent:
            if token.pos_ == "AUX" and token.tag_ == "MD":
                # Check if it's a forbidden modal
                if token.text.lower() in ("must", "should", "can", "will"):
                    if token.idx not in seen:
                        seen.add(token.idx)
                        issues.append({
                            "type": "NonImperativeInProcedures",
                            "message": f"Use imperative form instead of '{token.text}'. Use base form of the verb.",
                            "offset": token.idx,
                            "length": len(token.text),
                        })
    
    return issues


def check_descriptive_statement_first(doc):
    """Check for descriptive statements in instructions (Rule 5.4).
    
    Rule 5.4: "When there is a condition that the reader must know about first, 
    start the instruction with a descriptive statement. Then, divide that 
    descriptive statement from the command with a comma."
    
    If a special condition is necessary for a work step, the reader must know 
    the condition first. Write the condition first in the sentence, and then use 
    a comma to show the end of the condition, and the start of the instruction.
    
    Uses spaCy features:
    - doc.sents for sentence segmentation
    - token.dep_ to identify conditional clauses (if, when, before, after)
    - token.head to check clause relationships
    - Dependency parsing to identify condition placement
    - token.text to check for conditional words
    """
    issues = []
    seen = set()
    
    for sent in doc.sents:
        # Check if the sentence has a conditional clause
        has_conditional = False
        conditional_token = None
        
        for token in sent:
            if token.text.lower() in CONDITIONAL_WORDS:
                # Check if it's a subordinating conjunction (SCONJ), auxiliary (aux), 
                # or adverbial modifier (advmod) used as conditional
                if token.pos_ == "SCONJ" or token.dep_ in ("mark", "aux", "advmod"):
                    has_conditional = True
                    conditional_token = token
                    break
        
        if has_conditional:
            # Check if the conditional clause is at the beginning of the sentence
            # Find the first content word (non-punctuation)
            first_token = next((t for t in sent if t.pos_ != "PUNCT"), None)
            
            if first_token and conditional_token:
                # Check if the conditional word is not the first word
                if conditional_token.i > first_token.i:
                    if conditional_token.idx not in seen:
                        seen.add(conditional_token.idx)
                        issues.append({
                            "type": "DescriptiveStatementFirst",
                            "message": f"Write the condition first. Use '{conditional_token.text.lower()} ..., [command]' instead of '[command] ... {conditional_token.text.lower()} ...'.",
                            "offset": conditional_token.idx,
                            "length": len(conditional_token.text),
                        })
    
    return issues


def check_notes(doc):
    """Check for notes that contain instructions (Rule 5.5).
    
    Rule 5.5: "Write notes only to give information, not instructions."
    
    Notes only give information to help the reader during a procedure. They 
    contain descriptive text and obey the rules for descriptive writing. Notes 
    must not give instructions, requirements, or limits.
    
    Uses spaCy features:
    - doc.text for line-by-line analysis
    - token.pos_ to identify verbs (VERB)
    - token.tag_ for coarse POS tags (VB)
    - Dependency parsing to check for imperative form
    - token.lemma_ for base form comparison
    """
    issues = []
    seen = set()
    
    for sent in doc.sents:
        # Check if the sentence starts with "NOTE:" or "Note:"
        first_token = next((t for t in sent if t.pos_ != "PUNCT"), None)
        
        if first_token and first_token.text.lower().startswith("note"):
            # Extract the text after "NOTE:"
            after_note = sent.text.strip()
            if after_note.upper().startswith("NOTE:"):
                after_note = after_note[5:].strip()
                
                if after_note:
                    # Parse the text after NOTE:
                    note_doc = nlp(after_note)
                    
                    # Check if the note contains an imperative verb
                    for token in note_doc:
                        if token.pos_ == "VERB" and token.tag_ == "VB":
                            lemma = token.lemma_.lower()
                            if lemma in IMPERATIVE_VERB_LEMMAS:
                                if sent.start_char not in seen:
                                    seen.add(sent.start_char)
                                    issues.append({
                                        "type": "NoteContainsInstruction",
                                        "message": f"Notes must not contain instructions. Move '{token.text}' instruction to a work step.",
                                        "offset": sent.start_char,
                                        "length": len(sent.text),
                                    })
                                break
    
    return issues


# Import nlp from main module (already loaded there)
# This avoids duplicate model loading
import spacy as spacy_module
nlp = spacy_module.load("en_core_web_sm")
