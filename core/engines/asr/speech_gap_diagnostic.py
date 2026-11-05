"""Bounded VAD provenance for diagnosis only, never transcript/coverage evidence."""
from copy import deepcopy
import json
import math

MAX_SPANS = 256
MAX_BYTES = 128 * 1024
VAD_PARAMETERS = {"threshold": .35, "min_speech_duration_ms": 100,
                  "min_silence_duration_ms": 200, "speech_pad_ms": 0}


def build_gap_diagnostic(*, original, vocal, gaps, samples, rate, window_index,
                         window, selected_evidence, final_evidence, triggers,
                         original_vad_supplied, vocal_vad_supplied, forward_window=None):
    """All spans are seconds relative to the input PCM, not the video timeline.

    VAD parameters describe production defaults; supplied-region flags mean the
    caller supplied boundaries, whose extraction parameters are not known here.
    Origins mean intersecting VAD triggers, not confirmed lexical speech. Lists
    are bounded independently and report omissions; no diagnostic drives gates.
    """
    def spans(regions):
        return [[int(r["start"]) / rate, int(r["end"]) / rate] for r in regions[:MAX_SPANS]]
    def origins(span):
        return [name for name, regions in (("original_audio", original), ("vocal_stem", vocal))
                if any(r["start"] / rate < span[1] and r["end"] / rate > span[0] for r in regions)]
    value = {
        "version": 1, "timing_origin": "input_audio", "sample_rate": rate,
        "input_samples": samples, "vad_parameters": dict(VAD_PARAMETERS),
        "original_vad_supplied": original_vad_supplied, "vocal_vad_supplied": vocal_vad_supplied,
        "window_index": window_index, "window": [window[0] / rate, window[1] / rate],
        "selected_evidence": selected_evidence, "final_evidence": final_evidence,
        "forward_window": ([forward_window[0] / rate, forward_window[1] / rate]
                           if forward_window else None),
        "original_regions": spans(original), "vocal_regions": spans(vocal),
        "stem_selection_triggers": spans(triggers),
        "gaps": [{"span": list(span), "vad_origins": origins(span)} for span in gaps[:MAX_SPANS]],
        "omitted": {"original_regions": max(0, len(original) - MAX_SPANS),
                    "vocal_regions": max(0, len(vocal) - MAX_SPANS),
                    "stem_selection_triggers": max(0, len(triggers) - MAX_SPANS),
                    "gaps": max(0, len(gaps) - MAX_SPANS)},
    }
    return validate_gap_diagnostic(value)


def validate_gap_diagnostic(value):
    """Reject malformed worker metadata without promoting it to source evidence."""
    keys = {"version", "timing_origin", "sample_rate", "input_samples", "vad_parameters",
            "original_vad_supplied", "vocal_vad_supplied", "window_index", "window",
            "selected_evidence", "final_evidence", "forward_window", "original_regions",
            "vocal_regions", "stem_selection_triggers", "gaps", "omitted"}
    def require(condition):
        if not condition:
            raise ValueError("Invalid source-gap diagnostic")
    require(isinstance(value, dict) and set(value) == keys)
    require(type(value["version"]) is int and value["version"] == 1
            and value["timing_origin"] == "input_audio")
    require(type(value["sample_rate"]) is int and value["sample_rate"] == 16000
            and type(value["input_samples"]) is int and value["input_samples"] > 0)
    require(value["vad_parameters"] == VAD_PARAMETERS)
    require(all(type(value[key]) is bool for key in ("original_vad_supplied", "vocal_vad_supplied")))
    require(type(value["window_index"]) is int and value["window_index"] >= 0)
    duration = value["input_samples"] / value["sample_rate"]
    def span(item):
        require(isinstance(item, (list, tuple)) and len(item) == 2)
        require(all(type(n) in (int, float) and math.isfinite(n) for n in item))
        require(0 <= item[0] < item[1] <= duration)
    span(value["window"])
    if value["forward_window"] is not None:
        span(value["forward_window"])
    for key in ("selected_evidence", "final_evidence"):
        require(value[key] in ("original_audio", "vocal_stem_unverified"))
    lists = ("original_regions", "vocal_regions", "stem_selection_triggers", "gaps")
    for key in lists:
        require(isinstance(value[key], list) and len(value[key]) <= MAX_SPANS)
        for item in value[key]:
            if key == "gaps":
                require(isinstance(item, dict) and set(item) == {"span", "vad_origins"})
                require(item["vad_origins"] in (["original_audio"], ["vocal_stem"],
                                               ["original_audio", "vocal_stem"], []))
                span(item["span"])
            else:
                span(item)
    require(isinstance(value["omitted"], dict) and set(value["omitted"]) == set(lists))
    require(all(type(n) is int and n >= 0 for n in value["omitted"].values()))
    require(len(json.dumps(value, allow_nan=False).encode("utf-8")) <= MAX_BYTES)
    return deepcopy(value)
