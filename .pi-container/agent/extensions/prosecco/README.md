# Prosecco Extension

The Prosecco extension combines Vale and spaCy for comprehensive prose quality checking.

## Overview

This extension provides:
- **Tool**: `prosecco-lint` — Run Vale + spaCy on a file or directory
- **Command**: `/prosecco` — Lint prose with Vale + spaCy

## Checks

### Vale (General Prose)
- Spelling
- Capitalization
- Consistency
- Repetition
- And more via Vale's rule system

### spaCy (ASD-STE100)
- **Contractions** — Detects contracted verbs (doesn't → does not)
- **Passive voice** — Detects passive constructions via dependency parsing
- **-ing forms** — Detects be + present participle
- **Sentence length** — Accurate word counting via sentence segmentation
- **Forbidden modals** — Detects shall/should/may via POS tagging

## Usage

### Tool: `prosecco-lint`

```javascript
prosecco-lint({
  path: "file.md",
  spacy: true,           // Enable ASD-STE100 checks (default: false)
  steOnly: false,        // Use only STE100 rules (default: false)
  minAlertLevel: "warning",  // suggestion | warning | error
  outputFormat: "text"   // text | json
})
```

### Command: `/prosecco`

```bash
/prosecco file.md --spacy --minAlertLevel=warning
```

## Examples

### Basic linting (Vale only)
```javascript
prosecco-lint({ path: "README.md" })
```

### With spaCy ASD-STE100 checks
```javascript
prosecco-lint({
  path: "README.md",
  spacy: true
})
```

### STE100 only (no general Vale checks)
```javascript
prosecco-lint({
  path: "README.md",
  steOnly: true,
  spacy: true
})
```

## Architecture

```
Pi Coding Agent
    ↓
Prose Lint Extension
    ↓
    ├── Vale CLI (general prose checks)
    └── spaCy scripts (NLP-powered ASD-STE100 checks)
    ↓
Unified output
```

## Benefits

1. **Comprehensive checks** — Combines general prose quality with technical English standards
2. **No HTTP overhead** — Direct Python execution for spaCy
3. **Unified output** — Results from both tools in one place
4. **Flexible** — Enable/disable Vale or spaCy as needed
5. **Accurate NLP** — True dependency parsing and POS tagging via spaCy

## Files

- `index.js` — Extension implementation
- `../.vale/styles/SpacyChecks/spacy_direct.py` — Python spaCy checker
- `../.vale/styles/SpacyChecks/spacy_ste_check.sh` — Shell wrapper

## Dependencies

- Vale CLI (installed with extension)
- spaCy 3.x (Python)
- en_core_web_sm model (Python)

Install spaCy:
```bash
pip install spacy
python -m spacy download en_core_web_sm
```

## Notes

- The `spacy` parameter is optional. Set to `true` to enable ASD-STE100 checks.
- If spaCy is not installed, the extension continues with Vale-only results.
- The extension gracefully handles failures in either tool.
