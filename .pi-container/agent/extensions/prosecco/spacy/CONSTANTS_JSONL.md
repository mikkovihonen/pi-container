# ASD-STE100 Constants JSONL Format

This document describes the JSONL format for ASD-STE100 constants, including namespaces, rule references, and cardinality support.

## Overview

The `asd-ste100_base.jsonl` file contains all ASD-STE100 grammar checking constants in a portable JSONL (JSON Lines) format. Each line is a JSON object representing one constant with metadata about its rule references and data type.

### Benefits

- **Portability**: JSONL can be loaded by any language/tool
- **Modularity**: Load only needed namespaces
- **Extensibility**: Add new configurations without modifying core
- **Traceability**: Each constant links to its STE100 rule
- **Testing**: Easy to swap configurations for A/B testing
- **Documentation**: Rules are embedded in the data

## JSONL Schema

Each line in `asd-ste100_base.jsonl` is a JSON object with this structure:

```json
{
  "namespace": "words",
  "name": "NON_APPROVED_WORDS",
  "rules": ["Rule 1.10"],
  "type": "mapping",
  "data": { ... }
}
```

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `namespace` | string | Grouping category (see Namespaces below) |
| `name` | string | Constant name (e.g., "NON_APPROVED_WORDS") |
| `rules` | array | List of ASD-STE100 rule references |
| `type` | string | Data structure type (mapping, collection, etc.) |
| `data` | any | The actual constant value (normalized to JSON) |

## Namespaces

Constants are grouped into namespaces for modular loading:

| Namespace | Description | Constants |
|-----------|-------------|-----------|
| `words` | Rule 1.x: Technical nouns, verb approvals, false friends | 7 |
| `multiword` | Rule 2.x: Multi-word nouns | 2 |
| `verbs` | Rule 3.x: Verb forms, tenses, passive voice | 7 |
| `sentences` | Rule 4.x: Contractions, connecting words, articles | 2 |
| `procedural` | Rule 5.x: Sentence length, imperatives, notes | 2 |
| `descriptive` | Rule 6.x: Keywords, paragraph structure | 1 |
| `safety` | Rule 7.x: Safety instructions | 3 |
| `punctuation` | Rule 8.x: Punctuation and word count | 6 |
| `writing` | Rule 9.x: Phrasal verbs, word usage, consistent style | 5 |
| `general` | GR-1 to GR-8: General recommendations | 4 |

## Data Types

### Mapping (`type: "mapping"`)

Dictionary with string keys and string values:

```json
{
  "namespace": "words",
  "name": "NON_APPROVED_WORDS",
  "rules": ["Rule 1.10"],
  "type": "mapping",
  "data": {
    "acceptable": "permitted",
    "abundant": "many",
    "absolve": "remove"
  }
}
```

### Collection (`type: "collection"`)

Array of unique items (converted from Python set):

```json
{
  "namespace": "verbs",
  "name": "BE_VERBS",
  "rules": ["Rule 3.5"],
  "type": "collection",
  "data": ["am", "are", "be", "been", "being", "is", "was", "were"]
}
```

### Pattern Mapping (`type: "mapping_tuple_keys"`)

Dictionary with tuple keys (converted to arrays in JSON):

```json
{
  "namespace": "writing",
  "name": "PHRASAL_VERBS",
  "rules": ["Rule 9.3"],
  "type": "mapping_tuple_keys",
  "data": [
    [["add", "up"], "add"],
    [["back", "up"], "backup"],
    [["break", "down"], "stop working"]
  ]
}
```

Note: In this format, each entry is `[key_array, value]` where `key_array` is the tuple converted to an array.

## Cardinality System

The JSONL format supports multiple configurations where one overrides another. This enables:

- Company-specific glossaries
- Project-specific overrides
- Testing with different configurations
- A/B testing of rule interpretations

### Loading Multiple Configurations

```python
from constants_loader import ConstantsLoader

# Load base configuration
loader = ConstantsLoader()
loader.load('asd-ste100_base.jsonl')

# Load override configuration
override = ConstantsLoader()
override.load('company_glossary.jsonl')

# Merge (override wins)
loader.merge(override)

# Get a constant
non_approved = loader.get('words', 'NON_APPROVED_WORDS')
```

### Merge Behavior

- **Mapping types**: Keys from override update keys from base (deep merge)
- **Mapping removal**: Values equal to `"__REMOVE__"` delete the key from base
- **Collection types**: Values from override replace values from base
- **New entries**: Entries in override that don't exist in base are added

### Removing Entries from Base

To remove a key from a mapping in the base configuration, set its value
to `"__REMOVE__"` in the override:

```json
{"namespace":"words","name":"NON_APPROVED_WORDS","rules":["Company Policy"],"type":"mapping","data":{"forbidden_word":"__REMOVE__"}}
```

This is useful when a company vocabulary needs to allow a word that the
base ASD-STE100 configuration marks as forbidden.

### Example Override File

`company_glossary.jsonl`:

```json
{"namespace":"words","name":"NON_APPROVED_WORDS","rules":["Company Policy"],"type":"mapping","data":{"bollocks":"nonsense","bugger":"dammit"}}
{"namespace":"general","name":"FALSE_FRIENDS","rules":["Company Policy"],"type":"mapping","data":{"actually":"currently","current":"present"}}
```

When merged with the base configuration:
- `NON_APPROVED_WORDS` gains 2 new entries (322 → 324)
- `FALSE_FRIENDS` gains 1 new entry ('current') and overrides 'actually'

## ConstantsLoader API

### Loading Constants

```python
from constants_loader import ConstantsLoader

# Create loader
loader = ConstantsLoader()

# Load all constants
loader.load('asd-ste100_base.jsonl')

# Load only specific namespaces
loader.load('asd-ste100_base.jsonl', namespace_filter=['words', 'verbs'])
```

### Accessing Constants

```python
# Get a specific constant
words = loader.get('words', 'NON_APPROVED_WORDS')

# Get all constants in a namespace
all_words = loader.get_all('words')

# Get rule references for a constant
rules = loader.get_rules('words', 'NON_APPROVED_WORDS')
# Returns: ['Rule 1.10']

# Get data type of a constant
const_type = loader.get_type('words', 'NON_APPROVED_WORDS')
# Returns: 'mapping'
```

### Merging Configurations

```python
# Create two loaders
loader1 = ConstantsLoader()
loader1.load('asd-ste100_base.jsonl')

loader2 = ConstantsLoader()
loader2.load('company_glossary.jsonl')

# Merge (loader2 overrides loader1)
loader1.merge(loader2)
```

### Listing Namespaces

```python
# Get all loaded namespaces
namespaces = loader.get_namespaces()
# Returns: ['descriptive', 'general', 'multiword', ...]

# Get all constant names in a namespace
constants = loader.get_constants_in_namespace('words')
# Returns: ['COMMON_COMPOUND_NOUNS', 'NON_APPROVED_WORDS', ...]
```

## Migration from glossary.py

The `glossary.py` file can be kept as a wrapper that loads from JSONL for backward compatibility:

```python
# glossary.py (wrapper)
from constants_loader import ConstantsLoader

_loader = ConstantsLoader()
_loader.load('asd-ste100_base.jsonl')

# Re-export all constants for backward compatibility
NON_APPROVED_WORDS = _loader.get('words', 'NON_APPROVED_WORDS')
PASSIVE_EXCEPTIONS = _loader.get('verbs', 'PASSIVE_EXCEPTIONS')
# ... etc
```

This allows gradual migration without breaking existing imports.

## Example Usage

```python
from constants_loader import ConstantsLoader

# Load base constants
loader = ConstantsLoader()
loader.load('asd-ste100_base.jsonl')

# Load company-specific overrides
loader.merge(ConstantsLoader().load('company_glossary.jsonl'))

# Use in a check function
def check_non_approved_words(doc):
    non_approved = loader.get('words', 'NON_APPROVED_WORDS')
    issues = []
    for token in doc:
        if token.lemma_.lower() in non_approved:
            issues.append(...)
    return issues
```

## Files

- `asd-ste100_base.jsonl` - Base constants (39 entries, 10 namespaces)
- `glossary_loader.py` - Python loader with cardinality support
- `company_glossary.jsonl` - Example override file
- `CONSTANTS_JSONL.md` - This documentation
