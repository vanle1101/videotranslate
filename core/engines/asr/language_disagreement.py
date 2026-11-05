"""Bounded independent language hypotheses; never source certification."""
from difflib import SequenceMatcher
import json
import re

SUPPORTED = frozenset({"zh", "ko", "ja", "en", "yue"})


def validate_language_review(value):
    from core.engines.asr.source_language import SUPPORTED_SOURCE_LANGUAGES
    if (not isinstance(value, dict) or value.get("status") != "unresolved"
            or value.get("source_supported") is not False
            or type(value.get("conditional_selected")) is not bool
            or value.get("automatic_language") not in SUPPORTED_SOURCE_LANGUAGES
            or value.get("selected_language") not in SUPPORTED_SOURCE_LANGUAGES
            or type(value.get("automatic_probability")) not in (int, float)
            or not 0 <= value["automatic_probability"] <= 1
            or not isinstance(value.get("independent"), list) or len(value["independent"]) > 2
            or not isinstance(value.get("alternatives"), list) or not 1 <= len(value["alternatives"]) <= 2):
        raise ValueError("Invalid unverified language evidence")
    if len(json.dumps(value, allow_nan=False)) > 150000:
        raise ValueError("Language evidence exceeds bounded utterance size")
    return value


class LocalLanguageReader:
    def __init__(self):
        self.engine = None

    def __call__(self, audio):
        if self.engine is None:
            from core.engines.asr.sensevoice_engine import SenseVoiceEngine
            self.engine = SenseVoiceEngine(language="")
            self.engine._ensure_loaded()
        stream = self.engine.recognizer.create_stream()
        stream.accept_waveform(16000, audio)
        self.engine.recognizer.decode_stream(stream)
        result = stream.result
        return {"language": result.lang.removeprefix("<|").removesuffix("|>"),
                "text": result.text, "tokens": [{"text": t, "at": float(at)}
                    for t, at in zip(result.tokens, result.timestamps)]}


def normalized(text):
    return "".join(c.casefold() for c in text if c.isalnum())


def dominant_script(text, language):
    value = normalized(text)
    patterns = {"zh": r"[\u3400-\u9fff]", "yue": r"[\u3400-\u9fff]",
                "ko": r"[\uac00-\ud7af]", "ja": r"[\u3040-\u30ff\u3400-\u9fff]", "en": r"[a-z]"}
    return len(value) >= 3 and len(re.findall(patterns.get(language, r"(?!)"), value)) / len(value) >= .85


def recover_language(model, audio, start, end, bounds, rows, info, options, reader, *, speech_regions=()):
    """One conditional decode at most, based solely on original-audio evidence."""
    auto_language, probability = info.language, float(info.language_probability)
    proof = {"status": "unresolved", "source_supported": False, "automatic_language": auto_language,
             "automatic_probability": probability, "selected_language": auto_language,
             "conditional_selected": False, "independent": [], "alternatives": []}
    def snapshot(values):
        return [{"start": float(r.start), "end": float(r.end), "text": r.text,
                 "words": [{"word": w.word, "start": float(w.start), "end": float(w.end)} for w in (r.words or [])]}
                for r in values]
    proof["alternatives"].append({"language": auto_language, "kind": "automatic", "rows": snapshot(rows)})
    # Two context variants retain the complete utterance and stop before another
    # measured speech window. Timestamps in each evidence record are crop-relative.
    ranges = [(start, end),
              (max(bounds[0], start - 8000), min(bounds[1], end + 8000))]
    if end - start < 16000:
        proof["reason"] = "insufficient_independent_context"
        return rows, auto_language, probability, proof
    if ranges[0] == ranges[1]:
        # A boundary utterance may have only one complete crop. Preserve that
        # independent reading as evidence, but never use it to force a retry.
        try:
            evidence = reader(audio[ranges[0][0]:ranges[0][1]])
            proof["independent"].append({**evidence, "crop_start": ranges[0][0] / 16000,
                                         "crop_end": ranges[0][1] / 16000})
        except (OSError, RuntimeError, ValueError) as error:
            proof["reason"] = "independent_evidence_unavailable:" + type(error).__name__
            return rows, auto_language, probability, proof
        proof["reason"] = "single_boundary_context_unresolved"
        return rows, auto_language, probability, proof
    try:
        for left, right in ranges:
            evidence = reader(audio[left:right])
            proof["independent"].append({**evidence, "crop_start": left / 16000, "crop_end": right / 16000})
    except (OSError, RuntimeError, ValueError) as error:
        proof["reason"] = "independent_evidence_unavailable:" + type(error).__name__
        return rows, auto_language, probability, proof
    first, second = proof["independent"]
    candidate = first.get("language")
    if (candidate not in SUPPORTED or candidate != second.get("language")
            or not all(dominant_script(e.get("text", ""), candidate) for e in (first, second))):
        proof["reason"] = "independent_language_disagreement"
        return rows, auto_language, probability, proof
    if candidate == auto_language:
        proof["reason"] = "independent_language_agrees_source_unverified"
        return rows, auto_language, probability, proof
    # The explicit language's provider probability=1 is intentionally ignored.
    conditional_options = {**options, "language": candidate, "multilingual": False,
                           "vad_filter": False, "condition_on_previous_text": False}
    conditional_options.pop("vad_parameters", None)
    try:
        generated, _ = model.transcribe(audio[start:end], **conditional_options)
        alternative = list(generated)
    except (RuntimeError, ValueError) as error:
        if "cuda" in str(error).lower() and "memory" in str(error).lower():
            raise
        proof["reason"] = "conditional_decode_unavailable:" + type(error).__name__
        return rows, auto_language, probability, proof
    proof["alternatives"].append({"language": candidate, "kind": "conditional", "rows": snapshot(alternative)})
    def agreement(values):
        text = normalized(" ".join(r.text for r in values))
        return min(SequenceMatcher(None, text, normalized(e["text"])).ratio() for e in (first, second))
    def coverage(values):
        spans = sorted((float(r.start), float(r.end)) for r in values if r.text.strip())
        total = 0.
        cursor = None
        for left, right in spans:
            if cursor is None:
                cursor = [left, right]
            elif left <= cursor[1] + .05:
                cursor[1] = max(cursor[1], right)
            else:
                total += cursor[1] - cursor[0]
                cursor = [left, right]
        return total + (cursor[1] - cursor[0] if cursor else 0.)
    score = agreement(alternative)
    valid = alternative and all(0 <= r.start < r.end <= (end - start) / 16000 + .05 for r in alternative)
    if valid and score >= .55 and score >= agreement(rows) + .2:
        from core.engines.asr.utterance_language import uncovered_speech
        original_gaps = set(uncovered_speech(speech_regions, rows, offset=start / 16000))
        conditional_gaps = set(uncovered_speech(speech_regions, alternative, offset=start / 16000))
        if conditional_gaps - original_gaps:
            proof["reason"] = "conditional_speech_region_loss"
            return rows, auto_language, probability, proof
        if coverage(alternative) + .15 < coverage(rows):
            proof["reason"] = "conditional_coverage_loss"
            return rows, auto_language, probability, proof
        proof.update(selected_language=candidate, conditional_selected=True,
                     reason="independent_language_hypothesis_not_source_verification")
        # Independent recognizer has no calibrated language posterior.
        return alternative, candidate, 0., proof
    proof["reason"] = "conditional_lexical_agreement_insufficient"
    return rows, auto_language, probability, proof
