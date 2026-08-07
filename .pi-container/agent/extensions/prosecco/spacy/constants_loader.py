#!/usr/bin/env python3
"""
ASD-STE100 Constants Loader with Namespace and Cardinality Support.

Loads constants from JSONL files with support for:
- Namespace-based modular loading
- Rule reference tracking
- Cardinality (one configuration overrides another)
- Multiple configuration files

Example usage:
    loader = ConstantsLoader('constants.jsonl')
    loader.load('company_glossary.jsonl')  # Override with company config
    
    # Get a specific constant
    words = loader.get('words', 'NON_APPROVED_WORDS')
    
    # Get all constants in a namespace
    all_words = loader.get_all('words')
"""
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


class ConstantsLoader:
    """
    Load and manage ASD-STE100 constants from JSONL files.
    
    Supports namespace-based loading and cardinality where later
    configurations override earlier ones.
    """
    
    def __init__(self, base_path: Optional[str] = None):
        """
        Initialize the loader.
        
        Args:
            base_path: Optional base path for JSONL files (defaults to current directory)
        """
        self.base_path = base_path or os.getcwd()
        self.configs: Dict[str, Dict[str, Any]] = {}
        self.metadata: Dict[str, Dict[str, Any]] = {}
        self._current_filter: Optional[List[str]] = None
    
    def load(self, path: str, namespace_filter: Optional[List[str]] = None):
        """
        Load constants from a JSONL file.
        
        Args:
            path: Path to the JSONL file
            namespace_filter: Optional list of namespaces to load
        """
        full_path = os.path.join(self.base_path, path) if not os.path.isabs(path) else path
        self._current_filter = namespace_filter
        
        with open(full_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                
                try:
                    obj = json.loads(line)
                    self._process_entry(obj, line_num)
                except json.JSONDecodeError as e:
                    print(f"Warning: Invalid JSON on line {line_num} in {path}: {e}")
        
        self._current_filter = None
    
    def _process_entry(self, obj: Dict[str, Any], line_num: int):
        """Process a single JSONL entry."""
        namespace = obj.get('namespace', 'general')
        name = obj.get('name')
        
        if not name:
            print(f"Warning: Entry on line {line_num} missing 'name' field")
            return
        
        # Apply namespace filter if specified
        if self._current_filter and namespace not in self._current_filter:
            return
        
        # Store the data (later entries override earlier ones)
        self.configs.setdefault(namespace, {})[name] = obj.get('data', {})
        
        # Store metadata
        self.metadata.setdefault(namespace, {})[name] = {
            'rules': obj.get('rules', []),
            'type': obj.get('type', 'unknown'),
            'line': line_num,
        }
    
    def get(self, namespace: str, name: str) -> Any:
        """
        Get a specific constant.
        
        Args:
            namespace: The namespace (e.g., 'words', 'verbs')
            name: The constant name (e.g., 'NON_APPROVED_WORDS')
            
        Returns:
            The constant value, or None if not found
        """
        return self.configs.get(namespace, {}).get(name)
    
    def get_all(self, namespace: str) -> Dict[str, Any]:
        """
        Get all constants in a namespace.
        
        Args:
            namespace: The namespace to retrieve
            
        Returns:
            Dictionary of all constants in the namespace
        """
        return self.configs.get(namespace, {}).copy()
    
    def get_rules(self, namespace: str, name: str) -> List[str]:
        """
        Get the STE100 rule references for a constant.
        
        Args:
            namespace: The namespace
            name: The constant name
            
        Returns:
            List of rule references (e.g., ['Rule 1.10', 'GR-5'])
        """
        return self.metadata.get(namespace, {}).get(name, {}).get('rules', [])
    
    def get_type(self, namespace: str, name: str) -> str:
        """
        Get the data type of a constant.
        
        Args:
            namespace: The namespace
            name: The constant name
            
        Returns:
            Type string (e.g., 'mapping', 'collection')
        """
        return self.metadata.get(namespace, {}).get(name, {}).get('type', 'unknown')
    
    def get_namespaces(self) -> List[str]:
        """
        Get all loaded namespaces.
        
        Returns:
            List of namespace names
        """
        return sorted(self.configs.keys())
    
    def get_constants_in_namespace(self, namespace: str) -> List[str]:
        """
        Get all constant names in a namespace.
        
        Args:
            namespace: The namespace
            
        Returns:
            List of constant names
        """
        return sorted(self.configs.get(namespace, {}).keys())
    
    def merge(self, other: 'ConstantsLoader'):
        """
        Merge another loader's configuration (other overrides self).
        
        This performs a deep merge where:
        - For mapping types: keys from other override keys from self
        - For collection types: values from other replace values from self
        
        Args:
            other: Another ConstantsLoader instance
        """
        for namespace, consts in other.configs.items():
            self_ns = self.configs.setdefault(namespace, {})
            for name, value in consts.items():
                if name in self_ns and isinstance(self_ns[name], dict) and isinstance(value, dict):
                    # Deep merge for mappings
                    self_ns[name].update(value)
                else:
                    # Replace for other types
                    self_ns[name] = value
        
        for namespace, meta in other.metadata.items():
            self.metadata.setdefault(namespace, {}).update(meta)
    
    def load_override(self, override_path: str):
        """
        Load an override configuration file.
        
        This is a convenience method that loads the override and merges it
        with the current configuration, allowing later configurations to
        override earlier ones.
        
        Args:
            override_path: Path to the override JSONL file
        """
        # Save current state
        saved_configs = self.configs.copy()
        saved_metadata = self.metadata.copy()
        
        # Clear and reload
        self.configs = {}
        self.metadata = {}
        
        # Load base configuration
        # (Assuming base was loaded first)
        
        # Load override
        self.load(override_path)
        
        # Merge: base + override (override wins)
        override_configs = self.configs.copy()
        
        # Reset and merge
        self.configs = {}
        self.metadata = {}
        
        for ns, consts in saved_configs.items():
            self.configs.setdefault(ns, {}).update(consts)
        
        for ns, consts in override_configs.items():
            self.configs.setdefault(ns, {}).update(consts)
    
    def __repr__(self):
        """String representation."""
        namespaces = ', '.join(self.get_namespaces())
        total = sum(len(consts) for consts in self.configs.values())
        return f"ConstantsLoader(namespaces=[{namespaces}], constants={total})"


# Global loader instance for convenience
_default_loader: Optional[ConstantsLoader] = None


def get_default_loader() -> ConstantsLoader:
    """
    Get the default loader instance.
    
    Returns:
        The default ConstantsLoader instance
    """
    global _default_loader
    if _default_loader is None:
        _default_loader = ConstantsLoader()
    return _default_loader


def load_constants(path: str, namespace_filter: Optional[List[str]] = None) -> ConstantsLoader:
    """
    Convenience function to load constants from a file.
    
    Args:
        path: Path to the JSONL file
        namespace_filter: Optional list of namespaces to load
        
    Returns:
        Loaded ConstantsLoader instance
    """
    loader = ConstantsLoader()
    loader.load(path, namespace_filter)
    return loader
