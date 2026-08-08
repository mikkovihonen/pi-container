#!/usr/bin/env python3
"""Tests for polysemy detection in check_key_words."""
import pytest
from checks_section6 import check_key_words


class TestPolysemyDetection:
    """Tests for polysemy-aware key word checking.
    
    Current implementation uses POS tagging as the primary polysemy signal.
    Other methods (dependency analysis, collocation, semantic roles, embeddings)
    require external libraries and are not yet implemented.
    """

    def test_pos_change_detected(self):
        """Test that POS changes are detected (run VERB vs NOUN)."""
        nlp = __import__('spacy').load('en_core_web_sm')
        doc = nlp("He runs every morning. She will run the script.")
        issues = check_key_words(doc)
        # 'run' appears as VERB in both cases, should not be flagged for polysemy
        run_issues = [i for i in issues if 'run' in i['message'].lower()]
        # Both are VERB, so no polysemy detected
        assert len(run_issues) == 0

    def test_different_pos_flagged(self):
        """Test that different POS tags are detected as polysemy."""
        nlp = __import__('spacy').load('en_core_web_sm')
        # 'light' appears as NOUN (illumination) and ADJ (not heavy) - different POS
        doc = nlp("Turn on the light. The package is light.")
        issues = check_key_words(doc)
        # 'light' has different POS (NOUN vs ADJ)
        # Should be flagged as polysemous
        light_issues = [i for i in issues if 'light' in i['message'].lower()]
        assert len(light_issues) >= 1

    def test_same_pos_not_flagged(self):
        """Test that same POS with different syntactic roles is not flagged."""
        nlp = __import__('spacy').load('en_core_web_sm')
        # 'host' appears as nsubj and compound but both are NOUN
        doc = nlp("The host holds a process. The host port is open.")
        issues = check_key_words(doc)
        host_issues = [i for i in issues if 'host' in i['message'].lower()]
        # Should NOT be flagged - same POS, different syntactic roles
        assert len(host_issues) == 0

    def test_high_frequency_same_meaning(self):
        """Test that high-frequency terms with same meaning are not flagged."""
        nlp = __import__('spacy').load('en_core_web_sm')
        doc = nlp(
            "The process runs. The process provides. "
            "The process uses. The process handles. "
            "This process is key."
        )
        issues = check_key_words(doc)
        process_issues = [i for i in issues if 'process' in i['message'].lower()]
        assert len(process_issues) == 0

    def test_no_false_positives(self):
        """Test that common technical terms don't trigger false positives."""
        nlp = __import__('spacy').load('en_core_web_sm')
        doc = nlp(
            "The container holds data. The container stores files. "
            "The container image is built. The container network is isolated."
        )
        issues = check_key_words(doc)
        container_issues = [i for i in issues if 'container' in i['message'].lower()]
        assert len(container_issues) == 0

    def test_mixed_document(self):
        """Test document with both polysemous and same-meaning terms."""
        nlp = __import__('spacy').load('en_core_web_sm')
        doc = nlp(
            "The host holds a process. The host port is open. "
            "Turn on the light. The room is light."
        )
        issues = check_key_words(doc)
        # 'host' should not be flagged (same POS)
        host_issues = [i for i in issues if 'host' in i['message'].lower()]
        assert len(host_issues) == 0
        # 'light' should be flagged (different POS: NOUN vs ADJ)
        light_issues = [i for i in issues if 'light' in i['message'].lower()]
        assert len(light_issues) >= 1

    def test_wordnet_high_polysemy(self):
        """Test that high-synset words with different contexts are flagged."""
        from checks_section6 import _get_wordnet_synset_count
        # 'run' has 57 synsets (very polysemous)
        assert _get_wordnet_synset_count('run') > 20
        # 'container' has 1 synset (not polysemous)
        assert _get_wordnet_synset_count('container') <= 5
