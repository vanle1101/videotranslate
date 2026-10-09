"""Split ASR rows into complete utterances using measured word timings.

The ASR engine already gives us speech intervals.  This module only decides
where a *measured* row can end: sentence punctuation, a real pause between
words, or a speaker label transition supplied by an upstream diarizer.  It
never creates proportional timestamps and never joins neighbouring ASR rows,
because doing either can put a character's reply in the previous subtitle.
Measured ASR parent/piece provenance is retained for semantic translation;
that provenance alone must never be interpreted as same-speaker evidence.
"""

from __future__ import annotations

import copy
import math
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Sequence


# Characters that unambiguously finish an utterance in Chinese and common
# Latin transcripts.  Commas are deliberately excluded: a short pause after
# a comma is often still the same character's line.
SENTENCE_ENDINGS = frozenset("。！？!?；;….")
_SPEAKER_KEYS = ("speaker", "speaker_id", "diarization_speaker", "spk")


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _word_speaker(word: Dict[str, Any], row: Dict[str, Any]) -> Any:
    for key in _SPEAKER_KEYS:
        value = word.get(key)
        if value is not None and value != "":
            return value
    for key in _SPEAKER_KEYS:
        value = row.get(key)
        if value is not None and value != "":
            return value
    return None


def _has_sentence_end(piece: str) -> bool:
    # Whisper may put a trailing space after punctuation.  Looking at the
    # last non-space character also handles tokens such as "你好。".
    if "\n" in piece or "\r" in piece:
        return True
    stripped = piece.rstrip()
    return bool(stripped) and stripped[-1] in SENTENCE_ENDINGS


def _join_words(words: Sequence[Dict[str, Any]]) -> str:
    """Join Whisper word pieces without inventing spaces in CJK text."""
    text = "".join(str(word.get("word", "")) for word in words)
    text = re.sub(r"\s+", " ", text).strip()
    # Whisper can emit a space before Chinese punctuation or duplicate spaces
    # around punctuation.  Removing only those spaces leaves Latin words
    # readable while preserving the transcript itself.
    text = re.sub(r"\s+([。！？!?；;，、：:…])", r"\1", text)
    text = re.sub(r"([（「『【])\s+", r"\1", text)
    return text


def _copy_row_metadata(row: Dict[str, Any], words: Sequence[Dict[str, Any]], start: float,
                       end: float, text: str, row_id: int, *, piece_index: int = 0,
                       piece_count: int = 1) -> Dict[str, Any]:
    result = copy.deepcopy(row)
    result["id"] = row_id
    result["start"] = round(float(start), 3)
    result["end"] = round(float(end), 3)
    result["duration"] = round(float(end) - float(start), 3)
    result["text_zh"] = text
    result["text"] = text
    result["words"] = [copy.deepcopy(word) for word in words]
    result.setdefault("source_asr_row_id", row.get("id"))
    result.setdefault("source_asr_start", float(row["start"]))
    result.setdefault("source_asr_end", float(row["end"]))
    result["source_piece_index"] = piece_index
    result["source_piece_count"] = piece_count
    if words:
        speaker = _word_speaker(words[0], {})
        if speaker is not None and all(_word_speaker(word, {}) == speaker for word in words):
            inherited = [row.get(key) for key in _SPEAKER_KEYS if row.get(key) not in (None, "")]
            if any(value != speaker for value in inherited):
                # A row-wide speaker/evidence cannot be inherited by the new
                # speaker's piece. Keep aliases consistent but leave proof to
                # an actual upstream diarizer or recorded user confirmation.
                for key in ("speaker_evidence", "speaker_verified", "diarization_verified", "speaker_confidence"):
                    result.pop(key, None)
            result["speaker"] = speaker
            for key in _SPEAKER_KEYS:
                if key in result:
                    result[key] = speaker
    return result


def _valid_words(row: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    raw = row.get("words")
    if not isinstance(raw, list) or not raw:
        return None
    start, end = row.get("start"), row.get("end")
    if not (_number(start) and _number(end) and float(end) > float(start)):
        return None
    words: List[Dict[str, Any]] = []
    previous_end = float(start)
    for word in raw:
        if not isinstance(word, dict) or not isinstance(word.get("word"), str):
            return None
        left, right = word.get("start"), word.get("end")
        if not (_number(left) and _number(right)):
            return None
        left, right = float(left), float(right)
        # Keep an unsplit row when rounded word times overlap or leave its
        # bounds. Otherwise splitting would create rows the pipeline rejects.
        if left < float(start) or right > float(end) or right < left:
            return None
        if words and left < previous_end:
            return None
        if word.get("word", "").strip():
            words.append(word)
        previous_end = max(previous_end, right)
    return words or None


def split_dialogue_segments(rows: Iterable[Dict[str, Any]], pause_threshold: float = 0.30) -> List[Dict[str, Any]]:
    """Split measured ASR rows into natural dialogue lines.

    ``rows`` must contain ``start``, ``end``, and ``text_zh``/``text``.  A row
    is split only when valid ``words`` with real timestamps are present.  Each
    source row is handled independently, so adjacent rows from Whisper are
    never merged.  Output IDs are contiguous in display order and all source
    metadata (including ``speaker`` and ``emotion``) is retained.
    """
    if not _number(pause_threshold) or float(pause_threshold) < 0:
        raise ValueError("pause_threshold phải là số không âm")
    result: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("ASR row phải là object")
        start, end = row.get("start"), row.get("end")
        text = row.get("text_zh", row.get("text", ""))
        if not (_number(start) and _number(end) and float(end) > float(start)):
            raise ValueError("ASR row có mốc thời gian không hợp lệ")
        if not isinstance(text, str):
            raise ValueError("ASR row có nội dung không hợp lệ")
        words = _valid_words(row)
        if words:
            content = lambda value: "".join(char for char in unicodedata.normalize("NFC", value)
                                           if char.isalnum()).casefold()
            if content(_join_words(words)) != content(text):
                # Incomplete word metadata must never replace the transcript
                # with fewer words. Retain the full measured source row.
                words = None
        if words is None or len(words) == 1:
            # Preserve rows without measured boundaries, including one-word
            # answers such as “嗯” and “对”.
            result.append(_copy_row_metadata(row, words or [], float(start), float(end), text.strip(), len(result)))
            continue

        groups: List[List[Dict[str, Any]]] = []
        group: List[Dict[str, Any]] = []
        for index, word in enumerate(words):
            group.append(word)
            next_word = words[index + 1] if index + 1 < len(words) else None
            if next_word is None:
                continue
            gap = float(next_word["start"]) - float(word["end"])
            speaker_changed = (_word_speaker(next_word, row) != _word_speaker(word, row)
                               and (_word_speaker(next_word, row) is not None
                                    or _word_speaker(word, row) is not None))
            boundary = (_has_sentence_end(str(word.get("word", "")))
                        or gap >= float(pause_threshold)
                        or speaker_changed)
            if boundary:
                groups.append(group)
                group = []
        if group:
            groups.append(group)

        for group_index, group_words in enumerate(groups):
            first = group_words[0]
            last = group_words[-1]
            # Keep the source row's outer boundaries on the first/last line;
            # all interior boundaries come directly from measured words.
            group_start = float(start) if group_index == 0 else float(first["start"])
            group_end = float(end) if group_index == len(groups) - 1 else float(last["end"])
            group_text = _join_words(group_words)
            if not group_text:
                continue
            result.append(_copy_row_metadata(row, group_words, group_start, group_end,
                                             group_text, len(result), piece_index=group_index,
                                             piece_count=len(groups)))
    return result


# Concise alias for callers that already use “split ASR” terminology.
split_asr_segments = split_dialogue_segments
split_transcript_segments = split_dialogue_segments


__all__ = ["SENTENCE_ENDINGS", "split_dialogue_segments", "split_asr_segments",
           "split_transcript_segments"]
