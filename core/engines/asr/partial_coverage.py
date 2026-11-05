"""Explicit unresolved source windows; never silence or completed coverage."""
from copy import deepcopy
import math
import wave


def input_duration(path):
    """Bound child coverage against the actual PCM crop supplied by its owner."""
    with wave.open(str(path), "rb") as source:
        if (source.getcomptype() != "NONE" or source.getnframes() <= 0
                or source.getframerate() != 16000 or source.getnchannels() != 1):
            raise ValueError("Partial source coverage requires a nonempty mono 16 kHz PCM crop")
        return source.getnframes() / source.getframerate()


def validate_coverage(value, duration, rows=()):
    from core.engines.asr.speech_gap_diagnostic import validate_gap_diagnostic
    def require(condition):
        if not condition:
            raise ValueError("Invalid partial source coverage")
    require(type(duration) in (int, float) and math.isfinite(duration) and duration > 0)
    require(isinstance(value, dict) and set(value) == {
        "unresolved_windows", "missing_speech_spans", "diagnostics", "tail_unresolved"})
    def spans(items):
        require(isinstance(items, list) and len(items) <= 1024)
        previous = 0.
        for item in items:
            require(isinstance(item, (tuple, list)) and len(item) == 2)
            require(all(type(number) in (int, float) and math.isfinite(number) for number in item))
            require(previous <= item[0] < item[1] <= duration)
            previous = item[1]
    windows, missing = value["unresolved_windows"], value["missing_speech_spans"]
    spans(windows)
    spans(missing)
    require(bool(windows) == bool(missing))
    require(all(any(left <= start < end <= right for left, right in windows) for start, end in missing))
    require(all(any(left <= start < end <= right for start, end in missing) for left, right in windows))
    require(type(value["tail_unresolved"]) is bool and value["tail_unresolved"] == bool(
        windows and windows[-1][1] >= duration - .05))
    require(isinstance(value["diagnostics"], list) and len(value["diagnostics"]) == len(windows))
    for window, diagnostic in zip(windows, value["diagnostics"]):
        proof = validate_gap_diagnostic(diagnostic)
        require(abs(proof["input_samples"] / proof["sample_rate"] - duration) <= 1 / 16000)
        require(list(proof["window"]) == list(window))
    previous = 0.
    for row in rows:
        start, end = (row["start"], row["end"]) if isinstance(row, dict) else (row.start, row.end)
        require(all(type(number) in (int, float) and math.isfinite(number) for number in (start, end)))
        require(previous <= start < end <= duration)
        require(not any(start < right and end > left for left, right in windows))
        previous = end
        words = row.get("words", []) if isinstance(row, dict) else row.words
        for word in words:
            left, right = (word["start"], word["end"]) if isinstance(word, dict) else (word.start, word.end)
            require(0 <= left <= right <= duration)
            require(not any(left < stop and right > begin for begin, stop in windows))
    return deepcopy(value)
