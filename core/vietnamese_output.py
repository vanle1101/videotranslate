"""Detect foreign-script letters in fields contracted as Vietnamese output.

This is a script check, not language identification or semantic verification.
Latin-script foreign names and untranslated English require contextual review.
Never apply it to source evidence or rewrite a user's text automatically.
"""
import unicodedata


def has_foreign_letters(text):
    # Normalize only the inspection copy: compatibility Latin letters remain
    # readable, while Hangul/Japanese/Thai/etc. cannot masquerade as Vietnamese.
    # U+02BC is a typographic apostrophe in Latin names (NʼDjamena), despite
    # Unicode classifying it as a modifier letter. Other modifier letters can
    # carry foreign-script phonemes and must not be ignored wholesale.
    return any(char != "\u02bc" and unicodedata.category(char).startswith("L")
               and not unicodedata.name(char, "").startswith("LATIN ")
               for char in unicodedata.normalize("NFKC", text))
