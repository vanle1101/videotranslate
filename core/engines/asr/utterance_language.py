"""Detect spoken language on local speech context, retaining original timing.

Silero boundaries choose crops; no uniform subtitle/word timing is invented.
The local language posterior remains a hypothesis, not transcript verification.
"""
from types import SimpleNamespace
import math


class UnrecognizedSpeechError(RuntimeError):
    def __init__(self, message, spans=(), *, diagnostic=None):
        super().__init__(message)
        self.spans = list(spans)
        self.diagnostic = diagnostic


def uncovered_speech(regions, rows, *, offset=0., rate=16000):
    """Flag a VAD utterance omitted inside a nonempty recognition crop.

    Recognized time overlap is only a necessary coverage check. It cannot prove
    every word was heard, and an absent VAD region never certifies silence.
    """
    covered = [(row.start + offset, row.end + offset) for row in rows if row.text.strip()]
    return [(region["start"] / rate, region["end"] / rate) for region in regions
            if not any(end + .15 >= region["start"] / rate and start - .15 <= region["end"] / rate
                       for start, end in covered)]


def speech_windows(regions, samples, rate=16000):
    """Group neighboring complete VAD regions, without slicing speech at a grid."""
    groups = []
    for region in regions:
        start, end = int(region["start"]), int(region["end"])
        if not 0 <= start < end <= samples:
            raise ValueError("Invalid speech boundary")
        if groups and start < groups[-1][1]:
            raise ValueError("Overlapping speech boundaries")
        if groups and start - groups[-1][1] <= .7 * rate and end - groups[-1][0] <= 12 * rate:
            groups[-1][1] = end
        else:
            groups.append([start, end])
    result = []
    for i, (start, end) in enumerate(groups):
        # Context stays inside the neighboring silence; crops cannot duplicate
        # another utterance. Continuous speech is never arbitrarily cut at 12s.
        left = (groups[i - 1][1] + start) // 2 if i else 0
        right = (end + groups[i + 1][0]) // 2 if i + 1 < len(groups) else samples
        result.append((max(left, start - int(.3 * rate)), min(right, end + int(.3 * rate))))
    return result


def merge_speech_regions(regions):
    merged = []
    for item in sorted(regions, key=lambda r: r["start"]):
        if merged and item["start"] <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], item["end"])
        else:
            merged.append(dict(item))
    return merged


def novel_speech(region, original, rate=16000):
    remaining = region["end"] - region["start"]
    for raw in original:
        remaining -= max(0, min(region["end"], raw["end"]) - max(region["start"], raw["start"]))
    return remaining > .15 * rate


def transcribe_local_speech(model, audio, options, *, regions=None, vocal_audio=None, vocal_regions=None,
                            vocal_spans=None,
                            language_reader=None, coverage_callback=None):
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    from core.engines.asr.speech_gap_diagnostic import VAD_PARAMETERS, build_gap_diagnostic
    rate = 16000
    original_vad_supplied, vocal_vad_supplied = regions is not None, vocal_regions is not None
    if regions is None:
        regions = get_speech_timestamps(audio, VadOptions(**VAD_PARAMETERS))
    original_regions = regions
    if vocal_audio is not None:
        if abs(len(vocal_audio) - len(audio)) > 1:
            raise ValueError("Vocal evidence duration differs from original audio")
        if vocal_regions is None:
            vocal_regions = get_speech_timestamps(vocal_audio, VadOptions(**VAD_PARAMETERS))
        regions = merge_speech_regions([*regions, *vocal_regions])
    else:
        vocal_regions = []  # Supplied regions without their audio are not evidence used by this call.
    all_windows = speech_windows(regions, len(audio), rate)
    windows = all_windows
    if vocal_spans is not None:
        if not isinstance(vocal_spans, (list, tuple)) or not vocal_spans:
            raise ValueError("Focused vocal recognition requires source spans")
        focused = []
        for span in vocal_spans:
            if (not isinstance(span, (list, tuple)) or len(span) != 2
                    or any(type(value) not in (int, float) or not math.isfinite(value) for value in span)
                    or not 0 <= span[0] < span[1] <= len(audio) / rate):
                raise ValueError("Focused vocal recognition span is invalid")
            focused.append((float(span[0]), float(span[1])))
        # Retry workers keep the original PCM for timing and context, but only
        # decode VAD windows that intersect the measured missing speech spans.
        windows = [window for window in all_windows if any(
            window[0] / rate < end and window[1] / rate > start for start, end in focused)]
    summaries = []
    unresolved_windows, missing_spans, diagnostics = [], [], []
    def generate():
        window_index = 0
        while window_index < len(windows):
            start, end = windows[window_index]
            triggers = [r for r in (vocal_regions or []) if vocal_audio is not None
                        and r["start"] >= start and r["end"] <= end and novel_speech(r, original_regions)]
            forced_window = bool(vocal_audio is not None and vocal_spans and any(
                isinstance(span, (list, tuple)) and len(span) == 2
                and start / rate < span[1] and end / rate > span[0]
                for span in vocal_spans))
            use_stem = bool(triggers or forced_window)
            evidence = "vocal_stem_unverified" if use_stem else "original_audio"
            selected_evidence, forward_window = evidence, None
            local_options = {**options, "language": None, "vad_filter": False,
                             "multilingual": True, "condition_on_previous_text": False}
            local_options.pop("vad_parameters", None)
            segments, info = model.transcribe((vocal_audio if use_stem else audio)[start:end], **local_options)
            rows = list(segments)
            owned_regions = [r for r in regions if r["start"] >= start and r["end"] <= end]
            language, probability, review = info.language, float(info.language_probability), None
            if language_reader is not None and (probability < .8 or use_stem):
                from core.engines.asr.language_disagreement import recover_language
                source_window_index = all_windows.index((start, end))
                bounds = (all_windows[source_window_index - 1][1] if source_window_index else 0,
                          all_windows[source_window_index + 1][0]
                          if source_window_index + 1 < len(all_windows) else len(audio))
                rows, language, probability, review = recover_language(model, audio, start, end,
                    bounds, rows, info, local_options, language_reader, speech_regions=owned_regions)
                review.update(crop_start=start / rate, crop_end=end / rate, timing_origin="source_interval")
                if review.get("conditional_selected"):
                    evidence = "original_audio"
            owned_regions = [r for r in regions if r["start"] >= start and r["end"] <= end]
            gaps = uncovered_speech(owned_regions, rows, offset=start / rate, rate=rate)
            recovery_regions = ([*original_regions, *(vocal_regions or [])]
                                if use_stem else regions)
            recovery_owned_regions = [r for r in recovery_regions
                                      if r["start"] >= start and r["end"] <= end]
            recovery_gaps = uncovered_speech(recovery_owned_regions, rows,
                                              offset=start / rate, rate=rate)
            original_only_gap = any(
                any(r["start"] < right * rate and r["end"] > left * rate for r in original_regions)
                and not any(r["start"] < right * rate and r["end"] > left * rate for r in vocal_regions or [])
                for left, right in gaps)
            if (gaps and rows and original_only_gap
                    and (use_stem or coverage_callback is not None)
                    and not (review and review.get("conditional_selected"))):
                # A short, possibly split utterance can be missed by the first
                # decode (or be withheld with the crop's atomic coverage) even
                # when the original audio is usable. Retry that same bounded
                # original-audio context before the forward-context fallback.
                # The helper accepts this only if it preserves every existing
                # word at its measured time and adds a new, confident word over
                # each diagnosed gap.
                from core.engines.asr.gap_context import recover_same_window_gap
                all_regions = recovery_regions
                all_owned = recovery_owned_regions
                all_gaps = list(dict.fromkeys([*gaps, *uncovered_speech(
                    all_owned, rows, offset=start / rate)]))
                recovered = recover_same_window_gap(model, audio, local_options, start, end,
                    rows, SimpleNamespace(language=language, language_probability=probability),
                    all_regions, all_gaps, prior_review=review)
                if recovered is not None:
                    rows, info, review = recovered
                    language, probability, evidence = info.language, 0., "original_audio"
                    gaps = []
            if vocal_spans is None and gaps and window_index + 1 < len(windows):
                from core.engines.asr.gap_context import recover_forward_gap
                next_end = windows[window_index + 1][1]
                forward_window = ((start, next_end)
                                  if recovery_gaps and next_end > end
                                  and next_end - start <= 12 * rate
                                  and not (review and review.get("conditional_selected"))
                                  and not any(right < end / rate - 1. for _, right in recovery_gaps)
                                  else None)
                recovered = recover_forward_gap(model, audio, local_options, start, end, next_end,
                    rows, SimpleNamespace(language=language, language_probability=probability),
                    recovery_regions, recovery_gaps,
                    prior_review=review)
                if recovered is not None:
                    rows, info, review = recovered
                    language, probability, evidence = info.language, 0., "original_audio"
                    end = next_end
                    window_index += 1
                    gaps = []
            empty = not any(row.text.strip() for row in rows)
            if empty or gaps:
                diagnostic = build_gap_diagnostic(original=original_regions, vocal=vocal_regions or [],
                    gaps=gaps, samples=len(audio), rate=rate, window_index=window_index,
                    window=(start, end), selected_evidence=selected_evidence, final_evidence=evidence,
                    triggers=triggers, original_vad_supplied=original_vad_supplied,
                    vocal_vad_supplied=vocal_vad_supplied, forward_window=forward_window)
                # Preserve historical public spans (empty crop includes padding);
                # diagnostic gaps retain the actual VAD triggers inside it.
                if coverage_callback is not None:
                    # The whole measured VAD window is withheld: a rejected
                    # hypothesis cannot establish coverage outside its gap.
                    # Later independent windows still use their original PCM.
                    unresolved_windows.append([start / rate, end / rate])
                    missing_spans.extend([[start / rate, end / rate]] if empty else [list(gap) for gap in gaps])
                    diagnostics.append(diagnostic)
                    window_index += 1
                    continue
                raise UnrecognizedSpeechError(
                    "Detected speech has no native transcript" if empty else "Detected utterance omitted from native transcript",
                    [(start / rate, end / rate)] if empty else gaps, diagnostic=diagnostic)
            summaries.append((end - start, language, probability))
            for row in rows:
                if not row.text.strip():
                    continue
                if not 0 <= row.start < row.end <= (end - start) / rate + .05:
                    raise ValueError("Local ASR timing outside speech crop")
                shift = start / rate
                words = [SimpleNamespace(word=w.word, start=w.start + shift, end=w.end + shift)
                         for w in (row.words or [])]
                yield SimpleNamespace(start=row.start + shift, end=row.end + shift,
                    text=row.text, words=words, source_language=language,
                    source_language_probability=probability, source_audio_evidence=evidence,
                    source_language_review=review)
            window_index += 1
        if coverage_callback is not None:
            merged = []
            for left, right in sorted(missing_spans):
                if merged and left <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], right)
                else:
                    merged.append([left, right])
            coverage_callback({"unresolved_windows": unresolved_windows,
                "missing_speech_spans": merged, "diagnostics": diagnostics,
                "tail_unresolved": bool(unresolved_windows
                    and unresolved_windows[-1][1] >= len(audio) / rate - .05)})
    def metadata():
        # Compatibility metadata only; each row retains its own local posterior.
        if not summaries:
            return SimpleNamespace(language="en", language_probability=0.)
        longest = max(summaries, key=lambda item: item[0])
        return SimpleNamespace(language=longest[1], language_probability=longest[2])
    return generate(), metadata
