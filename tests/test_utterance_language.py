from types import SimpleNamespace
import numpy as np
import pytest

from core.engines.asr.utterance_language import speech_windows, transcribe_local_speech, UnrecognizedSpeechError
from core.engines.asr import whisper_process


def test_windows_follow_silence_and_never_cut_long_continuous_speech():
    assert speech_windows([{"start": 100, "end": 1000}, {"start": 1040, "end": 1200},
                           {"start": 2000, "end": 4000}], 5000, rate=100) == [(70, 1230), (1970, 4030)]
    assert speech_windows([{"start": 100, "end": 4000}], 5000, rate=100) == [(70, 4030)]


def test_each_local_crop_redetects_language_and_shifts_word_timing():
    calls = []
    class Model:
        def transcribe(self, audio, **options):
            calls.append((len(audio), options))
            lang, text, prob = ("ko", "안녕", .95) if len(calls) == 1 else ("zh", "你好", .87)
            row = SimpleNamespace(start=.2, end=.7, text=text,
                words=[SimpleNamespace(word=text, start=.2, end=.7)])
            return iter([row]), SimpleNamespace(language=lang, language_probability=prob)
    rows, meta = transcribe_local_speech(Model(), np.zeros(160000, dtype=np.float32),
        {"language": None, "word_timestamps": True},
        regions=[{"start": 16000, "end": 32000}, {"start": 80000, "end": 112000}])
    result = list(rows)
    assert [r.source_language for r in result] == ["ko", "zh"]
    assert [r.source_language_probability for r in result] == [.95, .87]
    assert [r.start for r in result] == pytest.approx([.9, 4.9])
    assert result[1].words[0].start == pytest.approx(4.9)
    assert all(opts["language"] is None and not opts["vad_filter"] for _, opts in calls)
    assert meta().language == "zh"


def test_empty_speech_crop_is_a_gap_and_silence_never_decodes():
    class Model:
        def transcribe(self, *args, **options):
            return iter([]), SimpleNamespace(language="ko", language_probability=.9)
    rows, _ = transcribe_local_speech(Model(), np.zeros(32000), {},
                                     regions=[{"start": 0, "end": 16000}])
    with pytest.raises(UnrecognizedSpeechError):
        list(rows)
    rows, _ = transcribe_local_speech(object(), np.zeros(32000), {}, regions=[])
    assert list(rows) == []


def test_gap_after_native_rows_cannot_publish_partial_checkpoint(tmp_path, monkeypatch):
    audio = tmp_path / "source.wav"
    audio.write_bytes(b"fixture")
    def records(*args, **kwargs):
        yield {"event": "segment", "segment": {"start": 0., "end": 1., "text": "你好", "words": [],
            "source_language": "zh", "source_language_probability": .95}}
        yield {"event": "error", "code": "source_speech_unrecognized"}
    monkeypatch.setattr(whisper_process, "iter_native_records", records)
    rows, _ = whisper_process.SubprocessWhisperModel("small", device="cpu").transcribe(audio)
    with pytest.raises(whisper_process.ASRProcessError) as caught:
        next(rows)
    assert caught.value.code == "source_speech_unrecognized"


def test_native_row_language_is_validated():
    row = {"event": "segment", "segment": {"start": 0., "end": 1., "text": "hello", "words": [],
        "source_language": "en", "source_language_probability": .92}}
    assert whisper_process._decode_segment(row).source_language_probability == .92
    row["segment"]["source_language_probability"] = float("nan")
    with pytest.raises(whisper_process.ASRProcessError):
        whisper_process._decode_segment(row)


def test_nonempty_crop_still_rejects_an_omitted_short_utterance():
    class Model:
        def transcribe(self, *args, **options):
            return iter([SimpleNamespace(start=.3, end=1.1, text="你好", words=[])]), SimpleNamespace(
                language="zh", language_probability=.97)
    rows, _ = transcribe_local_speech(Model(), np.zeros(64000), {}, regions=[
        {"start": 16000, "end": 32000}, {"start": 40000, "end": 43200}])
    with pytest.raises(UnrecognizedSpeechError) as caught:
        list(rows)
    assert caught.value.spans == [(2.5, 2.7)]


def measured_row(start, end, text):
    return SimpleNamespace(start=start, end=end, text=text,
        words=[SimpleNamespace(start=start, end=end, word=text, probability=.95)])


def sample_region(start, end):
    return {"start": round(start * 16000), "end": round(end * 16000)}


@pytest.mark.parametrize("with_next", [False, True])
def test_original_only_128ms_tail_recovers_in_same_window_before_oversized_forward(with_next):
    original = [sample_region(11.680, 18.), sample_region(18.688, 18.816)]
    vocal = [sample_region(11.488, 18.)]
    if with_next:
        original.append(sample_region(20., 25.856))
    calls = []
    class Model:
        def transcribe(self, audio, **options):
            calls.append((len(audio), float(audio[0]), options))
            if len(calls) == 1:
                rows = [measured_row(.3, 6.812, "hello")]
            elif len(calls) == 2:
                rows = [measured_row(.3, 6.812, "hello"), measured_row(7.5, 7.628, "there")]
            else:
                rows = [measured_row(.3, 6.156, "next")]
            return iter(rows), SimpleNamespace(language="en", language_probability=.95)
    rows, metadata = transcribe_local_speech(Model(), np.zeros(27 * 16000), {}, regions=original,
        vocal_audio=np.ones(27 * 16000), vocal_regions=vocal)
    result = list(rows)
    assert [r.text for r in result] == (["hello", "there", "next"] if with_next else ["hello", "there"])
    assert [c[:2] for c in calls[:2]] == [(126848, 1.), (126848, 0.)]
    assert len(calls) == (3 if with_next else 2)
    assert all(r.source_audio_evidence == "original_audio" for r in result)
    assert result[1].start == pytest.approx(18.688)
    assert result[1].words[0].end == pytest.approx(18.816)
    assert result[0].source_language_probability == result[1].source_language_probability == 0.
    assert metadata().language_probability == 0.
    proof = result[0].source_language_review
    assert proof["status"] == "unresolved" and proof["source_supported"] is False
    assert proof["crop_start"] == 11.188 and proof["crop_end"] == 19.116
    assert proof["alternatives"][1]["kind"] == "same_window_original_context"
    if with_next:
        assert result[2].source_language_probability == .95
        assert result[2].source_language_review is None


@pytest.mark.parametrize("mode", ["original_only", "stem_only_gap", "mixed_origin_gap", "conditional_veto"])
def test_same_window_recovery_does_not_bypass_source_selection_or_conditional_veto(mode, monkeypatch):
    original = [sample_region(1., 2.), sample_region(2.5, 2.7)]
    vocal = [sample_region(.808, 2.)]
    if mode == "stem_only_gap":
        original.pop()
        vocal.append(sample_region(2.5, 2.7))
    if mode == "mixed_origin_gap": vocal.append(sample_region(2.55, 2.65))
    calls = []
    class Model:
        def transcribe(self, audio, **options):
            calls.append(len(audio))
            return iter([measured_row(.3, 1.3, "hello")]), SimpleNamespace(language="en", language_probability=.95)
    def conditional(model, audio, start, end, bounds, rows, info, options, reader, **kwargs):
        return rows, "en", 0., {"conditional_selected": True}
    monkeypatch.setattr("core.engines.asr.language_disagreement.recover_language", conditional)
    rows, _ = transcribe_local_speech(Model(), np.zeros(64000), {}, regions=original,
        vocal_audio=None if mode == "original_only" else np.ones(64000), vocal_regions=vocal,
        language_reader=object() if mode == "conditional_veto" else None)
    with pytest.raises(UnrecognizedSpeechError):
        list(rows)
    assert len(calls) == 1


def test_rejected_same_window_candidate_keeps_gap_and_forward_budget():
    original = [sample_region(11.680, 18.), sample_region(18.688, 18.816), sample_region(20., 25.856)]
    calls = []
    class Model:
        def transcribe(self, audio, **options):
            calls.append((len(audio), float(audio[0])))
            return iter([measured_row(.3, 6.812, "hello")]), SimpleNamespace(language="en", language_probability=.95)
    rows, _ = transcribe_local_speech(Model(), np.zeros(27 * 16000), {}, regions=original,
        vocal_audio=np.ones(27 * 16000), vocal_regions=[sample_region(11.488, 18.)])
    with pytest.raises(UnrecognizedSpeechError) as caught:
        list(rows)
    assert calls == [(126848, 1.), (126848, 0.)]
    assert caught.value.spans == [(18.688, 18.816)]
    diagnostic = caught.value.diagnostic
    assert diagnostic["selected_evidence"] == diagnostic["final_evidence"] == "vocal_stem_unverified"
    assert diagnostic["gaps"] == [{"span": [18.688, 18.816], "vad_origins": ["original_audio"]}]


def test_same_window_rejection_can_still_use_existing_bounded_forward_retry():
    calls = []
    class Model:
        def transcribe(self, audio, **options):
            calls.append((len(audio), float(audio[0])))
            values = [measured_row(.3, 1.492, "hello")]
            if len(calls) == 3:
                values.extend([measured_row(1.992, 2.192, "there"), measured_row(3.492, 4.492, "again")])
            return iter(values), SimpleNamespace(language="en", language_probability=.95)
    rows, _ = transcribe_local_speech(Model(), np.zeros(96000), {},
        regions=[sample_region(1., 2.), sample_region(2.5, 2.7), sample_region(4., 5.)],
        vocal_audio=np.ones(96000), vocal_regions=[sample_region(.808, 2.)])
    result = list(rows)
    assert calls == [(39872, 1.), (39872, 0.), (76672, 0.)]
    assert [r.text for r in result] == ["hello", "there", "again"]
    assert result[1].start == pytest.approx(2.5)
    assert result[0].source_language_review["alternatives"][1]["kind"] == "forward_original_context"
    assert all(r.source_language_probability == 0. for r in result)


def test_same_window_candidate_cannot_hide_uncovered_raw_vocal_region_in_union():
    calls = []
    class Model:
        def transcribe(self, audio, **options):
            calls.append(len(audio))
            values = [measured_row(.3, 1.492, "hello")]
            if len(calls) == 2:
                values.append(measured_row(5.992, 6.192, "there"))
            return iter(values), SimpleNamespace(language="en", language_probability=.95)
    rows, _ = transcribe_local_speech(Model(), np.zeros(8 * 16000), {},
        regions=[sample_region(1., 6.), sample_region(6.5, 6.7)],
        vocal_audio=np.ones(8 * 16000),
        vocal_regions=[sample_region(.808, 2.), sample_region(4., 5.)])
    with pytest.raises(UnrecognizedSpeechError) as caught:
        list(rows)
    assert len(calls) == 2
    assert caught.value.spans == [(6.5, 6.7)]


@pytest.mark.parametrize("covers_vocal", [False, True])
def test_forward_retry_after_same_window_rejection_keeps_raw_vocal_gap(covers_vocal):
    calls = []
    class Model:
        def transcribe(self, audio, **options):
            calls.append(len(audio))
            values = [measured_row(.3, .7, "hello")]
            if len(calls) >= 2:
                if len(calls) == 3 and covers_vocal:
                    values.append(measured_row(1.55, 1.69, "bridge"))
                values.append(measured_row(1.992, 2.192, "there"))
            if len(calls) == 3:
                values.append(measured_row(3.492, 4.492, "again"))
            return iter(values), SimpleNamespace(language="en", language_probability=.95)
    rows, _ = transcribe_local_speech(Model(), np.zeros(96000), {},
        regions=[sample_region(1., 2.2), sample_region(2.5, 2.7), sample_region(4., 5.)],
        vocal_audio=np.ones(96000), vocal_regions=[sample_region(.808, 1.2), sample_region(2.05, 2.2)])
    if covers_vocal:
        result = list(rows)
        assert [r.text for r in result] == ["hello", "bridge", "there", "again"]
        assert all(r.source_language_probability == 0. for r in result)
    else:
        with pytest.raises(UnrecognizedSpeechError):
            list(rows)
    assert len(calls) == 3


def test_healthy_original_audio_path_does_not_add_a_decode():
    calls = []
    class Model:
        def transcribe(self, audio, **options):
            calls.append(float(audio[0]))
            return iter([measured_row(.3, 1.3, "hello")]), SimpleNamespace(language="en", language_probability=.95)
    rows, _ = transcribe_local_speech(Model(), np.zeros(48000), {}, regions=[sample_region(1., 2.)],
        vocal_audio=np.ones(48000), vocal_regions=[sample_region(1., 2.)])
    result = list(rows)
    assert calls == [0.]
    assert result[0].source_language_probability == .95
    assert result[0].source_audio_evidence == "original_audio"
    assert result[0].source_language_review is None
