"""Language selection must affect recognition, persistence and task reuse."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

import main
from config import settings
from core.engines.asr.faster_whisper_engine import FasterWhisperFallbackEngine
from core.streaming.pipeline import StreamingPipelineSession
from core.streaming.session_store import restore_saved_session


@pytest.mark.parametrize("language", ["auto", "zh", "en", "ja", "ko"])
def test_asr_uses_per_segment_detection_only_in_auto(language):
    options = {}
    def transcribe(path, **kwargs):
        options.update(kwargs)
        # A contract fixture, not an assertion of real recognition quality.
        return iter([SimpleNamespace(start=0, end=1, text="你好", words=[]),
                     SimpleNamespace(start=1, end=2, text="Good morning", words=[])]), None
    engine = FasterWhisperFallbackEngine()
    engine.model = SimpleNamespace(transcribe=transcribe)
    rows = engine.transcribe(Path("not-opened.wav"), language=language)
    assert [row["text_zh"] for row in rows] == ["你好", "Good morning"]
    assert options["language"] == (None if language == "auto" else language)
    assert bool(options.get("multilingual")) is (language == "auto")
    if language == "auto":
        assert options["chunk_length"] == 8
        assert options["condition_on_previous_text"] is False


@pytest.mark.parametrize("model,field", [(main.StreamUrlRequest, "url"),
                                        (main.StreamLocalFileRequest, "file_path")])
def test_new_requests_default_auto_and_reject_unknown_language(model, field):
    assert model(**{field: "source"}).source_language == "auto"
    with pytest.raises(ValidationError):
        model(**{field: "source", "source_language": "invented"})


def test_auto_ocr_keeps_english_word_boundaries_and_chinese():
    from core.screen_ocr import ScreenOCR
    box = [[10, 10], [190, 10], [190, 40], [10, 40]]
    observations = [[box, "Good morning", .99], [box, "大家早上好", .99]]
    rows = ScreenOCR.detections(observations, 200, 100, source_language="auto")
    assert [row["text_zh"] for row in rows] == ["Good morning", "大家早上好"]
    # Existing Chinese-only projects retain the previous OCR policy.
    assert [row["text_zh"] for row in ScreenOCR.detections(observations, 200, 100)] == ["大家早上好"]


@pytest.fixture
def language_project(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    registry = {}
    monkeypatch.setattr(main, "active_streaming_sessions", registry)
    monkeypatch.setattr("core.streaming.pipeline.active_streaming_sessions", registry)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"identity-only test fixture")
    session = main.create_streaming_session("language-resume", source, source_language="auto")
    session.persist()
    return session, registry


def test_language_survives_manifest_restore_progress_and_matching(language_project):
    original, registry = language_project
    registry.clear()
    restored = restore_saved_session(original.task_id)
    assert restored.source_language == "auto"
    assert restored.get_progress()["source_language"] == "auto"
    request = dict(video_path=restored.video_path, voice=restored.voice,
                   engine=restored.tts_engine_name, asr_engine=restored.asr_engine_name,
                   visual_translation=False, translation_mode="preview")
    assert main._saved_project_candidates(**request, source_language="auto")[0][0] == original.task_id
    assert main._saved_project_candidates(**request, source_language="en") == []
    # Even an attached owner cannot override a changed requested language.
    found, resumed = asyncio.run(main._restore_matching_saved_project(**request, source_language="en"))
    assert found is None and resumed is False


def test_legacy_manifest_retains_chinese_policy(language_project):
    import json
    original, registry = language_project
    path = settings.BASE_DIR / "workspace/projects" / (original.task_id + ".json")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["session"].pop("source_language")
    path.write_text(json.dumps(manifest), encoding="utf-8")
    registry.clear()
    assert restore_saved_session(original.task_id).source_language == "zh"


def test_parallel_visual_engine_keeps_source_language(language_project, monkeypatch):
    monkeypatch.setattr("core.video_intelligence.validate_visual_provider", lambda: "opencode")
    session, _ = language_project
    engine = session._new_chunk_intelligence()
    assert engine.screen_ocr.source_language == "auto"


@pytest.mark.parametrize("route", ["url", "local"])
def test_start_carries_source_language_to_session_and_recovery(language_project, monkeypatch, route):
    original, registry = language_project
    registry.clear()
    restore = AsyncMock(return_value=(None, False))
    monkeypatch.setattr(main, "_restore_matching_saved_project", restore)
    monkeypatch.setattr(main, "run_session", AsyncMock())
    monkeypatch.setattr(StreamingPipelineSession, "_start_guarded", AsyncMock())
    monkeypatch.setattr(main.downloader, "normalize_url", lambda value: value)
    async def run():
        if route == "url":
            result = await main.start_streaming_url(main.StreamUrlRequest(url="https://example.com/video", source_language="en"))
        else:
            result = await main.start_streaming_local_file(main.StreamLocalFileRequest(file_path=str(original.video_path), source_language="en"))
        await asyncio.sleep(0)
        assert registry[result["task_id"]].source_language == "en"
        assert restore.await_args.kwargs["source_language"] == "en"
    asyncio.run(run())
