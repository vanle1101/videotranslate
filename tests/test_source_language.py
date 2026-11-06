"""Language mismatches must never become invented Chinese timing/checkpoints."""
import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from core.engines.asr.source_language import SourceLanguageMismatchError, language_metadata, require_chinese_source
from core.engines.asr.native_process import ASRProcessError
from core.engines.asr import whisper_process
from core.streaming import chunked_source


@pytest.mark.parametrize("language", [None, "ko", "zh"])
def test_automatic_asr_redetects_language_without_changing_forced_evidence(language):
    from core.engines.asr.faster_whisper_engine import FasterWhisperFallbackEngine
    calls = []
    def transcribe(path, **options):
        calls.append(options)
        return iter([SimpleNamespace(start=0., end=2., text="우리 친구잖아", words=[])]), None
    engine = FasterWhisperFallbackEngine()
    engine.model = SimpleNamespace(transcribe=transcribe)
    rows = engine.transcribe("test-only-no-native.wav", language=language)
    assert rows[0]["text_zh"] == "우리 친구잖아"
    assert calls[0]["language"] == language
    if language is None:
        assert calls[0]["multilingual"] is True
        assert calls[0]["condition_on_previous_text"] is False
        assert calls[0]["utterance_language_detection"] is True
    else:
        assert "multilingual" not in calls[0]
        assert "condition_on_previous_text" not in calls[0]


def test_automatic_language_policy_invalidates_old_source_but_not_background(monkeypatch):
    from core.streaming import source_revision
    monkeypatch.setattr(chunked_source, "_media_identity", lambda _: {"media": "same"})
    monkeypatch.setattr(source_revision, "source_runtime_identity", lambda _: {"runtime": "same"})
    identity = chunked_source._source_identity(SimpleNamespace())
    assert identity["language_routing_version"] == 8
    assert identity != {**identity, "language_routing_version": 6}
    assert chunked_source._media_identity(SimpleNamespace()) == {"media": "same"}


@pytest.mark.parametrize("language", ["ko", "ja", "en"])
def test_detected_foreign_audio_is_actionable_and_not_retryable(language):
    with pytest.raises(SourceLanguageMismatchError) as caught:
        require_chinese_source({"language": language, "probability": .995})
    assert caught.value.retryable is False
    assert caught.value.metadata["language"] == language
    assert "Phụ đề Trung" in str(caught.value)
    assert chunked_source.source_failure_diagnostic(caught.value, "asr")[:2] == (
        "asr", "source_language_mismatch")


@pytest.mark.parametrize("metadata", [None, [], {"language": "ko", "probability": float("nan")},
    {"language": "ko", "probability": True}, {"language": "ko", "probability": 1.01},
    {"language": "", "probability": .9}])
def test_invalid_language_evidence_is_rejected(metadata):
    with pytest.raises(ASRProcessError):
        language_metadata(metadata)


def test_language_metadata_published_only_after_native_completion(tmp_path, monkeypatch):
    audio = tmp_path / "speech.wav"
    audio.write_bytes(b"fixture")
    def records(*args, **kwargs):
        assert args[1]["options"]["language"] is None
        assert args[1]["capture_language"]
        yield {"event": "segment", "segment": {"start": 0., "end": 2., "text": "우리 친구잖아", "words": []}}
        yield {"event": "language", "language": "ko", "probability": .99}
        yield {"event": "completed", "segments": 1}
    monkeypatch.setattr(whisper_process, "iter_native_records", records)
    rows, _ = whisper_process.SubprocessWhisperModel("small").transcribe(audio, language=None,
        metadata_callback=require_chinese_source)
    with pytest.raises(SourceLanguageMismatchError):
        next(rows)  # Even the first Korean row never escapes into text_zh.


def test_incomplete_native_run_does_not_publish_language_diagnosis(tmp_path, monkeypatch):
    audio = tmp_path / "speech.wav"
    audio.write_bytes(b"fixture")
    def records(*args, **kwargs):
        yield {"event": "language", "language": "ko", "probability": .99}
    monkeypatch.setattr(whisper_process, "iter_native_records", records)
    metadata = []
    rows, _ = whisper_process.SubprocessWhisperModel("small").transcribe(audio, language=None,
        metadata_callback=metadata.append)
    with pytest.raises(ASRProcessError):
        list(rows)
    assert metadata == []


def test_empty_audio_does_not_turn_automatic_language_guess_into_a_block(tmp_path, monkeypatch):
    audio = tmp_path / "speech.wav"
    audio.write_bytes(b"fixture")
    def records(*args, **kwargs):
        yield {"event": "language", "language": "en", "probability": .2}
        yield {"event": "completed", "segments": 0}
    monkeypatch.setattr(whisper_process, "iter_native_records", records)
    rows, _ = whisper_process.SubprocessWhisperModel("small").transcribe(audio, language=None,
        metadata_callback=require_chinese_source)
    assert list(rows) == []


def test_mismatch_diagnosis_cache_is_source_and_runtime_scoped(tmp_path):
    session = SimpleNamespace(cache_dir=tmp_path, is_stopped=False)
    identity = {"source": "source.mp4", "revision": "measured-runtime"}
    metadata = {"language": "ko", "probability": .996}
    chunked_source._save_language(session, 100., 132., identity, metadata)
    assert chunked_source._load_language(session, 100., identity) == metadata
    assert chunked_source._load_language(session, 100., {**identity, "source": "different.mp4"}) is None
    assert chunked_source._load_language(session, 100., {**identity, "revision": "new-runtime"}) is None
    assert chunked_source._load_language(session, 132., identity) is None
    assert not chunked_source._record_path(session, 100.).exists()
    assert not list(tmp_path.rglob("*.tmp"))


def test_resume_cached_supported_language_continues_native_source(tmp_path, monkeypatch):
    session = SimpleNamespace(cache_dir=tmp_path, is_stopped=False)
    identity = {"source": "source.mp4"}
    monkeypatch.setattr(chunked_source, "ensure_source_identity", lambda _: "source-hash")
    monkeypatch.setattr(chunked_source, "_source_identity", lambda _: identity)
    monkeypatch.setattr(chunked_source, "_load", lambda *args: None)
    async def blocking(function, *args, **kwargs):
        return function(*args, **kwargs)
    session._run_blocking = blocking
    chunked_source._save_language(session, 0., 32., identity, {"language": "ko", "probability": .99})
    # Cached Korean evidence no longer blocks the native routing path. Stop at
    # extraction to prove it was reached without touching media or production.
    session.total_duration = 32.
    async def extraction(*args, **kwargs):
        raise RuntimeError("extraction reached")
    session.report_progress = extraction
    with pytest.raises(RuntimeError, match="extraction reached"):
        asyncio.run(chunked_source.prepare_interval(session, 0., 24.))
    assert not session._source_preparation_active
    assert session._source_preparation_stage == "audio_extract"
    assert not chunked_source._record_path(session, 0.).exists()


def test_native_worker_reports_automatic_language_as_metadata(tmp_path, monkeypatch):
    from core.engines.asr import whisper_worker
    request, result = tmp_path / "request.json", tmp_path / "result.jsonl"
    request.write_text(json.dumps({"model_size": "small", "device": "cpu", "compute_type": "int8",
        "cpu_threads": 4, "audio_path": "speech.wav", "options": {"language": None}, "capture_language": True}))
    model = SimpleNamespace(transcribe=lambda *args, **kwargs: (
        iter([SimpleNamespace(start=0., end=1., text="우리 친구잖아", words=[])]),
        SimpleNamespace(language="ko", language_probability=.98)))
    monkeypatch.setattr("core.asr.load_whisper_model", lambda *args: model)
    whisper_worker.run(request, result)
    records = [json.loads(line) for line in result.read_text(encoding="utf-8").splitlines()]
    assert records[-2] == {"event": "language", "language": "ko", "probability": .98}
    assert records[-1] == {"event": "completed", "segments": 1}


def test_review_evidence_can_explicitly_use_measured_korean_language(tmp_path, monkeypatch):
    from core.engines.asr.review_evidence_worker import collect_engine
    monkeypatch.setattr("core.media_process.run_media", lambda *args, **kwargs: None)
    options = []
    class Model:
        def __init__(self, *args, **kwargs):
            pass
        def transcribe(self, path, **kwargs):
            options.append(kwargs)
            return iter([SimpleNamespace(text="우리 친구잖아")]), None
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Model))
    readings = []
    collect_engine({"engine": "faster-whisper-small", "rows": [{"id": 81, "start": 202., "end": 211.}],
                    "source_language": "ko", "video_path": "source.mp4", "whisper_model_path": "model",
                    "work_directory": str(tmp_path)}, readings.append)
    assert options[0]["language"] == "ko" and options[0]["task"] == "transcribe"
    assert readings[0]["source_language"] == "ko" and readings[0]["text"] == "우리 친구잖아"


def test_prepared_native_rows_keep_measured_timing_and_mixed_language_metadata(tmp_path, monkeypatch):
    session = SimpleNamespace(cache_dir=tmp_path, video_path=tmp_path / "source.mp4",
        total_duration=10., is_stopped=False, _source_preparation_stage=None,
        vocal_suppressor=SimpleNamespace(), faster_whisper=SimpleNamespace())
    identity = {"source": "fixed", "language_routing_version": 2}
    monkeypatch.setattr(chunked_source, "ensure_source_identity", lambda _: "fixed")
    monkeypatch.setattr(chunked_source, "_source_identity", lambda _: identity)
    monkeypatch.setattr(chunked_source, "_load", lambda *args: None)
    monkeypatch.setattr("core.streaming.prepared_background.background_provenance", lambda _: {})
    async def annotate(session, rows, start, end):
        return rows
    monkeypatch.setattr("core.streaming.speaker_source.annotate_source_rows", annotate)
    async def blocking(function, *args, **kwargs):
        return function(*args, **kwargs)
    async def ffmpeg(command):
        from pathlib import Path
        Path(command[-1]).write_bytes(b"fixture media")
    async def progress(*args, **kwargs):
        pass
    def recognize(path, **kwargs):
        assert kwargs["language"] is None
        kwargs["metadata_callback"]({"language": "ko", "probability": .99, "speech_segments": 2})
        return [{"start": 0., "end": 2., "duration": 2., "text_zh": "우리 친구잖아"},
                {"start": 3., "end": 4., "duration": 1., "text_zh": "你好",
                 "source_language": "zh", "source_language_probability": .99,
                 "source_audio_evidence": "vocal_stem_unverified"}]
    def suppress(**kwargs):
        kwargs["output_audio_path"].write_bytes(b"bgm")
        return {}
    session._run_blocking, session._run_ffmpeg, session.report_progress = blocking, ffmpeg, progress
    session._grounded_visual_segments = lambda rows: rows
    session.faster_whisper.transcribe = recognize
    session.vocal_suppressor.process_file = suppress
    record = asyncio.run(chunked_source.prepare_interval(session, 0., 10.))
    assert record["end"] == 10.
    assert [(row["start"], row["end"]) for row in record["rows"]] == [(0., 2.), (3., 4.)]
    assert [row["source_language"] for row in record["rows"]] == ["ko", "zh"]
    assert record["rows"][0]["evidence_mode"] == "native-recovery-ko"
    assert record["rows"][1]["source_language_uncertain"]
    assert record["rows"][1]["source_language_probability"] == .99
    assert record["rows"][1]["evidence_mode"] == "native-recovery-zh"
    assert record["rows"][1]["source_audio_evidence"] == "vocal_stem_unverified"
    assert "confirmed_silence" not in record["rows"][1]


@pytest.mark.parametrize("speech", [True, False])
def test_empty_native_asr_requires_separate_speech_activity_check(tmp_path, monkeypatch, speech):
    import wave
    import sys
    from types import SimpleNamespace
    from core.engines.asr.source_language import require_empty_source_is_speech_free
    path = tmp_path / "empty-recognition.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\x01\x00" * 16000)
    monkeypatch.setitem(sys.modules, "faster_whisper.vad", SimpleNamespace(
        VadOptions=lambda **kwargs: kwargs,
        get_speech_timestamps=lambda *args: [{"start": 0, "end": 16000}] if speech else []))
    if speech:
        with pytest.raises(ASRProcessError) as caught:
            require_empty_source_is_speech_free(path)
        assert caught.value.code == "source_speech_unrecognized"
    else:
        require_empty_source_is_speech_free(path)
