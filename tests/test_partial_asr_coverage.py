"""Fault-injected partial recognition keeps source holes explicit and atomic."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
import wave

import numpy as np
import pytest

from core.engines.asr import whisper_process
from core.engines.asr.faster_whisper_engine import FasterWhisperFallbackEngine
from core.engines.asr.partial_coverage import validate_coverage
from core.engines.asr.speech_gap_diagnostic import build_gap_diagnostic
from core.engines.asr.utterance_language import transcribe_local_speech, UnrecognizedSpeechError
from core.runtime_context import execution_context


def pcm(path, seconds=32):
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        stream.writeframes(b"\0\0" * round(seconds * 16000))
    return path


def diagnostic(start=9.7, end=11.3, duration=32):
    return build_gap_diagnostic(original=[{"start": 160000, "end": 176000}],
        vocal=[], gaps=[(10., 11.)], samples=round(duration * 16000), rate=16000,
        window_index=1, window=(round(start * 16000), round(end * 16000)),
        selected_evidence="original_audio", final_evidence="original_audio", triggers=[],
        original_vad_supplied=True, vocal_vad_supplied=False)


def coverage():
    return {"unresolved_windows": [[9.7, 11.3]], "missing_speech_spans": [[10., 11.]],
            "diagnostics": [diagnostic()], "tail_unresolved": False}


def segment(start, end, text="accepted"):
    return {"event": "segment", "segment": {"start": start, "end": end, "text": text, "words": []}}


@pytest.mark.parametrize("partial", [False, True])
def test_failed_whole_vad_window_is_withheld_and_later_windows_continue_only_when_opted_in(partial):
    calls, collected = [], []
    class Model:
        def transcribe(self, audio, **options):
            calls.append(len(audio))
            rows = [] if len(calls) == 2 else [SimpleNamespace(start=.3, end=1.3,
                text="earlier" if len(calls) == 1 else "later", words=[])]
            return iter(rows), SimpleNamespace(language="zh", language_probability=.95)
    rows, metadata = transcribe_local_speech(Model(), np.zeros(32 * 16000), {},
        regions=[{"start": start * 16000, "end": (start + 1) * 16000} for start in (1, 10, 28)],
        **({"coverage_callback": collected.append} if partial else {}))
    if not partial:
        with pytest.raises(UnrecognizedSpeechError):
            list(rows)
        assert len(calls) == 2 and not collected
        return
    accepted = list(rows)
    assert [row.text for row in accepted] == ["earlier", "later"]
    assert len(calls) == 3
    assert collected[0]["unresolved_windows"] == [[9.7, 11.3]]
    assert collected[0]["missing_speech_spans"] == [[9.7, 11.3]]
    assert validate_coverage(collected[0], 32, accepted) == collected[0]
    assert metadata().language == "zh"


def test_nonempty_rejected_hypotheses_are_not_returned_and_tail_is_explicit():
    class Model:
        def transcribe(self, *args, **kwargs):
            return iter([SimpleNamespace(start=.3, end=1.1, text="incomplete", words=[])]), SimpleNamespace(
                language="zh", language_probability=.97)
    result = []
    rows, _ = transcribe_local_speech(Model(), np.zeros(43200), {}, regions=[
        {"start": 16000, "end": 32000}, {"start": 40000, "end": 43200}],
        coverage_callback=result.append)
    assert list(rows) == []
    assert result[0]["unresolved_windows"] == [[.7, 2.7]]
    assert result[0]["missing_speech_spans"] == [[2.5, 2.7]]
    assert result[0]["tail_unresolved"] is True
    validate_coverage(result[0], 2.7)


@pytest.fixture
def protocol(tmp_path, monkeypatch):
    from config import settings
    # Preserve the pre-optimization contract in the existing fault-injection
    # cases; dedicated tests below cover the low-latency on-gap strategy.
    monkeypatch.setattr(settings, "ASR_VOCAL_EVIDENCE_STRATEGY", "always", raising=False)
    source = pcm(tmp_path / "crop.wav")
    monkeypatch.setattr("core.engines.asr.vocal_evidence.prepare_vocal_evidence", lambda *a, **k: source)
    def install(records):
        def generated(*args, **kwargs):
            yield from deepcopy(records)
        monkeypatch.setattr(whisper_process, "iter_native_records", generated)
    return source, install


def test_partial_protocol_publishes_only_after_validated_terminal_record(protocol):
    source, install = protocol
    install([segment(1, 2), segment(28, 29),
             {"event": "partial_completed", "segments": 2, "coverage": coverage()}])
    received = []
    rows, _ = whisper_process.SubprocessWhisperModel("small", "cpu").transcribe(source,
        language=None, utterance_language_detection=True, coverage_callback=received.append)
    assert received == []
    assert [row.start for row in rows] == [1, 28]
    assert received == [coverage()]


@pytest.mark.parametrize("failure", ["unsolicited", "wrong_marker", "missing_marker", "extra_record",
    "overlap", "out_of_crop", "nan", "bad_diagnostic", "wrong_duration", "gap_outside_window",
    "overlapping_windows", "unordered_windows", "uncovered_window", "wrong_tail", "word_overlap"])
def test_invalid_partial_protocol_never_yields_or_invokes_callback(protocol, failure):
    source, install = protocol
    value = coverage()
    rows = [segment(1, 2), segment(28, 29)]
    final = {"event": "partial_completed", "segments": 2, "coverage": value}
    records = [*rows, final]
    if failure == "wrong_marker": final["event"] = "completed"
    elif failure == "missing_marker": records.pop()
    elif failure == "extra_record": records.append(segment(30, 31))
    elif failure == "overlap": rows[0]["segment"].update(start=9, end=10)
    elif failure == "out_of_crop": value["unresolved_windows"][0][1] = 33
    elif failure == "nan": value["missing_speech_spans"][0][0] = float("nan")
    elif failure == "bad_diagnostic": value["diagnostics"][0]["window"] = [9, 11.3]
    elif failure == "wrong_duration": value["diagnostics"][0]["input_samples"] = 31 * 16000
    elif failure == "gap_outside_window": value["missing_speech_spans"] = [[5, 6]]
    elif failure == "overlapping_windows": value["unresolved_windows"].append([10, 12])
    elif failure == "unordered_windows": value["unresolved_windows"].append([4, 5])
    elif failure == "uncovered_window": value["unresolved_windows"].append([15, 17])
    elif failure == "wrong_tail": value["tail_unresolved"] = True
    elif failure == "word_overlap": rows[0]["segment"]["words"] = [{"word": "x", "start": 10, "end": 11}]
    install(records)
    received = []
    rows, _ = whisper_process.SubprocessWhisperModel("small", "cpu").transcribe(source,
        language=None, utterance_language_detection=True,
        **({} if failure == "unsolicited" else {"coverage_callback": received.append}))
    with pytest.raises(whisper_process.ASRProcessError):
        next(rows)
    assert received == []


def test_oom_discards_gpu_partial_before_one_cpu_coverage_publication(protocol, monkeypatch):
    source, _ = protocol
    devices, received = [], []
    def records(_, request, **kwargs):
        devices.append(request["device"])
        yield segment(1, 2, request["device"])
        if request["device"] == "cuda":
            yield {"event": "error", "code": "cuda_out_of_memory", "device": "cuda"}
        else:
            yield {"event": "partial_completed", "segments": 1, "coverage": coverage()}
    monkeypatch.setattr(whisper_process, "iter_native_records", records)
    rows, _ = whisper_process.SubprocessWhisperModel("small", "cuda").transcribe(source,
        language=None, utterance_language_detection=True, coverage_callback=received.append)
    assert [row.text for row in rows] == ["cpu"]
    assert devices == ["cuda", "cpu"] and received == [coverage()]


def test_cancel_or_child_crash_after_terminal_never_publishes_coverage(protocol, monkeypatch):
    source, _ = protocol
    for failure in ("cancel", "crash"):
        cancelled, received = [], []
        def records(*args, **kwargs):
            yield segment(1, 2)
            yield {"event": "partial_completed", "segments": 1, "coverage": coverage()}
            if failure == "crash":
                raise whisper_process.ASRProcessError("child exit 7")
            cancelled.append(True)
        monkeypatch.setattr(whisper_process, "iter_native_records", records)
        with execution_context("partial-test", lambda: bool(cancelled)):
            rows, _ = whisper_process.SubprocessWhisperModel("small", "cpu").transcribe(source,
                language=None, utterance_language_detection=True, coverage_callback=received.append)
            with pytest.raises(asyncio.CancelledError if failure == "cancel" else whisper_process.ASRProcessError):
                next(rows)
        assert not received


def test_adapter_returns_same_list_contract_and_delivers_validated_coverage(protocol):
    source, install = protocol
    install([segment(1, 2), segment(28, 29),
             {"event": "partial_completed", "segments": 2, "coverage": coverage()}])
    engine = FasterWhisperFallbackEngine("small")
    engine.model = whisper_process.SubprocessWhisperModel("small", "cpu")
    received = []
    rows = engine.transcribe(source, language=None, coverage_callback=received.append)
    assert [row["start"] for row in rows] == [1, 28]
    assert received == [coverage()]
    with pytest.raises(ValueError):
        engine.transcribe(source, language="zh", coverage_callback=received.append)


def test_completed_opt_in_still_requires_explicit_empty_coverage(protocol):
    source, install = protocol
    received = []
    value = {"unresolved_windows": [], "missing_speech_spans": [], "diagnostics": [], "tail_unresolved": False}
    install([segment(1, 2), {"event": "completed", "segments": 1, "coverage": value}])
    rows, _ = whisper_process.SubprocessWhisperModel("small", "cpu").transcribe(source,
        language=None, utterance_language_detection=True, coverage_callback=received.append)
    assert len(list(rows)) == 1 and received == [value]


def test_on_gap_strategy_skips_roformer_when_original_audio_coverage_is_complete(protocol, monkeypatch):
    from config import settings
    source, install = protocol
    monkeypatch.setattr(settings, "ASR_VOCAL_EVIDENCE_STRATEGY", "on_gap", raising=False)
    empty = {"unresolved_windows": [], "missing_speech_spans": [], "diagnostics": [], "tail_unresolved": False}
    install([segment(1, 2), {"event": "completed", "segments": 1, "coverage": empty}])
    monkeypatch.setattr("core.engines.asr.vocal_evidence.prepare_vocal_evidence",
        lambda *a, **k: pytest.fail("RoFormer evidence should be skipped for complete original-audio coverage"))
    received = []
    rows, _ = whisper_process.SubprocessWhisperModel("small", "cpu").transcribe(source,
        language=None, utterance_language_detection=True, coverage_callback=received.append)
    assert [row.text for row in rows] == ["accepted"]
    assert received == [empty]


def test_on_gap_strategy_retries_with_roformer_after_unresolved_original_audio(protocol, monkeypatch):
    from config import settings
    source, _ = protocol
    monkeypatch.setattr(settings, "ASR_VOCAL_EVIDENCE_STRATEGY", "on_gap", raising=False)
    requests, prepared = [], []
    def records(_, request, **kwargs):
        requests.append(request)
        if len(requests) == 1:
            yield segment(1, 2, "first pass")
            yield {"event": "partial_completed", "segments": 1, "coverage": coverage()}
        else:
            yield segment(10, 11, "evidence retry")
            value = {"unresolved_windows": [], "missing_speech_spans": [], "diagnostics": [], "tail_unresolved": False}
            yield {"event": "completed", "segments": 1, "coverage": value}
    monkeypatch.setattr(whisper_process, "iter_native_records", records)
    monkeypatch.setattr("core.engines.asr.vocal_evidence.prepare_vocal_evidence",
        lambda path, **kwargs: prepared.append(path) or path)
    received = []
    rows, _ = whisper_process.SubprocessWhisperModel("small", "cpu").transcribe(source,
        language=None, utterance_language_detection=True, coverage_callback=received.append)
    assert [(row.text, row.start, row.end) for row in rows] == [
        ("first pass", 1, 2), ("evidence retry", 10, 11)]
    assert len(prepared) == 1 and len(requests) == 2
    assert requests[0]["options"].get("vocal_evidence_path") is None
    assert requests[1]["options"]["vocal_evidence_path"] == str(source)
    assert requests[1]["options"]["vocal_evidence_spans"] == [[10., 11.]]
    assert received[0]["unresolved_windows"] == []


def test_failed_focused_retry_keeps_original_rows_and_gap_diagnostic(protocol, monkeypatch):
    from config import settings
    source, _ = protocol
    monkeypatch.setattr(settings, "ASR_VOCAL_EVIDENCE_STRATEGY", "on_gap", raising=False)
    requests = []
    def records(_, request, **kwargs):
        requests.append(request)
        yield segment(1, 2, "first pass" if len(requests) == 1 else "unrelated retry row")
        yield {"event": "partial_completed", "segments": 1, "coverage": coverage()}
    monkeypatch.setattr(whisper_process, "iter_native_records", records)
    monkeypatch.setattr("core.engines.asr.vocal_evidence.prepare_vocal_evidence",
        lambda path, **kwargs: path)
    received = []
    rows, _ = whisper_process.SubprocessWhisperModel("small", "cpu").transcribe(source,
        language=None, utterance_language_detection=True, coverage_callback=received.append)
    assert [row.text for row in rows] == ["first pass"]
    assert received == [coverage()]
    assert len(requests) == 2
    assert requests[1]["options"].get("vocal_evidence_spans") == [[10., 11.]]


def test_focused_vocal_retry_decodes_only_windows_around_missing_spans(monkeypatch):
    audio = np.zeros(32 * 16000, dtype=np.float32)
    regions = [{"start": start * 16000, "end": (start + 1) * 16000}
               for start in (1, 10, 20)]
    monkeypatch.setattr("faster_whisper.vad.get_speech_timestamps",
        lambda samples, _options: regions)
    calls = []
    class Model:
        def transcribe(self, samples, **_options):
            calls.append(len(samples))
            return iter([SimpleNamespace(start=.2, end=1.2, text="recovered", words=[])]), \
                SimpleNamespace(language="zh", language_probability=.95)
    coverage_result = []
    generated, _ = transcribe_local_speech(Model(), audio, {"language": None},
        vocal_audio=audio, vocal_spans=[[10., 11.]], language_reader=None,
        coverage_callback=coverage_result.append)
    rows = list(generated)
    assert len(calls) == 1 and calls[0] < len(audio)
    assert [(round(row.start, 3), round(row.end, 3), row.text) for row in rows] == [(9.9, 10.9, "recovered")]
    assert coverage_result == [{"unresolved_windows": [], "missing_speech_spans": [],
        "diagnostics": [], "tail_unresolved": False}]


def test_real_worker_emits_partial_terminal_and_keeps_later_source(tmp_path, monkeypatch):
    from config import settings
    from core.engines.asr import whisper_worker
    from core.engines.asr import utterance_language
    monkeypatch.setattr(settings, "WHISPER_COMPUTE_TYPE", "int8")
    monkeypatch.setattr(settings, "ASR_CPU_THREADS", 4)
    calls = []
    class Model:
        def transcribe(self, audio, **options):
            calls.append(len(audio))
            rows = [] if len(calls) == 2 else [SimpleNamespace(start=.3, end=1.3,
                text="earlier" if len(calls) == 1 else "later", words=[])]
            return iter(rows), SimpleNamespace(language="zh", language_probability=.95)
    monkeypatch.setattr("core.asr.load_whisper_model", lambda *a: Model())
    monkeypatch.setattr("faster_whisper.audio.decode_audio", lambda *a, **k: np.zeros(32 * 16000))
    monkeypatch.setattr("core.engines.asr.language_disagreement.LocalLanguageReader", lambda: None)
    original = utterance_language.transcribe_local_speech
    def with_regions(*args, **kwargs):
        return original(*args, **kwargs, regions=[
            {"start": start * 16000, "end": (start + 1) * 16000} for start in (1, 10, 28)])
    monkeypatch.setattr(utterance_language, "transcribe_local_speech", with_regions)
    request, output = tmp_path / "request.json", tmp_path / "output.jsonl"
    request.write_text(json.dumps({"model_size": "small", "device": "cpu", "compute_type": "int8",
        "cpu_threads": 4, "audio_path": "injected.wav", "capture_language": True, "capture_coverage": True,
        "options": {"language": None, "utterance_language_detection": True}}))
    whisper_worker.run(request, output)
    records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert [item["event"] for item in records] == ["segment", "segment", "language", "partial_completed"]
    assert [item["segment"]["text"] for item in records[:2]] == ["earlier", "later"]
    assert records[-1]["segments"] == 2
    assert records[-1]["coverage"]["unresolved_windows"] == [[9.7, 11.3]]
    validate_coverage(records[-1]["coverage"], 32, [item["segment"] for item in records[:2]])
