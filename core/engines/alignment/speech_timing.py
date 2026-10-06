"""Subtitle timing taken from synthesized speech, with explicit fallback provenance."""
import math
import re
import tempfile
import unicodedata
import wave
from pathlib import Path

import numpy as np

from core.subtitle_cues import build_subtitle_cues


def audio_activity_span(path, *, threshold=.001):
    """Measure first/last audible 10 ms PCM blocks without another media process."""
    try:
        with wave.open(str(path), "rb") as audio:
            if audio.getsampwidth() != 2 or audio.getcomptype() != "NONE":
                return None
            rate, channels = audio.getframerate(), audio.getnchannels()
            samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
        if rate <= 0 or not channels or not len(samples):
            return None
        samples = samples.reshape(-1, channels).astype(np.float64) / 32768.0
        energy = np.mean(samples * samples, axis=1)
        frame = max(1, round(rate * .01))
        starts = np.arange(0, len(energy), frame)
        rms = np.sqrt(np.add.reduceat(energy, starts) / np.minimum(frame, len(energy) - starts))
        # TTS PCM has no soundtrack: a loud vowel must not raise the threshold
        # enough to exclude a quiet opening consonant or final syllable.
        active = np.flatnonzero(rms >= threshold)
        if not len(active):
            return None
        # A single impulse is not speech. Require at least 30 ms of activity.
        if len(active) * frame / rate < .03:
            return None
        return ((active[0] + 1) * frame / rate,
                min(len(energy) / rate, (active[-1] + 1) * frame / rate))
    except (OSError, EOFError, wave.Error, ValueError):
        return None


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


def build_speech_timing(text, start, end, fitted_audio, speed_ratio=1.0, word_boundaries=None):
    """Build absolute display pages for the current spoken sentence.

    Word boundaries are relative to the raw TTS audio. Their first offset is
    calibrated against the final fitted PCM, including encoder silence and any
    atempo adjustment. Voices lacking timestamps get explicitly estimated word
    timing between measured audio onset/offset, never a fictitious exact label.
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
    activity = audio_activity_span(Path(fitted_audio))
    if activity is None:
        return empty
    onset, offset = activity
    speech_start, speech_end = max(0.0, start + onset), min(end, start + offset)
    if speech_end <= speech_start:
        return empty
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
        tokens = _display_tokens(text)
        if not tokens:
            return empty
        weights = [max(1, len(_letters(token))) for token in tokens]
        total, elapsed = sum(weights), 0
        for token, weight in zip(tokens, weights):
            a = speech_start + (speech_end - speech_start) * elapsed / total
            elapsed += weight
            b = speech_start + (speech_end - speech_start) * elapsed / total
            words.append({"text": token, "start": a, "end": b})
    # A complete current sentence appears when its first spoken word starts;
    # the next sentence must never be combined into that earlier display page.
    # This is ordinary subtitle paging, not karaoke/progressive word reveal.
    sentences = (re.split(r'(?<=[.!?。！？…])\s+', str(text).strip())
                 if "\n" not in str(text) else [text])
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
    # Keep each page visible through short pauses until the next page starts.
    for left, right in zip(cues, cues[1:]):
        left["end"] = right["start"]
    if cues:
        cues[-1]["end"] = speech_end
    return {"subtitle_cues": cues, "subtitle_timing_source": source,
            "speech_start": speech_start, "speech_end": speech_end}
