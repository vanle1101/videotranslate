"""Conservative ownership of a fresh, padded Chinese ASR reading.

Context can recover a clipped phoneme. It cannot create a source correction:
both recognizers must still return the entire unchanged target transcript.
Boundary words may be excluded only when a supported adjacent source owns them.
"""
import math
import re

from core.chinese_text import comparable_audio_chinese, comparable_chinese

METHOD = "bounded-context-unchanged-source-v1"
# A four-second crop still lost Whisper's phonetic context in the real probe.
# Target <=2s plus these margins stays within an eleven-second native decode.
PADDING = 4.5
WORD_TOLERANCE = .06
MAX_CROSSING = .2
MIN_PROBABILITY = .7


def boundary_reference(neighbor, word, timing, position, before):
    """Match characters, or the entire observed 0–99 age numeral.

    Comparing a lone 八 as '8' to the suffix of 你18 could also accept 你28.
    The numeric exception instead compares the full timed 十八 with full 18.
    """
    source = comparable_chinese(neighbor.get("text_zh", ""))
    literal = comparable_chinese(word)
    if literal and (source.endswith(literal) if before else source.startswith(literal)):
        return {}
    age = re.fullmatch(r"[我你]([0-9]{1,2})", source) if before else None
    if age is None:
        return None
    parts = []
    for item in reversed(timing[:position + 1]):
        text = comparable_chinese(item["text"])
        if (not re.fullmatch(r"[0-9零〇一二三四五六七八九十]+", text)
                or item["probability"] < MIN_PROBABILITY
                or not neighbor["start"] <= item["start"] < item["end"] <= neighbor["end"] + MAX_CROSSING):
            break
        parts.append(text)
    measured = "".join(reversed(parts))
    if measured and comparable_audio_chinese(measured) == comparable_audio_chinese(age[1]):
        return {"text_zh": measured, "source": neighbor["text_zh"], "method": "exact-terminal-age-number"}
    return None


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_reading(record, row, engine):
    """Reject partial/malformed native output before it can enter the cache."""
    if (not isinstance(record, dict) or record.get("event") != "evidence"
            or type(record.get("id")) is not int or record["id"] != row["id"]
            or record.get("engine") != engine or not isinstance(record.get("text"), str)
            or record.get("crop_start") != max(0, row["start"] - PADDING)
            or record.get("crop_end") != row["end"] + PADDING
            or not isinstance(record.get("timing"), list) or len(record["timing"]) > 2000):
        return False
    previous = -1.
    for item in record["timing"]:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            return False
        if engine == "sensevoice":
            at = item.get("at")
            if not number(at) or not record["crop_start"] <= at <= record["crop_end"] + .001:
                return False
        else:
            at, end, probability = (item.get(key) for key in ("start", "end", "probability"))
            if (not all(number(x) for x in (at, end, probability))
                    or not record["crop_start"] <= at <= end <= record["crop_end"] + .001
                    or not 0 <= probability <= 1):
                return False
        if at < previous:
            return False
        previous = at
    # Timed ownership cannot silently omit lexical content from the decode.
    # These are two presentations of the SAME native decode. Compare lexical
    # identity, not cross-engine numeric spelling equivalence: sentence spaces
    # can delimit a spoken numeral in the display text while character-level
    # word timings contain no spaces. Numeric/negation changes remain invalid.
    return comparable_chinese("".join(item["text"] for item in record["timing"])) == comparable_chinese(record["text"])


def scope_reading(record, row, context):
    """Return evidence or None; never remove a disputed target word to fit."""
    engine = record.get("engine") if isinstance(record, dict) else None
    if engine not in {"sensevoice", "faster-whisper-small", "faster-whisper-medium", "faster-whisper-large-v3"} or not validate_reading(record, row, engine):
        return None
    selected, exclusions = [], []
    for position, item in enumerate(record["timing"]):
        word = comparable_chinese(item["text"])
        if not word:
            continue
        if engine == "sensevoice":
            if row["start"] <= item["at"] < row["end"]:
                selected.append(item["text"])
            continue
        start, end = item["start"], item["end"]
        if end <= start:
            # A lexical token with no measured interval cannot prove ownership.
            if row["start"] <= start <= row["end"]:
                return None
            continue
        if start >= row["start"] - WORD_TOLERANCE - 1e-7 and end <= row["end"] + WORD_TOLERANCE + 1e-7:
            if item["probability"] < MIN_PROBABILITY:
                return None
            selected.append(item["text"])
        elif end > row["start"] and start < row["end"]:
            if item["probability"] < MIN_PROBABILITY:
                return None
            before = start < row["start"] and end <= row["start"] + MAX_CROSSING
            after = end > row["end"] and start >= row["end"] - MAX_CROSSING
            owners = []
            for neighbor in context:
                if neighbor.get("id") == row["id"] or not (neighbor.get("verification") or {}).get("source_supported"):
                    continue
                if not all(number(neighbor.get(key)) for key in ("start", "end")):
                    continue
                if ((before and abs(neighbor["end"] - row["start"]) <= .001
                        and neighbor["start"] <= start < neighbor["end"])
                        or (after and abs(neighbor["start"] - row["end"]) <= .001
                            and neighbor["start"] < end <= neighbor["end"])):
                    reference = boundary_reference(neighbor, item["text"], record["timing"], position, before)
                    if reference is not None:
                        owners.append((neighbor, reference))
            if len(owners) != 1:
                return None
            exclusions.append({"id": owners[0][0]["id"], "quote": item["text"], "start": start, "end": end,
                               **owners[0][1]})
    text = "".join(selected).strip()
    expected = comparable_audio_chinese(row.get("text_zh", ""))
    # No contextual source rewrite, empty/silence inference or shortened source.
    if not expected or comparable_audio_chinese(text) != expected:
        return None
    return {"engine": engine, "text_zh": text, "full_text_zh": record["text"],
            "start": row["start"], "end": row["end"], "method": METHOD,
            "source_scope_window": {"start": record["crop_start"], "end": record["crop_end"]},
            "evidence": exclusions}
