#!/usr/bin/env python3
"""
Add a word to the project glossary.

Usage:
    python add_to_glossary.py --key <key> --value <val>
    python add_to_glossary.py --key <key> --remove

Example:
    python add_to_glossary.py --key privilege --remove
    python add_to_glossary.py --key foo --value bar

Namespace and constant name are hardcoded to words/NON_APPROVED_WORDS.
"""
import sys
import argparse
from glossary import add_to_project_glossary


def main():
    parser = argparse.ArgumentParser(description="Add a word to the project glossary.")
    parser.add_argument("--key", required=True, help="The key to add/update (e.g., 'privilege')")
    parser.add_argument("--value", help="The value to set. Use with --remove to set __REMOVE__.")
    parser.add_argument("--remove", action="store_true", help="Remove the key from NON_APPROVED_WORDS (sets value to __REMOVE__)")
    
    args = parser.parse_args()
    
    if args.remove:
        if args.value is not None:
            print("Error: Cannot specify both --value and --remove. Use one or the other.", file=sys.stderr)
            sys.exit(1)
        value = "__REMOVE__"
    elif args.value is not None:
        value = args.value
    else:
        print("Error: Either --value or --remove must be specified.", file=sys.stderr)
        sys.exit(1)
    
    # Hardcoded namespace and name
    namespace = "words"
    name = "NON_APPROVED_WORDS"
    
    # Add the entry to the project glossary
    result = add_to_project_glossary(namespace, name, args.key, value)
    
    if result == 'added':
        if args.remove:
            print(f"Added '{args.key}' to project glossary ({namespace}/{name}) with value '__REMOVE__' to mark it for removal.")
        else:
            print(f"Added '{args.key}' to project glossary ({namespace}/{name}) with value '{value}'.")
    elif result == 'unchanged':
        print(f"Entry '{args.key}' already exists in project glossary with the same value. No changes made.")
    else:
        print("Failed to add entry to project glossary.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
