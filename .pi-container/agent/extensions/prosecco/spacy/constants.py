#!/usr/bin/env python3
"""
ASD-STE100 constants and configuration for spaCy-based grammar checking.

This module contains all mapping dictionaries, word lists, patterns, and
configuration constants used by the ASD-STE100 grammar check modules.

Constants are organized by ASD-STE100 rule category:
- Rule 1.x: Words (technical nouns, verb approvals, false friends, etc.)
- Rule 2.x: Multi-word nouns
- Rule 3.x: Verbs (forms, tenses, passive voice, etc.)
- Rule 4.x: Sentences (contractions, connecting words, articles, etc.)
- Rule 5.x: Procedural writing (sentence length, imperatives, etc.)
- Rule 6.x: Descriptive writing (keywords, paragraph structure, etc.)
- Rule 7.x: Safety instructions
- Rule 8.x: Punctuation and word count
- Rule 9.x: Writing practices (phrasal verbs, word usage, consistent style, etc.)
- GR-1 to GR-8: General recommendations

All constants are imported by the check modules (checks_section*.py) and used
in the data-driven pattern matching approach.
"""
import re
import sys
import spacy

# Contractions mapping (matches Contractions.yml and Rule 4.2)
# Rule 4.2: "Do not omit words or use contractions to make your sentences shorter."
CONTRACTIONS = {
    "ain't": "am not; are not; is not; has not; have not",
    "aren't": "are not",
    "can't": "cannot",
    "couldn't": "could not",
    "didn't": "did not",
    "doesn't": "does not",
    "don't": "do not",
    "daren't": "dare not",
    "hadn't": "had not",
    "hasn't": "has not",
    "haven't": "have not",
    "he's": "he is; he has",
    "here's": "here is",
    "how's": "how is; how has",
    "i'm": "i am",
    "isn't": "is not",
    "it's": "it is; it has",
    "let's": "let us",
    "mightn't": "might not",
    "mustn't": "must not",
    "needn't": "need not",
    "oughtn't": "ought not",
    "she's": "she is; she has",
    "shouldn't": "should not",
    "wouldn't": "would not",
    "that's": "that is; that has",
    "they're": "they are",
    "they've": "they have",
    "wasn't": "was not",
    "we're": "we are",
    "we've": "we have",
    "weren't": "were not",
    "what's": "what is; what has",
    "when's": "when is; when has",
    "where's": "where is; where has",
    "who's": "who is; who has",
    "won't": "will not",
    "you're": "you are",
    "you've": "you have",
}

# Passive voice exceptions (matches PassiveVoice.yml and Rule 3.6)
# Rule 3.6: "Use the active voice. In descriptive writing, you can use the passive voice only if the agent is unknown."
PASSIVE_EXCEPTIONS = {
    "is here", "is there", "is where", "is when", "is who", "is why", "is how",
    "was here", "was there", "are here", "are there",
}

# Forbidden modals (matches List of recurring errors)
# shall → MUST, should → MUST, may → CAN
FORBIDDEN_MODALS = {
    "shall": "must",
    "should": "must",
    "may": "can",
}

# Be verbs for IngForms detection
BE_VERBS = {"is", "are", "was", "were", "be", "been", "being", "am"}

# Approved -ing words (matches Rule 3.5 exceptions)
# Rule 3.5: "Use the -ing form of a verb only as a technical noun or as a modifier in a technical noun."
# Exceptions from the dictionary:
# - Nouns (lighting, opening, routing, servicing)
# - Adjectives (mating, missing, remaining)
# - A pronoun (something)
# - A preposition (during)
APPROVED_ING_WORDS = {
    "lighting", "opening", "routing", "servicing",
    "mating", "missing", "remaining",
    "something",
    "during",
}

# Phrasal verbs to detect (Rule 9.3)
# Rule 9.3: "When you use two words together, do not make phrasal verbs."
# These are approved words used together in ways that create phrasal verb meanings.
# Format: (word1, word2) -> suggested replacement
PHRASAL_VERBS = {
    # 2-word phrasal verbs
    ("add", "up"): "add",
    ("back", "up"): "backup",
    ("break", "down"): "stop working",
    ("bring", "up"): "mention",
    ("call", "off"): "cancel",
    ("carry", "out"): "do",
    ("check", "out"): "verify",
    ("clear", "up"): "explain",
    ("come", "across"): "find",
    ("come", "up"): "appear",
    ("come", "up with"): "think of",
    ("cut", "back"): "reduce",
    ("cut", "off"): "disconnect",
    ("deal", "with"): "handle",
    ("drop", "off"): "decrease",
    ("drop", "out"): "leave",
    ("end", "up"): "finish",
    ("face", "up to"): "accept",
    ("fill", "in"): "complete",
    ("figure", "out"): "solve",
    ("find", "out"): "discover",
    ("get", "along"): "have a good relationship",
    ("get", "by"): "manage",
    ("get", "over"): "recover",
    ("give", "up"): "stop",
    ("go", "ahead"): "proceed",
    ("go", "back"): "return",
    ("go", "down"): "decrease",
    ("go", "through"): "experience",
    ("grow", "up"): "mature",
    ("hand", "in"): "submit",
    ("hold", "up"): "delay",
    ("keep", "up"): "continue",
    ("kind", "of"): "somewhat",
    ("let", "down"): "disappoint",
    ("let", "off"): "excuse",
    ("look", "after"): "care for",
    ("look", "for"): "search",
    ("look", "into"): "investigate",
    ("make", "up"): "invent",
    ("pick", "up"): "lift",
    ("put", "off"): "postpone",
    ("put", "out"): "extinguish",
    ("put", "up"): "build",
    ("run", "out"): "exhaust",
    ("show", "up"): "appear",
    ("take", "off"): "remove",
    ("take", "over"): "replace",
    ("take", "up"): "start",
    ("turn", "down"): "refuse",
    ("turn", "off"): "switch off",
    ("turn", "on"): "switch on",
    ("turn", "up"): "increase",
    ("wake", "up"): "awaken",
    ("work", "out"): "exercise",
    
    # Additional phrasal verbs from PDF examples
    ("give", "off"): "release",
    ("let", "go"): "release",
    ("fill", "up"): "fill",
    ("clean", "up"): "clean",
    ("put", "away"): "store",
    ("hand", "out"): "distribute",
    ("break", "off"): "detach",
    ("break", "up"): "separate",
    ("call", "back"): "return",
    ("call", "out"): "summon",
    ("carry", "on"): "continue",
    ("close", "down"): "shut",
    ("count", "on"): "rely",
    ("drop", "off"): "deliver",
    ("fall", "apart"): "disassemble",
    ("get", "back"): "return",
    ("get", "off"): "exit",
    ("get", "on"): "enter",
    ("get", "up"): "stand",
    ("hold", "on"): "wait",
    ("keep", "up"): "maintain",
    ("move", "in"): "install",
    ("move", "out"): "remove",
    ("pass", "out"): "distribute",
    ("pay", "back"): "repay",
    ("plug", "in"): "connect",
    ("point", "out"): "indicate",
    ("pull", "out"): "extract",
    ("set", "up"): "install",
    ("slow", "down"): "decelerate",
    ("step", "down"): "resign",
    ("take", "back"): "retrieve",
    ("take", "out"): "remove",
    ("team", "up"): "combine",
    ("throw", "away"): "discard",
    ("throw", "out"): "discard",
    ("turn", "down"): "reject",
    ("wake", "up"): "awaken",
    ("write", "down"): "record",
    
    # Context-specific phrasal verbs
    ("put", "on"): "wear",
    ("come", "on"): "start",
    ("go", "off"): "explode",
    ("run", "out", "of"): "exhaust",
    ("look", "at"): "examine",
    ("switch", "off"): "de-energize",
    ("switch", "on"): "energize",
}

# Non-approved words (from List of recurring errors)
# These are common words that writers use incorrectly in STE
NON_APPROVED_WORDS = {
    "acceptable": "permitted",
    "access": "approach",
    "accomplish": "do",
    "acquire": "get",
    "adjacent": "next to",
    "adjourn": "end the meeting",
    "additional": "more",
    "advantageous": "good",
    "aforementioned": "this",
    "aim": "try",
    "allocate": "assign",
    "alternate": "alternative",
    "ambiguity": "confusion",
    "ample": "enough",
    "annex": "add",
    "anticipate": "expect",
    "applicable": "for",
    "approximate": "about",
    "assemble": "put together",
    "assume": "suppose",
    "attempt": "try",
    "authorize": "permit",
    "automatic": "self-acting",
    "avail": "help",
    "aware": "know",
    "basing": "base",
    "benefit": "help",
    "brief": "short",
    "calculate": "compute",
    "calibrate": "adjust",
    "capability": "ability",
    "capitalize": "use",
    "carryout": "do",
    "cause": "make",
    "characteristic": "feature",
    "chaser": "pursuer",
    "chronic": "long-term",
    "circumstance": "condition",
    "classify": "group",
    "collect": "gather",
    "comprise": "include",
    "concept": "idea",
    "conceive": "think",
    "concentrate": "focus",
    "concern": "about",
    "conclude": "finish",
    "confer": "give",
    "confirm": "state",
    "conform": "agree",
    "confuse": "mix up",
    "consider": "think about",
    "consolidate": "combine",
    "constrain": "limit",
    "construct": "build",
    "consult": "ask",
    "contain": "have",
    "contaminate": "dirty",
    "contingent": "possible",
    "continue": "keep",
    "contribute": "give",
    "convert": "change",
    "convey": "give",
    "cooperate": "work together",
    "correspond": "match",
    "criterion": "standard",
    "criteria": "standards",
    "deem": "consider",
    "deficiency": "lack",
    "define": "state",
    "deliberate": "think",
    "deliver": "give",
    "demonstrate": "show",
    "denote": "mean",
    "depart": "leave",
    "depict": "show",
    "derive": "get",
    "despite": "even if",
    "deteriorate": "worsen",
    "determine": "find",
    "deviate": "change",
    "devise": "plan",
    "diminish": "reduce",
    "disclose": "show",
    "discontinue": "stop",
    "discriminate": "distinguish",
    "display": "show",
    "disseminate": "spread",
    "distinguish": "tell apart",
    "diverge": "separate",
    "domesticate": "train",
    "elaborate": "explain",
    "electronic": "digital",
    "eliminate": "remove",
    "embark": "start",
    "emerge": "appear",
    "emit": "give off",
    "empirical": "experimental",
    "encompass": "include",
    "encounter": "meet",
    "endeavor": "attempt",
    "enhance": "improve",
    "enlarge": "increase",
    "enforce": "apply",
    "ensure": "make sure",
    "equivalent": "equal",
    "establish": "set up",
    "evaluate": "check",
    "evolve": "develop",
    "exceed": "be more than",
    "execute": "do",
    "exhibit": "show",
    "expenditure": "cost",
    "expedite": "hasten",
    "expedition": "journey",
    "expedient": "suitable",
    "expertise": "knowledge",
    "expose": "show",
    "extend": "apply to",
    "facilitate": "help",
    "feasible": "possible",
    "fellow": "member",
    "finalize": "complete",
    "fluctuate": "vary",
    "formulate": "develop",
    "forthwith": "immediately",
    "function": "work",
    "furthermore": "also",
    "gain": "get",
    "gratuitous": "free",
    "hereby": "by this",
    "herein": "in this",
    "hitherto": "until now",
    "identify": "find",
    "impart": "give",
    "implement": "do",
    "imply": "suggest",
    "improve": "make better",
    "indicate": "show",
    "initiate": "start",
    "instantly": "immediately",
    "instantiate": "create",
    "institute": "start",
    "instruct": "tell",
    "integrate": "combine",
    "intend": "plan",
    "interact": "work together",
    "interim": "temporary",
    "interpret": "explain",
    "intervene": "step in",
    "intricate": "complex",
    "invalidate": "cancel",
    "involve": "include",
    "itemize": "list",
    "judicious": "wise",
    "lateral": "side",
    "leverage": "use",
    "lieutenant": "deputy",
    "locate": "find",
    "manifest": "show",
    "mandatory": "required",
    "material": "important",
    "maximize": "increase to maximum",
    "merely": "only",
    "methodology": "method",
    "mitigate": "reduce",
    "modulate": "adjust",
    "multifaceted": "complex",
    "negate": "cancel",
    "negotiate": "discuss",
    "nonetheless": "however",
    "notwithstanding": "despite",
    "nowhere": "not here",
    "nullify": "cancel",
    "object": "item",
    "obtain": "get",
    "occasion": "cause",
    "optimize": "make best",
    "option": "choice",
    "orient": "align",
    "outlay": "cost",
    "outperform": "be better than",
    "outweigh": "be greater than",
    "participate": "take part",
    "perceive": "see",
    "perpetuate": "continue",
    "perquisite": "benefit",
    "pertain": "relate to",
    "peruse": "read",
    "phase": "stage",
    "placate": "pacify",
    "plural": "more than one",
    "poignant": "touching",
    "populate": "fill",
    "portend": "predict",
    "possess": "have",
    "postulate": "assume",
    "preclude": "prevent",
    "predominant": "main",
    "predecessor": "before",
    "preempt": "replace",
    "preparatory": "ready",
    "present": "give",
    "prior": "before",
    "prioritize": "rank",
    "privilege": "advantage",
    "procedural": "process",
    "proclaim": "announce",
    "procurable": "available",
    "proliferate": "multiply",
    "proposition": "proposal",
    "prospective": "possible",
    "proviso": "condition",
    "pursuant": "according to",
    "pursue": "follow",
    "questionnaire": "survey",
    "radical": "fundamental",
    "rational": "logical",
    "reconcile": "agree",
    "reconfigure": "change",
    "reconstruct": "build again",
    "redefine": "define again",
    "redundant": "extra",
    "refrain": "avoid",
    "regarding": "about",
    "relinquish": "give up",
    "relevant": "related to",
    "requisite": "required",
    "rescind": "cancel",
    "resolve": "solve",
    "respectively": "separately",
    "restive": "impatient",
    "resume": "continue",
    "resumption": "restart",
    "reticent": "quiet",
    "retrieve": "get back",
    "revert": "return",
    "revoke": "cancel",
    "rhetoric": "speech",
    "scrutinize": "examine",
    "secure": "attach, safety",
    "segregate": "separate",
    "significant": "important",
    "simulate": "imitate",
    "sophisticated": "complex",
    "specify": "state",
    "spurious": "false",
    "stipulate": "require",
    "strive": "try",
    "subsequent": "later",
    "substantiate": "prove",
    "substitute": "replace",
    "sufficient": "enough",
    "summarize": "sum up",
    "supplement": "add",
    "supplant": "replace",
    "supplementary": "additional",
    "supreme": "highest",
    "surmise": "guess",
    "terminate": "end",
    "therein": "in that",
    "thereof": "of that",
    "thereunder": "under that",
    "thus": "therefore",
    "topple": "fall",
    "totaled": "total",
    "traduce": "slander",
    "transcend": "go beyond",
    "transmit": "send",
    "traverse": "cross",
    "trepidation": "fear",
    "truncated": "cut short",
    "ultimate": "final",
    "unanimous": "agreed",
    "undermine": "weaken",
    "utilize": "use",
    "validate": "confirm",
    "versus": "vs",
    "viable": "possible",
    "vicarious": "secondhand",
    "vice": "deputy",
    "virtue": "advantage",
    "visualize": "imagine",
    "vulnerable": "at risk",
    "whereby": "by which",
    "witness": "see",
    "warrant": "guarantee",
    "wherein": "in which",
    "whilst": "while",
    
    # Additional non-approved words
    "any": None,  # No direct alternative, use different sentence construction
    "avoid": "prevent",
    "both": "the two",
    "check": "check",  # "check" is only approved as a noun, not verb
    "cover": "cover",  # "cover" is only approved as a technical noun, not verb
    "complete": "completed",
    "damage": "damage",  # "damage" is only approved as a noun, not verb
    "fit": "install",
    "follow": "obey",
    "further": "more",
    "have to": "use an action verb in the imperative form",
    "however": "but",
    "insert": "put",
    "main": "primary",
    "may": "can",
    "need": "necessary",
    "now": "at this time",
    "old": "remaining, used, expired",
    "over": "above, on, along",
    "people": "person, personnel",
    "perform": "do",
    "portion": "part",
    "press": "push",
    "reach": "get",
    "repeat": "do ... again",
    "required": "necessary",
    "rotate": "turn",
    "shall": "must",
    "should": "must",
    "since": "because",
    "test": "test",  # "test" is only approved as a noun, not verb
    "therefore": "thus, as a result",
    "under": "below, in, less than",
    "using": "use, with",
}

# Regional, slang, and jargon words (Rule 1.10)
# Rule 1.10: "Do not use regional, slang, or jargon words as technical nouns."
REGIONAL_SLANG_JARGON = {
    "choker": "cable",  # Regional term in logging operations
    "brick": "set to OFF",  # Technical slang in IT
    "rig": "equipment",  # Sailing jargon
    "knackwurst": "sausage",  # Regional German term
    "jacket": "cover",  # Regional term for engine cover
    "bonnet": "hood",  # British English for car hood
    "boot": "trunk",  # British English for car trunk
    "lift": "elevator",  # British English for elevator
    "flat": "apartment",  # British English for apartment
    "lorry": "truck",  # British English for truck
    "petrol": "gasoline",  # British English for gasoline
    "queue": "line",  # British English for line
    "nappy": "diaper",  # British English for diaper
    "torch": "flashlight",  # British English for flashlight
    "tap": "faucet",  # British English for faucet
    "roundabout": "traffic circle",  # British English for traffic circle
    "footpath": "sidewalk",  # British English for sidewalk
    # Note: British spellings like 'colour', 'centre', 'program' are now covered by BritishEnglish check
    # Note: British spellings like 'colour', 'centre', 'program' are now covered by BritishEnglish check
    # Only keep terms that are NOT simple spelling differences
    "motorway": "highway",  # British English for highway
    "dustbin": "trash can",  # British English for trash can
    "crisps": "chips",  # British English for chips
    "chips": "fries",  # British English for fries (context-dependent)
}



# British English spellings (Rule 1.14)
# Rule 1.14: "Use American English spelling unless other official directives tell you differently."
# Use the spelling specified in the STE dictionary (American English spelling).
# Use a different spelling only if other technical publication specifications,
# style guides, contracts, or other official directives are applicable.
BRITISH_ENGLISH = {
    "colour": "color",
    "favour": "favor",
    "honour": "honor",
    "labour": "labor",
    "neighbour": "neighbor",
    "behaviour": "behavior",
    "catalogue": "catalog",
    "centre": "center",
    "metre": "meter",
    "litre": "liter",
    "tonne": "ton",
    "defence": "defense",
    "offence": "offense",
    "pretence": "pretense",
    "licence": "license",  # noun
    "practise": "practice",  # verb
    "analyse": "analyze",
    "catalyse": "catalyze",
    "emphasise": "emphasize",
    "organise": "organize",
    "realise": "realize",
    "signalling": "signaling",
    "travelled": "traveled",
    "modelling": "modeling",
    "labelling": "labeling",
    "cancelled": "canceled",
    "travelling": "traveling",
    "fibre": "fiber",
    "grey": "gray",
    "plough": "plow",
    "tyre": "tire",
    "fulfil": "fulfill",
    "aeroplane": "airplane",
    "analogue": "analog",
    "artefact": "artifact",
    "authorise": "authorize",
    "behavioural": "behavioral",
    "capitalise": "capitalize",
    "categorise": "categorize",
    "characterise": "characterize",
    "cheque": "check",
    "civilisation": "civilization",
    "crystallise": "crystallize",
    "dialogue": "dialog",
    "draught": "draft",
    "encyclopaedia": "encyclopedia",
    "enrolment": "enrollment",
    "favourite": "favorite",
    "gaol": "jail",
    "humour": "humor",
    "initialled": "initialed",
    "jewellery": "jewelry",
    "manoeuvre": "maneuver",
    "meagre": "meager",
    "normalise": "normalize",
    "palaeontology": "paleontology",
    "programme": "program",
    "pyjamas": "pajamas",
    "rigour": "rigor",
    "rumour": "rumor",
    "sabre": "saber",
    "smoulder": "smolder",
    "specialise": "specialize",
    "sulphur": "sulfur",
    "theatre": "theater",
    "traveller": "traveler",
    "vigour": "vigor",
    "wonky": "crooked",
    "yoghurt": "yogurt",
}

# Gender-specific pronouns to detect (GR-7)
# GR-7: "STE does not include examples of inclusive language, but it fully complies
# with gender-neutral language requirements. When you write in STE, make sure that
# you always use gender-neutral language. Gender-specific pronouns, for example
# 'he' or 'she' are not permitted in STE."
GENDER_PRONOUNS = {
    "he": "they",
    "him": "them",
    "his": "their",
    "she": "they",
    "her": "them",
    "man": "person",
    "woman": "person",
}

# False friends to detect (GR-5)
# GR-5: "False friends"
# A false friend is a word that looks the same as one in a person's native language
# but has a different meaning in English.
FALSE_FRIENDS = {
    "actually": "currently",
    "assist": "help",
    "capital": "city",
    "curious": "strange",
    "eventually": "finally",
    "fabric": "cloth",
    "familiar": "relative",
    "gift": "present",
    "lie": "recline",
    "mass": "crowd",
    "mind": "think",
    "note": "observe",
    "pain": "hurt",
    "pretty": "attractive",
    "real": "actual",
    "scene": "view",
    "sensitive": "sensible",
    "soap": "clean",
    "start": "begin",
    "tape": "band",
    "train": "education",
    "trousers": "pants",
    "vacant": "empty",
    "waste": "trash",
}

# Words with restricted meanings (Rule 9.2)
# Rule 9.2: "Use each approved word correctly."
# Some STE-approved words have meanings that are applicable only in some contexts
# (restricted meaning). Always make sure that the word that you select has the
# correct meaning in the applicable context.
RESTRICTED_WORDS = {
    "wear": "use or put on",  # "wear" means "to become damaged by friction"
    "extend": "apply to",  # "extend" means "to increase in dimension or range"
    "go down": "decrease",  # "go down" refers to physical movement
    "go through": "pass through",  # "go through" refers to physical movement
    "see": "make sure",  # "see" means "to perceive with eyes"
    "turn": "rotate",  # "turn" means "to move around an axis"
    "work": "do work",  # "work" is approved as a noun, not a verb
    "help": "use help",  # "help" is approved as a verb, not a noun
    "damage": "cause damage",  # "damage" is approved as a noun, not verb
}

# Consistent style patterns (Rule 9.4)
# Rule 9.4: "When you select terminology or wording, always use a consistent style."
# When you select terminology or wording for a work step, use the same terminology
# or wording each time that type of work step occurs. Different terminology or
# wording can cause confusion and delays.
CONSISTENT_STYLE_PATTERNS = {
    # Different terms for the same item
    "main body": "body assembly",
    "body": "body assembly",
    "servo control unit": "actuator",
    "control unit": "actuator",
    
    # Different wordings for the same action
    "torque-tighten": "torque",
    "lubricate the .* bolts": "apply a small quantity of oil to the threads of the .* bolts",
    "apply a small quantity of oil to the threads of the .* bolts": "lubricate the .* bolts",
    
    # Different terms for the same component
    "fastener": "bolt",
    "nut": "nut",
    "washer": "washer",
    "seal": "seal",
    "O-ring": "O-ring",
    
    # Different terms for the same position
    "open position": "OPEN",
    "closed position": "CLOSED",
    "middle position": "MIDDLE",
    
    # Different terms for the same tool
    "torque wrench": "torque wrench",
    "wrench": "torque wrench",
    "screwdriver": "screwdriver",
}


# Technical nouns that should not be used as verbs (Rule 1.7)
# Rule 1.7: "Do not use words that are technical nouns as verbs."
# Use a technical noun only as a noun or as an adjective that is part of a
# different technical noun. Do not use the same word as a verb.
TECHNICAL_NOUNS_NOT_AS_VERBS = {
    "oil": "apply oil to",
    "snow": "snow will fall",
    "drill": "use a drill to",
    "sample": "take a sample of",
    "test": "do a test of",
    "check": "do a check of",
    "clean": "clean the",
    "cover": "put a cover on",
    "fill": "fill the",
    "level": "level the",
    "mark": "mark the",
    "note": "note that",
    "plan": "make a plan for",
    "point": "point to",
    "record": "record the",
    "report": "report that",
    "set": "set the",
    "tag": "tag the",
    "turn": "turn the",
}

# Technical verbs that should not be used as nouns (Rule 1.13)
# Rule 1.13: "Do not use technical verbs as nouns."
# Use a technical verb only as a verb. Do not use the same word as a noun.
TECHNICAL_VERBS_NOT_AS_NOUNS = {
    "drill": "use drilling",
    "sample": "take a sample",
    "test": "do a test",
    "check": "do a check",
    "clean": "cleaning",
    "cover": "put a cover",
    "fill": "filling",
    "level": "leveling",
    "mark": "marking",
    "note": "noting",
    "plan": "making a plan",
    "point": "pointing",
    "record": "recording",
    "report": "reporting",
    "set": "setting",
    "tag": "tagging",
    "turn": "turning",
}

# Latin abbreviations to detect (GR-6)
# GR-6: "STE recommends that you do not use Latin abbreviations because they can
# confuse your readers if they do not know them. Always use English words to make
# the text clear."
LATIN_ABBREVIATIONS = {
    "e.g.": "for example",
    "i.e.": "that is",
    "etc.": "and so on",
    "viz.": "namely",
    "ibid.": "in the same place",
    "op. cit.": "work cited",
    "vol.": "volume",
    "vs.": "versus",
}

# Section 1 Constants (Words)
# Rule 1.1-1.14: Words, Part of Speech, Approved Meaning, Technical Nouns, etc.

# Rule 1.2: Words with restricted POS in STE dictionary
# Format: word -> (approved_pos, disapproved_pos)
# These are words that are commonly misused with incorrect part of speech
RESTRICTED_WORDS_POS = {
    "oil": ("NOUN", "VERB"),
    "grease": ("NOUN", "VERB"),
    "clean": ("ADJ", "VERB"),
    "level": ("NOUN", "VERB"),
    "track": ("NOUN", "VERB"),
    "drive": ("NOUN", "VERB"),
    "run": ("NOUN", "VERB"),
    "set": ("NOUN", "VERB"),
    "check": ("NOUN", "VERB"),
    "hold": ("NOUN", "VERB"),
    "cover": ("NOUN", "VERB"),
    "case": ("NOUN", "VERB"),
    "box": ("NOUN", "VERB"),
    "pack": ("NOUN", "VERB"),
    "packaging": ("NOUN", "VERB"),
}

# Rule 1.3: Words with restricted meanings in STE
# Format: word -> {"approved": [...], "disapproved": [...]}
# These are words that have different meanings in STE vs standard English
RESTRICTED_WORDS_MEANING = {
    "apply": {
        "approved": ["surface", "coating", "paint", "oil", "grease", "adhesive"],
        "disapproved": ["rule", "law", "regulation", "pressure", "force"]
    },
    "clean": {
        "approved": ["dirt", "contamination", "surface", "filter", "part"],
        "disapproved": ["house", "room", "building", "office", "car"]
    },
    "connect": {
        "approved": ["wire", "cable", "hose", "pipe", "connector"],
        "disapproved": ["meeting", "person", "event", "relationship"]
    },
    "check": {
        "approved": ["condition", "status", "value", "level", "position"],
        "disapproved": ["mail", "email", "box", "list", "name"]
    },
    "close": {
        "approved": ["door", "valve", "switch", "circuit", "cap"],
        "disapproved": ["meeting", "deal", "sale", "shop", "business"]
    },
    "open": {
        "approved": ["cap", "cover", "door", "valve", "switch"],
        "disapproved": ["meeting", "event", "show", "store", "business"]
    },
    "operate": {
        "approved": ["device", "system", "equipment", "machine", "vehicle"],
        "disapproved": ["business", "company", "organization"]
    },
    "position": {
        "approved": ["object", "component", "part", "assembly", "device"],
        "disapproved": ["person", "employee", "worker", "staff"]
    },
    "put": {
        "approved": ["object", "component", "part", "item"],
        "disapproved": ["effort", "time", "energy", "work"]
    },
    "remove": {
        "approved": ["object", "substance", "part", "component", "assembly"],
        "disapproved": ["duty", "responsibility", "position", "job"]
    },
    "set": {
        "approved": ["value", "parameter", "limit", "threshold", "level"],
        "disapproved": ["table", "alarm", "clock", "timer", "date"]
    },
    "turn": {
        "approved": ["knob", "switch", "valve", "handle", "wheel"],
        "disapproved": ["around", "corner", "page", "head", "body"]
    },
}

# Rule 1.4: Approved -ing forms that can be used as technical nouns
# These are common -ing forms that are approved in STE for technical writing
APPROVED_ING_FORMS = {
    "opening", "closing", "putting", "taking", "making",
    "giving", "getting", "going", "coming", "doing",
    "setting", "holding", "checking", "cleaning",
    "connecting", "disconnecting", "installing",
    "removing", "testing", "adjusting",
    "tightening", "lubricating", "filling", "draining"
}

# Rule 1.9: Long technical noun patterns that should be shortened
# Format: [(regex_pattern, replacement)]
# These are patterns where a shorter, more common term should be used
LONG_TECHNICAL_NOUN_PATTERNS = [
    (r"(?i)stainless\s+steel\s+pan\s+head\s+machine\s+screws", "screws"),
    (r"(?i)metallic\s+machined\s+flange", "flange"),
    (r"(?i)front\s+housing\s+cover", "cover"),
    (r"(?i)servo\s+control\s+unit", "actuator"),
    (r"(?i)\bcontrol\s+unit\b", "actuator"),
    (r"(?i)main\s+body", "body assembly"),
    (r"(?i)\bbody\b", "body assembly"),
]

# Rule 1.11: Inconsistent technical noun patterns
# Format: [(regex_pattern, replacement)]
# These are patterns where terminology should be consistent throughout the document
INCONSISTENT_TECHNICAL_NOUN_PATTERNS = [
    (r"(?i)servo\s+control\s+unit", "actuator"),
    (r"(?i)\bcontrol\s+unit\b", "actuator"),
    (r"(?i)main\s+body", "body assembly"),
    (r"(?i)\bbody\b", "body assembly"),
    (r"(?i)torque-tighten", "torque"),
]

# Section 3 Constants (Verbs)
# Rule 3.1-3.7: Verb forms, tenses, passive voice, -ing forms, etc.

# Rule 3.1: Approved verb tags in STE
# These are the coarse POS tags that are approved for verb usage in STE
# VB: base form (infinitive without "to")
# VBD: simple past
# VBN: past participle
# VBZ: 3rd person singular present
# VBP: present tense (except 3rd person singular)
# MD: modal (only "can", "may", "must")
# VP: auxiliary (be, have, do)
APPROVED_VERB_TAGS = {"VB", "VBD", "VBN", "VBZ", "VBP", "MD", "VP"}

# Rule 3.7: Common patterns where nouns are used instead of approved verbs
# Format: [(pattern, replacement)]
# These are common patterns where a noun phrase should be replaced with an approved verb
# Patterns use word boundaries (\b) to match complete words
# Case-insensitive matching is enabled by default in check_noun_as_verb
# Verb conjugations are handled by matching the lemma form with optional endings
NOUN_AS_VERB_PATTERNS = [
    # "give" patterns (handles gives, gave, given, giving)
    (r"\bgive\w*\s+an\s+indication\s+of", "show"),
    (r"\bgive\w*\s+an\s+indication", "show"),
    (r"\bgive\w*\s+a\s+reading\s+of", "show"),
    (r"\bgive\w*\s+a\s+reading", "show"),
    (r"\bgive\w*\s+a\s+measurement\s+of", "show"),
    (r"\bgive\w*\s+a\s+measurement", "show"),
    # "make" patterns (handles makes, made, making)
    (r"\bmake\w*\s+an\s+inspection\s+of", "inspect"),
    (r"\bmake\w*\s+an\s+inspection", "inspect"),
    # "do" patterns (handles does, did, doing)
    (r"\bdo\w*\s+a\s+check\s+of", "check"),
    (r"\bdo\w*\s+a\s+check", "check"),
    (r"\bdo\w*\s+a\s+test\s+of", "test"),
    (r"\bdo\w*\s+a\s+test", "test"),
    (r"\bdo\w*\s+a\s+verification\s+of", "verify"),
    (r"\bdo\w*\s+a\s+verification", "verify"),
    # "perform" patterns (handles performs, performed, performing)
    (r"\bperform\w*\s+an\s+inspection\s+of", "inspect"),
    (r"\bperform\w*\s+an\s+inspection", "inspect"),
    (r"\bperform\w*\s+a\s+check\s+of", "check"),
    (r"\bperform\w*\s+a\s+check", "check"),
    (r"\bperform\w*\s+a\s+test\s+of", "test"),
    (r"\bperform\w*\s+a\s+test", "test"),
    (r"\bperform\w*\s+a\s+verification\s+of", "verify"),
    (r"\bperform\w*\s+a\s+verification", "verify"),
    # "carry out" patterns (handles carries out, carried out, carrying out)
    (r"\bcarry\w*\s+out\s+an\s+inspection\s+of", "inspect"),
    (r"\bcarry\w*\s+out\s+an\s+inspection", "inspect"),
    (r"\bcarry\w*\s+out\s+a\s+check\s+of", "check"),
    (r"\bcarry\w*\s+out\s+a\s+check", "check"),
    (r"\bcarry\w*\s+out\s+a\s+test\s+of", "test"),
    (r"\bcarry\w*\s+out\s+a\s+test", "test"),
    (r"\bcarry\w*\s+out\s+a\s+verification\s+of", "verify"),
    (r"\bcarry\w*\s+out\s+a\s+verification", "verify"),
    # "conduct" patterns (handles conducts, conducted, conducting)
    (r"\bconduct\w*\s+an\s+inspection\s+of", "inspect"),
    (r"\bconduct\w*\s+an\s+inspection", "inspect"),
    (r"\bconduct\w*\s+a\s+check\s+of", "check"),
    (r"\bconduct\w*\s+a\s+check", "check"),
    (r"\bconduct\w*\s+a\s+test\s+of", "test"),
    (r"\bconduct\w*\s+a\s+test", "test"),
    (r"\bconduct\w*\s+a\s+verification\s+of", "verify"),
    (r"\bconduct\w*\s+a\s+verification", "verify"),
    # "before/after the X of" patterns (no verb conjugation)
    (r"before\s+the\s+removal\s+of", "before you remove"),
    (r"after\s+the\s+removal\s+of", "after you remove"),
    (r"before\s+the\s+installation\s+of", "before you install"),
    (r"after\s+the\s+installation\s+of", "after you install"),
    (r"before\s+the\s+adjustment\s+of", "before you adjust"),
    (r"after\s+the\s+adjustment\s+of", "after you adjust"),
    (r"before\s+the\s+check\s+of", "before you check"),
    (r"after\s+the\s+check\s+of", "after you check"),
    (r"before\s+the\s+test\s+of", "before you test"),
    (r"after\s+the\s+test\s+of", "after you test"),
    (r"before\s+the\s+verification\s+of", "before you verify"),
    (r"after\s+the\s+verification\s+of", "after you verify"),
]

# Section 4 Constants (Sentences)
# Rule 4.1-4.5: Short sentences, contractions, vertical lists, connecting words, articles

# Rule 4.4: Common connecting words and phrases
# These are used to connect sentences that contain related topics
CONNECTING_WORDS = {
    "additionally", "furthermore", "moreover", "also", "besides",
    "however", "nevertheless", "nonetheless", "yet", "but",
    "therefore", "thus", "hence", "consequently", "so",
    "although", "though", "even though", "despite", "in spite of",
    "because", "since", "as", "due to",
    "next", "then", "after", "before", "finally", "first", "second", "third",
    "meanwhile", "similarly", "likewise", "in contrast", "on the other hand",
    "in addition", "as well", "too", "either", "neither",
}

# Section 5 Constants (Procedural writing)
# Rule 5.1-5.5: Sentence length, multiple instructions, imperative form, etc.

# Rule 5.4: Conditional words used to detect conditional clauses
# These words indicate a condition that should come first in the sentence
CONDITIONAL_WORDS = {
    "if", "when", "before", "after", "while", "although", "unless",
}

# Rule 5.5: Imperative verb lemmas that should not be in notes
# Notes must not contain instructions, so these verbs indicate a violation
IMPERATIVE_VERB_LEMMAS = {
    "make", "set", "install", "put", "remove", "adjust",
    "check", "test", "verify", "do", "open", "close",
    "connect", "disconnect", "attach", "detach", "tighten",
    "loosen", "clean", "inspect", "replace",
}

# Section 6 Constants (Descriptive writing)
# Rule 6.1-6.6: Information structure, key words, sentence length, paragraphs

# Rule 6.2: Common determiners to skip when extracting key terms
# These words are not considered key terms for terminology tracking
COMMON_DETERMINERS = {
    "the", "a", "an", "this", "that", "these", "those",
}

# Section 7 Constants (Safety instructions)
# Rule 7.1-7.3: Safety instruction format and explanations

# Rule 7.1: Safety instruction keywords
# These words identify the level of risk in safety instructions
SAFETY_KEYWORDS = {
    "WARNING", "CAUTION", "DANGER", "NOTE",
}

# Rule 7.1-7.2: High-risk safety keywords
# These keywords require imperative form and risk explanation
HIGH_RISK_SAFETY_KEYWORDS = {
    "WARNING", "CAUTION", "DANGER",
}

# Rule 7.3: Risk and explanation indicators
# These words/phrases indicate a risk explanation in safety instructions
RISK_INDICATORS = {
    "can", "may", "will", "might", "could",  # Modal verbs
    "cause", "result", "lead", "produce",  # Causal verbs
    "risk", "danger", "hazard", "damage",  # Risk nouns
    "injury", "death", "fire", "explosion",  # Consequence nouns
    "if", "when", "unless", "should", "before", "after",  # Conditional words
}

# Section 8 Constants (Punctuation and word count)
# Rule 8.1-8.7: Punctuation rules and word counting

# Rule 8.1: Forbidden punctuation
# Semicolons are not permitted in STE
FORBIDDEN_PUNCTUATION = {";"}

# Rule 8.3: Allowed uses for parentheses
# Parentheses are allowed for these specific purposes
PARENTHESES_ALLOWED_CONTEXTS = {
    "reference",  # Make references to illustrations or text
    "illustration",  # Include letters or numbers that identify items
    "work_step",  # Identify work steps in a procedure
    "abbreviation",  # Include abbreviations
    "singular_plural",  # Give singular and plural forms
    "explanation",  # Explain words or a part of a sentence
    "alternative",  # Include an alternative
}

# Rule 8.6: Common units of measurement
# Numbers together with units count as one word
COMMON_UNITS = {
    "mm", "cm", "m", "km",  # Length
    "in", "ft", "yd", "mi",  # Imperial length
    "g", "kg", "lb", "oz",  # Weight
    "L", "mL", "gal",  # Volume
    "s", "min", "h", "day",  # Time
    "psi", "bar", "atm",  # Pressure
    "hp", "kW", "MW", "W",  # Power
    "V", "kV", "mV",  # Voltage
    "A", "mA",  # Current
    "Hz", "kHz", "MHz", "GHz",  # Frequency
    "°C", "°F", "K",  # Temperature
    "N", "kN",  # Force
    "J", "kJ", "kJ/kg",  # Energy
    "rpm",  # Rotational speed
    "dB",  # Sound level
}

# Rule 8.6: Common abbreviations
# Abbreviations count as one word
COMMON_ABBREVIATIONS = {
    "e.g.", "i.e.", "etc.", "vs.", "v.s.",
    "approx.", "approx",  # approximately
    "min.", "max.",  # minimum/maximum
    "ref.", "ref",  # reference
    "eq.", "eq",  # equation
    "vol.", "vol",  # volume
    "op.", "op",  # operation
    "sec.", "sec",  # second/section
    "fig.", "fig",  # figure
    "tab.", "tab",  # table
    "eq.", "eq",  # equation
    "eq.", "eq",  # equation
}

# Rule 8.2: Common hyphenated technical terms
# Hyphenated words count as one word (Rule 8.7)
# These are examples of correctly hyphenated terms
COMMON_HYPHENATED_TERMS = {
    "multi-word", "multi-word-noun",  # Technical nouns
    "single-step", "multi-step",  # Process descriptors
    "long-term", "short-term",  # Time descriptors
    "high-speed", "low-speed",  # Speed descriptors
    "right-angle", "left-angle",  # Angle descriptors
    "flat-pack", "self-service",  # Compound modifiers
}

# Rule 8.3: Explanation words for parentheses
# These words indicate an explanation in parentheses
EXPLANATION_WORDS = {
    "that", "is", "meaning", "which", "means",
}

# Section 9 Constants (Writing practices)
# Rule 9.1-9.4: Different sentence constructions, word usage, phrasal verbs, consistent style

# Rule 9.3: Phrasal verbs to avoid
# These are combinations of approved words that form phrasal verbs with different meanings
# The value is the recommended replacement
PHRASAL_VERBS = {
    # 2-word phrasal verbs
    ("clean", "up"): "remove dirt from",
    ("clean", "off"): "remove dirt from",
    ("clean", "out"): "remove dirt from",
    ("close", "down"): "close",
    ("come", "from"): "originate in",
    ("come", "off"): "detach",
    ("come", "out"): "appear",
    ("come", "to"): "reach",
    ("cover", "up"): "hide",
    ("cut", "down"): "reduce",
    ("cut", "off"): "isolate",
    ("cut", "up"): "divide into pieces",
    ("fill", "in"): "complete",
    ("fill", "out"): "complete",
    ("fill", "up"): "fill completely",
    ("get", "back"): "return",
    ("get", "off"): "disembark",
    ("get", "on"): "embark",
    ("get", "out"): "exit",
    ("get", "up"): "rise",
    ("give", "back"): "return",
    ("give", "in"): "surrender",
    ("give", "up"): "abandon",
    ("go", "against"): "oppose",
    ("go", "ahead"): "proceed",
    ("go", "off"): "explode",
    ("go", "on"): "continue",
    ("go", "over"): "review",
    ("go", "through"): "examine",
    ("go", "up"): "increase",
    ("hold", "back"): "restrain",
    ("hold", "on"): "wait",
    ("hold", "up"): "delay",
    ("keep", "away"): "stay away",
    ("keep", "down"): "suppress",
    ("keep", "off"): "stay away",
    ("keep", "out"): "stay outside",
    ("keep", "up"): "maintain",
    ("let", "down"): "disappoint",
    ("let", "in"): "admit",
    ("let", "out"): "release",
    ("look", "after"): "take care of",
    ("look", "at"): "examine",
    ("look", "for"): "search for",
    ("look", "into"): "investigate",
    ("look", "out"): "be careful",
    ("look", "up"): "research",
    ("make", "up"): "invent",
    ("pass", "out"): "distribute",
    ("pick", "up"): "lift",
    ("put", "away"): "store",
    ("put", "off"): "postpone",
    ("put", "on"): "wear",
    ("put", "out"): "extinguish",
    ("take", "after"): "resemble",
    ("take", "apart"): "disassemble",
    ("take", "away"): "remove",
    ("take", "back"): "return",
    ("take", "down"): "write down",
    ("take", "off"): "remove",
    ("take", "out"): "remove",
    ("take", "over"): "assume control",
    ("take", "up"): "begin",
    ("throw", "away"): "discard",
    ("throw", "out"): "discard",
    ("turn", "around"): "rotate",
    ("turn", "down"): "reject",
    ("turn", "off"): "switch off",
    ("turn", "on"): "switch on",
    ("turn", "up"): "appear",
    ("wash", "up"): "clean",
    ("write", "down"): "record",
    
    # 3-word phrasal verbs
    ("come", "up", "with"): "propose",
    ("get", "away", "with"): "escape",
    ("get", "along", "with"): "have a good relationship",
    ("look", "forward", "to"): "anticipate",
    ("run", "out", "of"): "exhaust",
    ("take", "advantage", "of"): "exploit",
    ("make", "use", "of"): "utilize",
    ("put", "up", "with"): "tolerate",
    ("catch", "up", "with"): "reach",
    ("keep", "up", "with"): "maintain pace",
    ("live", "up", "to"): "fulfill",
    ("look", "down", "on"): "despise",
    ("look", "forward", "to"): "anticipate",
    ("make", "fun", "of"): "mock",
    ("pay", "attention", "to"): "heed",
    ("run", "away", "from"): "flee",
    ("take", "care", "of"): "handle",
    ("turn", "out", "to", "be"): "prove to be",  # Note: this has 4 words, but included for completeness
}

# Rule 9.2: Restricted verb phrase replacements
# These are combinations of approved words that have restricted meanings
# The value is the recommended replacement
RESTRICTED_VERB_PHRASES = {
    ("go", "down"): "decrease",
    ("go", "up"): "increase",
    ("go", "through"): "pass through",
    ("go", "off"): "explode",
    ("go", "out"): "extinguish",
    ("go", "over"): "review",
}

# Rule 9.2: Restricted word usage patterns
# Structure: {pattern_name: {"base": lemma, "conditions": [...], "replacement": str}}
# Each pattern defines a rule for detecting incorrect word usage
RESTRICTED_WORD_USAGE = {
    # Pattern 1: "go" + preposition/adverb (already handled by RESTRICTED_VERB_PHRASES)
    # Included here for completeness and consistency
    "go_preposition": {
        "base_lemma": "go",
        "conditions": [
            {"type": "next_dep_or", "value": ["prep", "advmod", "prt"]},
            {"type": "next_lemma_or", "value": ["down", "up", "through", "off", "out", "over"]},
        ],
        "replacement_func": "get_restricted_verb_replacement",
    },
    # Pattern 2: "see" + "if"
    "see_if": {
        "base_lemma": "see",
        "conditions": [
            {"type": "next_lemma", "value": "if"},
        ],
        "replacement": "Use 'make sure' instead of 'see if'.",
    },
    # Pattern 3: "wear" + object (not "protective")
    "wear_object": {
        "base_lemma": "wear",
        "conditions": [
            {"type": "next_pos_or", "value": ["NOUN", "DET"]},
            {"type": "not_in_object", "value": "protective"},
        ],
        "replacement": "Use 'put on' or 'use' instead of 'wear'.",
    },
}

# Rule 9.4: Common technical nouns that may need compound forms
# These nouns are commonly used as part of compound technical terms
COMMON_COMPOUND_NOUNS = {
    "body", "head", "base", "main", "top", "bottom",
    "left", "right", "front", "rear", "inner", "outer",
}
