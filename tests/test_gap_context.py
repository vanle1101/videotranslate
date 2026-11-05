from types import SimpleNamespace as NS
import numpy as np
import pytest

from core.engines.asr.gap_context import recover_forward_gap, recover_same_window_gap
from core.engines.asr.utterance_language import transcribe_local_speech, UnrecognizedSpeechError


def row(start, end, text, probability=.95):
    return NS(start=start, end=end, text=text,
              words=[NS(start=start, end=end, word=text, probability=probability)])


REGIONS = [{"start": 16000, "end": 32000}, {"start": 40000, "end": 43200},
           {"start": 64000, "end": 80000}]


@pytest.mark.parametrize("failure", [None, "low_word", "lost_text", "overlap", "low_language", "no_gap_word",
                                     "missing_confidence", "nan_language", "moved_text"])
def test_context_requires_preserved_words_and_real_gap_alignment(failure):
    calls = []
    class Model:
        def transcribe(self, audio, **options):
            calls.append(len(audio))
            values = [row(.3, 1.3, "hello"), row(1.8, 2., "there"), row(3.3, 4.3, "again")]
            if failure == "low_word": values[1].words[0].probability = .2
            if failure == "lost_text": values[0] = row(.3, 1.3, "changed")
            if failure == "overlap": values[2].start = 1.9
            if failure == "no_gap_word": values[1] = row(2.1, 2.3, "there")
            if failure == "missing_confidence": del values[1].words[0].probability
            if failure == "moved_text": values = [row(.3,1.3,"changed"), values[1],row(3.3,4.3,"hello")]
            probability = float("nan") if failure == "nan_language" else .2 if failure == "low_language" else .95
            return iter(values), NS(language="en", language_probability=probability)
    value = recover_forward_gap(Model(), np.zeros(96000), {}, 11200, 48000, 84800,
        [row(.3, 1.3, "hello")], NS(language="en", language_probability=.95), REGIONS, [(2.5, 2.7)])
    assert (value is not None) == (failure is None)
    assert len(calls) == 1
    if value:
        from core.engines.asr.language_disagreement import validate_language_review
        from core.streaming.session_store import _clean_segment_field
        assert validate_language_review(value[2])["source_supported"] is False
        assert _clean_segment_field("source_language_review", value[2]) == value[2]


@pytest.mark.parametrize("end,next_end,gaps", [(48000,240000,[(2.5,2.7)]),
    (80000,120000,[(1.,1.5)])])
def test_context_budget_and_interior_gap_do_not_decode(end, next_end, gaps):
    assert recover_forward_gap(object(), np.zeros(250000), {}, 0, end, next_end,
        [], NS(language="en",language_probability=.9), REGIONS, gaps) is None


def test_conditional_reading_is_not_flattened_by_context_fallback():
    assert recover_forward_gap(object(), np.zeros(96000), {}, 11200,48000,84800,
        [], NS(language="en",language_probability=.9),REGIONS,[(2.5,2.7)],
        prior_review={"conditional_selected":True}) is None


def test_combined_window_publishes_once_with_measured_offset_and_uncertainty():
    calls = []
    class Model:
        def transcribe(self, audio, **options):
            calls.append(len(audio))
            assert options["language"] is None
            if len(calls) == 1:
                values = [row(.3, 1.3, "hello")]
            else:
                values = [row(.3, 1.3, "hello"), row(1.8, 2., "there"), row(3.3, 4.3, "again")]
            return iter(values), NS(language="en", language_probability=.95)
    rows, metadata = transcribe_local_speech(Model(), np.zeros(96000), {}, regions=REGIONS)
    values = list(rows)
    assert len(calls) == 2  # The next window is owned by the combined decode.
    assert [r.text for r in values] == ["hello", "there", "again"]
    assert values[1].start == pytest.approx(2.5)
    assert all(r.source_language_probability == 0. for r in values)
    assert metadata().language_probability == 0.
    assert all(r.source_audio_evidence == "original_audio" for r in values)
    assert all(r.source_language_review["source_supported"] is False for r in values)


def test_original_audio_gap_gets_confident_same_window_retry_before_coverage_blocks():
    calls = []

    class Model:
        def transcribe(self, audio, **options):
            calls.append((len(audio), options))
            decoded = [row(.3, 1.3, "hello")]
            if len(calls) > 1:
                decoded.append(row(1.8, 2., "there"))
            return iter(decoded), NS(language="en", language_probability=.95)

    coverage = {}
    generated, _ = transcribe_local_speech(Model(), np.zeros(80000), {},
        regions=[{"start": 16000, "end": 32000}, {"start": 40000, "end": 43200}],
        coverage_callback=coverage.update)
    generated = list(generated)

    assert [value.text for value in generated] == ["hello", "there"]
    assert len(calls) == 2
    assert calls[1][1]["word_timestamps"] is True
    assert calls[1][1]["language"] is None
    assert coverage["unresolved_windows"] == []
    assert coverage["missing_speech_spans"] == []
    assert generated[1].start == pytest.approx(2.5)
    assert generated[1].source_audio_evidence == "original_audio"
    assert generated[1].source_language_review["reason"] == (
        "split_utterance_context_hypothesis_not_source_verification")


@pytest.mark.parametrize("failure", [None, "omitted_old", "moved_old", "weak_new", "unsupported_language",
    "uncertain_language", "nan_language", "missing_language", "missing_words", "missing_timing",
    "missing_probability", "nan_word", "invalid_probability", "bool_time", "outside_crop",
    "leftover_original", "leftover_vocal", "no_new_word", "partial_old_words"])
def test_same_window_candidate_preserves_words_and_closes_every_measured_gap(failure):
    calls = []
    old = [row(.3, 1.3, "hello")]
    regions = [{"start": 16000, "end": 32000}, {"start": 40000, "end": 43200}]
    if failure in ("leftover_original", "leftover_vocal"):
        regions.append({"start": 56000, "end": 59200})
    if failure == "partial_old_words":
        old[0].text = "hello there"
    class Model:
        def transcribe(self, audio, **options):
            calls.append((len(audio), options))
            candidate = [row(.3, 1.3, "hello"), row(1.8, 2., "there")]
            info = NS(language="en", language_probability=.95)
            if failure == "omitted_old": candidate = candidate[1:]
            if failure == "moved_old": candidate[0] = row(.0, .1, "hello")
            if failure == "weak_new": candidate[1].words[0].probability = .69
            if failure == "unsupported_language": info.language = "fr"
            if failure == "uncertain_language": info.language_probability = .79
            if failure == "nan_language": info.language_probability = float("nan")
            if failure == "missing_language": del info.language_probability
            if failure == "missing_words": candidate[1].words = []
            if failure == "missing_timing": del candidate[1].words[0].start
            if failure == "missing_probability": del candidate[1].words[0].probability
            if failure == "nan_word": candidate[1].words[0].end = float("nan")
            if failure == "invalid_probability": candidate[1].words[0].probability = 1.1
            if failure == "bool_time": candidate[1].words[0].start = True
            if failure == "outside_crop": candidate[1].end = 6.
            if failure == "no_new_word": candidate = [row(.3, 2., "hello")]
            return iter(candidate), info
    result = recover_same_window_gap(Model(), np.zeros(80000), {"language": "ko", "vad_parameters": {}},
        11200, 64000, old, NS(language="en", language_probability=.95), regions, [(2.5, 2.7)])
    assert (result is not None) == (failure is None)
    assert calls[0][0] == 52800
    assert calls[0][1] == dict(language=None, word_timestamps=True, vad_filter=False,
                               multilingual=True, condition_on_previous_text=False)
    if result is not None:
        from core.engines.asr.language_disagreement import validate_language_review
        from core.streaming.session_store import _clean_segment_field
        proof = result[2]
        assert proof["alternatives"][1]["kind"] == "same_window_original_context"
        assert validate_language_review(proof)["source_supported"] is False
        assert _clean_segment_field("source_language_review", proof) == proof
        assert proof["crop_start"] == .7 and proof["crop_end"] == 4.


@pytest.mark.parametrize("start,end,gaps,review", [
    (0, 192001, [(1., 1.2)], None), (0, 16000, [], None),
    (0, 16000, [(.5, .7)], {"conditional_selected": True})])
def test_same_window_budget_and_conditional_veto_never_decode(start, end, gaps, review):
    assert recover_same_window_gap(object(), np.zeros(200000), {}, start, end, [],
        NS(language="en", language_probability=.95), [], gaps, prior_review=review) is None
