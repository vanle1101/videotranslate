"""A single bounded original-audio hypothesis for a split utterance.

Acceptance preserves earlier lexical output and requires measured confident
words at each omitted event. This never certifies the source or non-speech.
"""
import math

from core.engines.asr.language_disagreement import normalized, SUPPORTED


def recover_forward_gap(model, audio, options, start, end, next_end, rows, info, regions, gaps,
                        prior_review=None):
    rate = 16000
    if not gaps or next_end <= end or next_end - start > 12 * rate:
        return None
    if prior_review and prior_review.get("conditional_selected"):
        return None  # Do not flatten an independently selected conditional reading.
    # Only a failure near this crop's right edge benefits from its next window.
    if any(right < end / rate - 1. for _, right in gaps):
        return None
    generated, candidate_info = model.transcribe(audio[start:next_end], **options)
    return _validate_candidate(list(generated), candidate_info, start, next_end, rows, info,
                               regions, gaps, prior_review, "forward_original_context")


def recover_same_window_gap(model, audio, options, start, end, rows, info, regions, gaps,
                            prior_review=None):
    if not gaps or not 0 <= start < end <= len(audio) or end - start > 12 * 16000:
        return None
    if prior_review and prior_review.get("conditional_selected"):
        return None
    local_options = {**options, "language": None, "word_timestamps": True, "vad_filter": False,
                     "multilingual": True, "condition_on_previous_text": False}
    local_options.pop("vad_parameters", None)
    generated, candidate_info = model.transcribe(audio[start:end], **local_options)
    return _validate_candidate(list(generated), candidate_info, start, end, rows, info,
                               regions, gaps, prior_review, "same_window_original_context")


def _finite_times(value):
    return all(type(getattr(value, field, None)) in (int, float)
               and math.isfinite(getattr(value, field)) for field in ("start", "end"))


def _validate_candidate(candidate, candidate_info, start, end, rows, info, regions, gaps,
                        prior_review, kind):
    from core.engines.asr.utterance_language import uncovered_speech
    rate = 16000
    language = getattr(candidate_info, "language", None)
    language_probability = getattr(candidate_info, "language_probability", None)
    if type(language_probability) not in (int, float):
        return None
    if language not in SUPPORTED or not math.isfinite(language_probability) or not .8 <= language_probability <= 1:
        return None
    if any(not isinstance(getattr(row, "text", None), str) for row in [*rows, *candidate]):
        return None
    old_text = normalized(" ".join(row.text for row in rows))
    new_text = normalized(" ".join(row.text for row in candidate))
    if not new_text or (old_text and (info.language != language or old_text not in new_text)):
        return None
    duration = (end - start) / rate
    previous = 0.
    words = []
    for row in candidate:
        if (not row.text.strip() or not _finite_times(row)
                or not previous <= row.start < row.end <= duration + .05):
            return None
        previous = row.end
        row_words = list(getattr(row, "words", None) or [])
        if (any(not isinstance(getattr(w, "word", None), str) for w in row_words)
                or normalized("".join(w.word for w in row_words)) != normalized(row.text)):
            return None
        word_end = row.start
        for word in row_words:
            probability = getattr(word, "probability", None)
            if type(probability) not in (int, float):
                return None
            if (not _finite_times(word) or not math.isfinite(probability)
                    or not word_end <= word.start < word.end <= row.end + .05
                    or not 0 <= probability <= 1):
                return None
            word_end = word.end
            words.append(word)
    # Lexical containment alone can move a retained word to another utterance.
    # Match each prior token, in order, to a measured temporal neighbor.
    position = 0
    retained = set()
    for old_row in rows:
        old_words = list(getattr(old_row, "words", None) or [])
        if (any(not isinstance(getattr(w, "word", None), str) for w in old_words)
                or normalized("".join(w.word for w in old_words)) != normalized(old_row.text)):
            return None
        for old_word in old_words:
            token = normalized(old_word.word)
            if not token:
                continue
            if not _finite_times(old_word) or not 0 <= old_word.start < old_word.end <= duration + .05:
                return None
            match = next((i for i in range(position, len(words))
                if normalized(words[i].word) == token
                and words[i].start < old_word.end + .15
                and words[i].end > old_word.start - .15), None)
            if match is None:
                return None
            retained.add(match)
            position = match + 1
    owned = [r for r in regions if r["start"] >= start and r["end"] <= end]
    if uncovered_speech(owned, candidate, offset=start / rate):
        return None
    for left, right in gaps:
        if not any(i not in retained and w.start + start / rate < right and w.end + start / rate > left
                   and w.probability >= .7 and normalized(w.word) for i, w in enumerate(words)):
            return None
    def snapshot(values):
        return [dict(start=float(r.start), end=float(r.end), text=r.text,
                     words=[dict(word=w.word, start=float(w.start), end=float(w.end))
                            for w in (r.words or [])]) for r in values]
    proof = dict(status="unresolved", source_supported=False,
        automatic_language=info.language, automatic_probability=float(info.language_probability),
        selected_language=language, conditional_selected=False,
        independent=list((prior_review or {}).get("independent", [])),
        alternatives=[dict(kind="automatic", language=info.language, rows=snapshot(rows)),
                      dict(kind=kind, language=language, rows=snapshot(candidate))],
        reason="split_utterance_context_hypothesis_not_source_verification",
        crop_start=start / rate, crop_end=end / rate, timing_origin="source_interval")
    return candidate, candidate_info, proof
