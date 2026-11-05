"""Diagnostic provenance survives failure without changing coverage decisions."""
import json
import asyncio
from types import SimpleNamespace
from pathlib import Path

import numpy as np
import pytest

from core.engines.asr.utterance_language import transcribe_local_speech, UnrecognizedSpeechError
from core.engines.asr.speech_gap_diagnostic import build_gap_diagnostic, validate_gap_diagnostic, MAX_SPANS
from core.engines.asr import whisper_process, whisper_worker
from core.streaming.chunked_source import _save_native_error, prepare_interval
from test_preview_chunked_pipeline import preview, wav
from test_session_store import persisted


def region(start, end):
    return {"start": int(start * 16000), "end": int(end * 16000)}


def fail(*, original, vocal, empty=False):
    class Model:
        def transcribe(self, audio, **options):
            rows = [] if empty else [SimpleNamespace(start=.3, end=1.3, text="你好", words=[])]
            return iter(rows), SimpleNamespace(language="zh", language_probability=.95)
    rows, _ = transcribe_local_speech(Model(), np.zeros(64000), {}, regions=original,
        vocal_audio=np.zeros(64000), vocal_regions=vocal)
    with pytest.raises(UnrecognizedSpeechError) as caught:
        list(rows)
    return caught.value


def test_stem_only_gap_keeps_original_and_selected_window_without_certifying_silence():
    error = fail(original=[region(1, 2)], vocal=[region(2.5, 2.7)])
    assert error.spans == [(2.5, 2.7)]
    diagnostic = error.diagnostic
    assert diagnostic["gaps"] == [{"span": [2.5, 2.7], "vad_origins": ["vocal_stem"]}]
    assert diagnostic["original_regions"] == [[1., 2.]]
    assert diagnostic["vocal_regions"] == diagnostic["stem_selection_triggers"] == [[2.5, 2.7]]
    assert diagnostic["window"] == [.7, 3.]
    assert diagnostic["selected_evidence"] == diagnostic["final_evidence"] == "vocal_stem_unverified"


def test_empty_overlapping_vad_preserves_union_origins_and_public_crop_span():
    error = fail(original=[region(1, 2)], vocal=[region(1.5, 2.5)], empty=True)
    assert error.spans == [(.7, 2.8)]  # Existing public empty-crop behavior retained.
    assert error.diagnostic["gaps"] == [{"span": [1., 2.5],
                                       "vad_origins": ["original_audio", "vocal_stem"]}]
    assert error.diagnostic["original_regions"] == [[1., 2.]]
    assert error.diagnostic["vocal_regions"] == [[1.5, 2.5]]


def test_real_worker_error_serialization_and_parent_transport(tmp_path, monkeypatch):
    error = fail(original=[region(1, 2)], vocal=[region(2.5, 2.7)])
    def failed_segments(*args, **kwargs):
        def generate():
            raise error
            yield
        return generate(), None
    monkeypatch.setattr("core.asr.load_whisper_model", lambda *args: object())
    monkeypatch.setattr("faster_whisper.audio.decode_audio", lambda *args, **kwargs: np.zeros(64000))
    monkeypatch.setattr("core.engines.asr.utterance_language.transcribe_local_speech", failed_segments)
    request, result = tmp_path / "request.json", tmp_path / "records.jsonl"
    request.write_text(json.dumps({"device": "cpu", "compute_type": "int8", "cpu_threads": 1,
        "model_size": "small", "audio_path": "unused.wav", "options": {"utterance_language_detection": True}}))
    from config import settings
    monkeypatch.setattr(settings, "WHISPER_COMPUTE_TYPE", settings.WHISPER_COMPUTE_TYPE)
    monkeypatch.setattr(settings, "ASR_CPU_THREADS", settings.ASR_CPU_THREADS)
    whisper_worker.run(request, result)
    record = json.loads(result.read_text(encoding="utf-8"))
    assert record["event"] == "error" and record["diagnostic"] == error.diagnostic
    def records(*args, **kwargs):
        yield record
    monkeypatch.setattr(whisper_process, "iter_native_records", records)
    with pytest.raises(whisper_process.ASRProcessError) as caught:
        list(whisper_process.SubprocessWhisperModel("small", "cpu")._run_worker(
            tmp_path / "unused.wav", {}, 10, None))
    assert caught.value.code == "source_speech_unrecognized"
    assert caught.value.spans == [[2.5, 2.7]]
    assert caught.value.diagnostic == error.diagnostic


@pytest.mark.parametrize("invalid", [None, {"version": 1}, "silence"])
def test_malformed_transport_diagnostic_is_not_accepted(monkeypatch, tmp_path, invalid):
    def records(*args, **kwargs):
        yield {"event": "error", "code": "source_speech_unrecognized", "spans": [[1, 2]],
               "diagnostic": invalid}
    monkeypatch.setattr(whisper_process, "iter_native_records", records)
    with pytest.raises(whisper_process.ASRProcessError):
        list(whisper_process.SubprocessWhisperModel("small", "cpu")._run_worker(tmp_path / "unused", {}, 10, None))


def test_provenance_lists_are_bounded_with_explicit_omission_counts():
    regions = [region(i, i + .2) for i in range(MAX_SPANS + 2)]
    value = build_gap_diagnostic(original=regions, vocal=[], gaps=[(i, i + .2) for i in range(MAX_SPANS + 2)],
        samples=16000 * (MAX_SPANS + 3), rate=16000, window_index=0, window=(0, 16000),
        selected_evidence="original_audio", final_evidence="original_audio", triggers=[],
        original_vad_supplied=True, vocal_vad_supplied=False)
    assert len(value["original_regions"]) == len(value["gaps"]) == MAX_SPANS
    assert value["omitted"] == {"original_regions": 2, "vocal_regions": 0, "stem_selection_triggers": 0, "gaps": 2}
    value["window"] = [0, float("nan")]
    with pytest.raises(ValueError):
        validate_gap_diagnostic(value)


def test_failure_sidecar_retains_input_identity_and_origin_without_checkpoint(tmp_path):
    raw = tmp_path / "interval_16k.wav"
    raw.write_bytes(b"original fixture bytes")
    vocal = tmp_path / "interval_16k_vocal_evidence.wav"
    vocal.write_bytes(b"vocal fixture bytes")
    error = fail(original=[region(1, 2)], vocal=[region(2.5, 2.7)])
    error.code = "source_speech_unrecognized"
    session = SimpleNamespace(cache_dir=tmp_path)
    _save_native_error(session, 156.06, 160.06, {"fixture": True}, raw, error)
    files = list((tmp_path / "source_preparation").iterdir())
    assert [p.name for p in files] == ["000000156060_native_error.json"]
    saved = json.loads(files[0].read_text())
    assert saved["interval_start"] == 156.06 and saved["interval_end"] == 160.06
    assert saved["diagnostic"] == error.diagnostic and saved["spans"] == [[2.5, 2.7]]
    assert saved["raw_audio"]["sha256"] != saved["vocal_evidence"]["sha256"]
    assert raw.read_bytes() == b"original fixture bytes" and vocal.read_bytes() == b"vocal fixture bytes"
    assert not list((tmp_path / "source_preparation").glob("[0-9].json"))


def test_diagnostic_write_failure_does_not_replace_original_gap(tmp_path, monkeypatch, caplog):
    raw = tmp_path / "input.wav"
    raw.write_bytes(b"fixture")
    error = fail(original=[region(1, 2)], vocal=[region(2.5, 2.7)])
    error.code = "source_speech_unrecognized"
    def denied(*args, **kwargs):
        raise PermissionError("fixture")
    monkeypatch.setattr("core.streaming.chunked_source.tempfile.NamedTemporaryFile", denied)
    _save_native_error(SimpleNamespace(cache_dir=tmp_path), 0, 4, {}, raw, error)
    assert error.spans == [(2.5, 2.7)] and "SOURCE_GAP_DIAGNOSTIC_NOT_SAVED" in caplog.text


def test_prepare_interval_persists_native_failure_without_publishing_source(preview, monkeypatch):
    session, *_ = preview
    error = fail(original=[region(1, 2)], vocal=[region(2.5, 2.7)])
    transported = whisper_process.ASRProcessError(str(error))
    transported.code, transported.spans, transported.diagnostic = "source_speech_unrecognized", error.spans, error.diagnostic
    def recognize(*args, **kwargs):
        raise transported
    async def ffmpeg(command):
        wav(Path(command[-1]))
    monkeypatch.setattr(session, "_run_ffmpeg", ffmpeg)
    monkeypatch.setattr(session.faster_whisper, "transcribe", recognize)
    with pytest.raises(whisper_process.ASRProcessError) as caught:
        asyncio.run(prepare_interval(session, 0, 24))
    assert caught.value is transported
    root = session.cache_dir / "source_preparation"
    assert json.loads((root / "000000000000_native_error.json").read_text())["diagnostic"] == error.diagnostic
    assert not (root / "000000000000.json").exists()
