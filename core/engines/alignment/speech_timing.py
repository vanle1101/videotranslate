"""Subtitle timing taken from synthesized speech, with explicit fallback provenance."""
import math
import re
import tempfile
import unicodedata
import wave
from pathlib import Path

import numpy as np

from core.subtitle_cues import build_subtitle_cues


def _audio_activity_intervals(path, *, threshold=.001, min_pause=.25):
    """Measure speech islands, merging brief phoneme gaps but retaining pauses."""
    try:
        with wave.open(str(path), "rb") as audio:
            if audio.getsampwidth() != 2 or audio.getcomptype() != "NONE":
                return []
            rate, channels = audio.getframerate(), audio.getnchannels()
            samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
        if rate <= 0 or not channels or not len(samples):
            return []
        samples = samples.reshape(-1, channels).astype(np.float64) / 32768.0
        energy = np.mean(samples * samples, axis=1)
        frame = max(1, round(rate * .01))
        starts = np.arange(0, len(energy), frame)
        rms = np.sqrt(np.add.reduceat(energy, starts) / np.minimum(frame, len(energy) - starts))
        # TTS PCM has no soundtrack: a loud vowel must not raise the threshold
        # enough to exclude a quiet opening consonant or final syllable.
        active = np.flatnonzero(rms >= threshold)
        if not len(active):
            return []
        # A single impulse is not speech. Require at least 30 ms of activity.
        if len(active) * frame / rate < .03:
            return []
        groups = np.split(active, np.flatnonzero((np.diff(active) - 1) * frame / rate >= min_pause) + 1)
        return [((group[0] + 1) * frame / rate,
                 min(len(energy) / rate, (group[-1] + 1) * frame / rate))
                for group in groups if len(group) * frame / rate >= .03]
    except (OSError, EOFError, wave.Error, ValueError):
        return []


def audio_activity_span(path, *, threshold=.001):
    """Measure outer audible bounds without treating an isolated click as speech."""
    intervals = _audio_activity_intervals(path, threshold=threshold)
    return (intervals[0][0], intervals[-1][1]) if intervals else None


def take_tts_word_boundaries(engine, path):
    """Optional engine capability; old/local engines need no API changes."""
    take = getattr(engine, "take_word_boundaries", None)
    result = take(path) if callable(take) else []
    return result if isinstance(result, list) else []


def trim_tts_padding(path):
    """Remove only outer synthesis silence so short replies aren't sped up for it."""
    # Destructive trimming uses a lower threshold than caption onset: retain
    # quiet phonemes and breathing even when they are below subtitle detection.
    activity = audio_activity_span(path, threshold=.0001)
    if activity is None:
        return 0.0
    try:
        with wave.open(str(path), "rb") as audio:
            params = audio.getparams()
            rate = audio.getframerate()
            left = max(0, round((activity[0] - .04) * rate))
            right = min(audio.getnframes(), round((activity[1] + .06) * rate))
            if left == 0 and right == audio.getnframes():
                return 0.0
            audio.setpos(left)
            frames = audio.readframes(right - left)
    except (OSError, EOFError, wave.Error, ValueError):
        return 0.0
    # Publish only a complete PCM file. An I/O failure must not silently corrupt
    # the source and continue alignment with a partially written wave header.
    with tempfile.TemporaryDirectory(prefix="tts-trim-", dir=Path(path).parent) as directory:
        trimmed = Path(directory) / "speech.wav"
        with wave.open(str(trimmed), "wb") as audio:
            audio.setparams(params)
            audio.writeframes(frames)
        trimmed.replace(path)
    return left / rate


def _letters(text):
    return "".join(char for char in unicodedata.normalize("NFC", str(text)).casefold()
                   if char.isalnum())


def _display_tokens(text):
    tokens, prefix = [], []
    for token in re.findall(r"\S+", str(text)):
        if not _letters(token):
            if tokens:
                tokens[-1] += " " + token
            else:
                prefix.append(token)
        else:
            tokens.append(" ".join([*prefix, token]))
            prefix = []
    return tokens


def _match_boundaries(text, boundaries):
    """Preserve edited punctuation while checking every spoken token matches."""
    tokens = _display_tokens(text)
    if not tokens or not boundaries:
        return []
    spans, cursor, previous = [], 0, -1.0
    for item in boundaries:
        try:
            start, end = float(item["start"]), float(item["end"])
            letters = _letters(item["text"])
        except (KeyError, TypeError, ValueError):
            return []
        if not letters or not all(math.isfinite(value) for value in (start, end)):
            return []
        if start < 0 or end <= start or start < previous:
            return []
        previous = start
        spans.append((cursor, cursor + len(letters), start, end))
        cursor += len(letters)
    if "".join(_letters(item["text"]) for item in boundaries) != _letters(" ".join(tokens)):
        # Number normalization or incomplete metadata must not pretend to have
        # exact word alignment. The caller will label measured-onset estimation.
        return []
    result, cursor = [], 0
    for token in tokens:
        size = len(_letters(token))
        matching = [span for span in spans if span[0] < cursor + size and span[1] > cursor]
        if not matching:
            if result and not size:
                result[-1]["text"] += " " + token
                continue
            return []
        result.append({"text": token, "start": matching[0][2], "end": matching[-1][3]})
        cursor += size
    return result


def _estimate_tokens(tokens, intervals):
    """Map estimated speaking time onto audible spans, excluding long silence."""
    durations = [right - left for left, right in intervals]
    duration = sum(durations)
    weights = [max(1, len(_letters(token))) for token in tokens]
    total, elapsed = sum(weights), 0

    def at_time(position, *, onset):
        for (left, right), length in zip(intervals, durations):
            # A word beginning exactly at a pause belongs to the next audible
            # span; a word ending there belongs to the previous span.
            if position < length - 1e-9 or (not onset and position <= length + 1e-9):
                return min(right, left + max(0, position))
            position -= length
        return intervals[-1][1]

    words = []
    for token, weight in zip(tokens, weights):
        left = at_time(duration * elapsed / total, onset=True)
        elapsed += weight
        right = at_time(duration * elapsed / total, onset=False)
        words.append({"text": token, "start": left, "end": max(left, right)})
    return words


def _estimate_speech_words(text, sentences, intervals):
    """Use real pauses to separate sentences; within each span timing is estimated.

    This is not forced alignment or speaker identification. When there are at
    least as many audible spans as sentences and their speaking budgets agree,
    assign each sentence at least one span in order. Extra spans are divided
    near proportional speaking budgets.
    Otherwise retain estimated token timing over audible time only.
    """
    groups = [_display_tokens(sentence) for sentence in sentences]
    groups = [group for group in groups if group]
    if len(groups) < 2 or len(intervals) < len(groups):
        return _estimate_tokens(_display_tokens(text), intervals)
    # Vietnamese whitespace tokens are mostly syllables. Character counts can
    # make a short reply such as "Ừ" look far shorter than it is when spoken.
    weights = [len(group) for group in groups]
    cumulative = [0.0]
    for left, right in intervals:
        cumulative.append(cumulative[-1] + right - left)
    total_weight, elapsed, cursor, assigned = sum(weights), 0, 0, []
    for index, (tokens, weight) in enumerate(zip(groups, weights)):
        elapsed += weight
        remaining = len(groups) - index - 1
        if remaining:
            target = cumulative[-1] * elapsed / total_weight
            boundary = min(range(cursor + 1, len(intervals) - remaining + 1),
                           key=lambda cut: abs(cumulative[cut] - target))
            if abs(cumulative[boundary] - target) > max(.20, cumulative[-1] * .20):
                # A pause after "Nào," is not evidence that the whole first
                # sentence finished. Do not force one sentence onto each island
                # when their speaking budgets disagree; retain the estimate.
                return _estimate_tokens(_display_tokens(text), intervals)
        else:
            boundary = len(intervals)
        assigned.append((tokens, intervals[cursor:boundary]))
        cursor = boundary
    words = []
    for tokens, spans in assigned:
        words.extend(_estimate_tokens(tokens, spans))
    return words


def build_speech_timing(text, start, end, fitted_audio, speed_ratio=1.0, word_boundaries=None):
    """Build absolute display pages for the current spoken sentence.

    Word boundaries are relative to the raw TTS audio. Their first offset is
    calibrated against the final fitted PCM, including encoder silence and any
    atempo adjustment. Voices lacking timestamps get explicitly estimated word
    timing over measured audible spans, never a fictitious exact label.
    """
    empty = {"subtitle_cues": [], "subtitle_timing_source": "unavailable",
             "speech_start": None, "speech_end": None}
    try:
        start, end, speed_ratio = float(start), float(end), float(speed_ratio)
    except (TypeError, ValueError):
        return empty
    if not all(math.isfinite(value) for value in (start, end, speed_ratio)) or end <= start or speed_ratio <= 0:
        return empty
    if not str(text or "").strip():
        return empty
    activity = _audio_activity_intervals(Path(fitted_audio))
    if not activity:
        return empty
    onset, offset = activity[0][0], activity[-1][1]
    speech_start, speech_end = max(0.0, start + onset), min(end, start + offset)
    if speech_end <= speech_start:
        return empty
    intervals = [(max(speech_start, start + left), min(speech_end, start + right))
                 for left, right in activity if start + right > speech_start and start + left < speech_end]
    sentences = re.split(r'(?<=[.!?。！？…])\s+', str(text).strip())
    words = _match_boundaries(text, word_boundaries or [])
    source = "edge-word-boundary" if words else "audio-onset-estimate"
    if words:
        first = words[0]["start"]
        for word in words:
            word["start"] = min(speech_end, max(speech_start, speech_start + (word["start"] - first) / speed_ratio))
            word["end"] = min(speech_end, max(word["start"], speech_start + (word["end"] - first) / speed_ratio))
        # Discard metadata that cannot fit the actual audio instead of losing
        # its last tokens. Keep the fallback's uncertainty visible to clients.
        if any(word["end"] <= word["start"] for word in words):
            words, source = [], "audio-onset-estimate"
    if not words:
        if not _display_tokens(text):
            return empty
        words = _estimate_speech_words(text, sentences, intervals)
        source = "audio-pause-estimate" if len(intervals) > 1 else "audio-onset-estimate"
    # A complete current sentence appears when its first spoken word starts;
    # the next sentence must never be combined into that earlier display page.
    # This is ordinary subtitle paging, not karaoke/progressive word reveal.
    pages = [page for sentence in sentences
             for page in build_subtitle_cues(sentence, speech_start, speech_end,
                                              max_chars=48, line_chars=26)]
    cursor = 0
    cues = []
    for page in pages:
        # Standalone punctuation is attached to a neighbouring word by the
        # matcher. Counting whitespace tokens would steal the next sentence's
        # first word and could drop its final cue altogether ("Ừ . Sao vậy?").
        letters, needed, group = 0, len(_letters(page["text"])), []
        while cursor < len(words) and letters < needed:
            word = words[cursor]
            group.append(word)
            letters += len(_letters(word["text"]))
            cursor += 1
        if not group:
            continue
        cues.append({"text": page["text"], "start": group[0]["start"],
                     "end": group[-1]["end"], "words": group})
    # Keep the current page through short pauses only. Long pauses should not
    # leave stale words on screen or reveal the next sentence before its voice.
    for left, right in zip(cues, cues[1:]):
        if right["start"] - left["end"] < .25:
            left["end"] = right["start"]
    if cues:
        cues[-1]["end"] = speech_end
    if len(intervals) > 1:
        # One sentence can also contain a long dramatic pause. Keep the same
        # page/geometry on either side, but hide it during measured silence.
        # Ordinary comma pauses must not make the entire caption blink.
        display_intervals = []
        for left, right in intervals:
            if display_intervals and left - display_intervals[-1][1] < .6:
                display_intervals[-1] = (display_intervals[-1][0], right)
            else:
                display_intervals.append((left, right))
        clipped = []
        for cue in cues:
            assigned_words = set()
            for left, right in display_intervals:
                left, right = max(cue["start"], left), min(cue["end"], right)
                if right > left:
                    visible_words = []
                    for index, word in enumerate(cue["words"]):
                        if index not in assigned_words and word["start"] < right and word["end"] > left:
                            visible_words.append({**word, "start": max(left, word["start"]),
                                                  "end": min(right, word["end"])})
                            assigned_words.add(index)
                    clipped.append({**cue, "start": left, "end": right,
                                    "words": visible_words})
        cues = clipped
    return {"subtitle_cues": cues, "subtitle_timing_source": source,
            "speech_start": speech_start, "speech_end": speech_end}
