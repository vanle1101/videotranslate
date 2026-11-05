"""Stable sentence batches and bounded, read-only neighboring dialogue."""
from core.translation_context import neighbor_context


def sentence_batches(rows, max_targets=8):
    if type(max_targets) is not int or not 1 <= max_targets <= 8:
        raise ValueError("Translation batches support one to eight target sentences")
    ordered = sorted(list(rows), key=lambda row: (row["start"], row["id"]))
    ids = [row["id"] for row in ordered]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate source sentence IDs")
    return [ordered[offset:offset + max_targets] for offset in range(0, len(ordered), max_targets)]
