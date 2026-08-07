#!/usr/bin/env python3
"""
ASD-STE100 Section 7 Checks (Safety instructions)

Summary of the rules

Definitions
A warning tells the reader that there is a risk of injury or death.
A caution tells the reader that there is a risk of damage to objects.

How to write safety instructions
Rule 7.1: Use an applicable word (for example, "warning" or "caution") to 
          identify the level of risk.
Rule 7.2: Start a safety instruction with a clear and accurate command or 
          condition.
Rule 7.3: Give an explanation to show the risk or possible result.
"""
import re
import spacy
from glossary import SAFETY_KEYWORDS, HIGH_RISK_SAFETY_KEYWORDS, RISK_INDICATORS
from shared import _get_paragraph_index


def check_safety_instruction_format(doc):
    """Check for proper safety instruction format (Rules 7.1-7.2).
    
    Rule 7.1: "Use an applicable word (for example, 'warning' or 'caution') to 
    identify the level of risk."
    
    Rule 7.2: "Start a safety instruction with a clear and accurate command or 
    condition."
    
    Safety instructions must stay within a single paragraph. A WARNING may not 
    span multiple paragraphs.
    
    Uses spaCy features:
    - doc.sents for sentence segmentation
    - token.pos_ to identify parts of speech
    - token.dep_ to check for imperative form
    - token.tag_ for coarse POS tags (VB)
    - Dependency parsing to verify command/condition structure
    """
    issues = []
    seen = set()
    
    # Get all sentences first
    sentences = list(doc.sents)
    
    for i, sent in enumerate(sentences):
        # Check if the sentence starts with a safety keyword
        first_token = next((t for t in sent if t.pos_ != "PUNCT"), None)
        
        if first_token and first_token.text.upper() in SAFETY_KEYWORDS:
            # Extract the full safety instruction (may span multiple sentences within the same paragraph)
            keyword = first_token.text.upper()
            instruction_text = sent.text.strip()
            current_para_idx = _get_paragraph_index(sent, doc)
            
            # Check subsequent sentences within the same paragraph
            # Stop when we hit: another safety keyword, NOTE, or a paragraph break
            j = i + 1
            while j < len(sentences):
                next_sent = sentences[j]
                next_para_idx = _get_paragraph_index(next_sent, doc)
                
                # Stop if we've crossed a paragraph boundary
                if next_para_idx != current_para_idx:
                    break
                
                next_first = next((t for t in next_sent if t.pos_ != "PUNCT"), None)
                
                # Stop if we hit another safety keyword or NOTE
                if next_first and next_first.text.upper() in SAFETY_KEYWORDS:
                    break
                
                instruction_text += " " + next_sent.text.strip()
                j += 1
            
            # Check if the instruction has content after the keyword
            after_label = instruction_text[len(keyword) + 1:].strip()
            
            # Rule 7.1: Check if the label is present (already verified above)
            # Rule 7.2: Check if there's a command or condition after the label
            if not after_label:
                if first_token.idx not in seen:
                    seen.add(first_token.idx)
                    issues.append({
                        "type": "SafetyInstructionFormat",
                        "message": f"{keyword} label is empty. Add a clear and accurate command or condition.",
                        "offset": first_token.idx,
                        "length": len(first_token.text) + 1,
                    })
            else:
                # Check if the command/condition is in imperative form
                instruction_doc = nlp(after_label)
                has_imperative = False
                
                for token in instruction_doc:
                    if token.pos_ == "VERB" and token.tag_ == "VB":
                        # Check if it's a root verb (imperative form)
                        if token.dep_ == "ROOT":
                            has_imperative = True
                            break
                
                # If no imperative verb found, flag it
                if not has_imperative and keyword in HIGH_RISK_SAFETY_KEYWORDS:
                    if first_token.idx not in seen:
                        seen.add(first_token.idx)
                        issues.append({
                            "type": "SafetyInstructionFormat",
                            "message": f"{keyword} instruction should start with a clear command in imperative form.",
                            "offset": first_token.idx,
                            "length": len(first_token.text) + 1,
                        })
    
    return issues


def check_safety_instruction_explanation(doc):
    """Check for safety instruction explanations (Rule 7.3).
    
    Rule 7.3: "Give an explanation to show the risk or possible result."
    
    If it is possible, always tell your reader about the problems that can occur 
    if the reader does not obey the safety instruction.
    
    Safety instructions must stay within a single paragraph. The explanation must
    be in the same paragraph as the safety keyword.
    
    Uses spaCy features:
    - doc.sents for sentence segmentation
    - token.pos_ to identify verbs (VERB)
    - token.lemma_ for base form comparison
    - token.dep_ to check for causal relationships
    - Dependency parsing to verify explanation structure
    """
    issues = []
    seen = set()
    
    # Get all sentences first
    sentences = list(doc.sents)
    
    for i, sent in enumerate(sentences):
        # Check if the sentence starts with a safety keyword
        first_token = next((t for t in sent if t.pos_ != "PUNCT"), None)
        
        if first_token and first_token.text.upper() in HIGH_RISK_SAFETY_KEYWORDS:
            # Extract the full safety instruction (may span multiple sentences within the same paragraph)
            instruction_text = sent.text.strip()
            current_para_idx = _get_paragraph_index(sent, doc)
            
            # Check subsequent sentences within the same paragraph
            # Stop when we hit: another safety keyword, NOTE, or a paragraph break
            j = i + 1
            while j < len(sentences):
                next_sent = sentences[j]
                next_para_idx = _get_paragraph_index(next_sent, doc)
                
                # Stop if we've crossed a paragraph boundary
                if next_para_idx != current_para_idx:
                    break
                
                next_first = next((t for t in next_sent if t.pos_ != "PUNCT"), None)
                
                # Stop if we hit another safety keyword or NOTE
                if next_first and next_first.text.upper() in SAFETY_KEYWORDS:
                    break
                
                instruction_text += " " + next_sent.text.strip()
                j += 1
            
            # Parse the full instruction
            instruction_doc = nlp(instruction_text)
            
            # Check if the instruction has an explanation (risk or possible result)
            has_explanation = False
            
            for token in instruction_doc:
                # Check for risk indicators
                if token.lemma_.lower() in RISK_INDICATORS:
                    has_explanation = True
                    break
                
                # Check for causal dependencies (caused_by, agent, etc.)
                if token.dep_ in ("caused_by", "agent", "nmod"):
                    has_explanation = True
                    break
            
            # If no explanation found, flag it
            if not has_explanation and first_token.idx not in seen:
                seen.add(first_token.idx)
                issues.append({
                    "type": "SafetyInstructionExplanation",
                    "message": f"Add an explanation to show the risk or possible result.",
                    "offset": first_token.idx,
                    "length": len(instruction_text),
                })
    
    return issues


# Load spaCy model for safety instruction analysis
try:
    nlp = spacy.load("en_core_web_sm")
except:
    nlp = None
