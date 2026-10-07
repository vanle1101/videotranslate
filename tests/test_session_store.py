"""Persistence boundaries: corruption and missing media never become success."""
import json
import asyncio
import subprocess
import sys
import wave
from pathlib import Path
from unittest.mock import Mock

import pytest

from config import settings
from core.streaming.pipeline import SegmentItem, StreamingPipelineSession, active_streaming_sessions
from core.streaming.session_store import list_saved_sessions, restore_saved_session, _project_path


@pytest.fixture
def persisted(tmp_path, monkeypatch):
    for name, value in {
        "BASE_DIR": tmp_path, "WORKSPACE_DIR": tmp_path / "workspace",
        "INPUT_DIR": tmp_path / "workspace" / "inputs", "OUTPUT_DIR": tmp_path / "workspace" / "outputs",
        "TEMP_DIR": tmp_path / "workspace" / "temp",
    }.items():
        value.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(settings, name, value)
    source = settings.INPUT_DIR / "clip.mp4"
    source.write_bytes(b"video")
    session = StreamingPipelineSession("persist-test", source)
    session.initialized = True
    session.total_duration = 2
    session.video_size = (1920, 1080)
    segment = SegmentItem(0, 0, 2, 2)
    segment.status = "READY"
    segment.text_zh, segment.final_vi = "你好", "Xin chào."
    segment.audio_path = str(session.segments_dir / "seg_0.wav")
    with wave.open(segment.audio_path, "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x01\x00" * 100)
    session.segments[0] = segment
    session.persist()
    old = dict(active_streaming_sessions)
    active_streaming_sessions.clear()
    yield session
    active_streaming_sessions.clear()
    active_streaming_sessions.update(old)


def test_corrupt_manifest_is_listed_but_cannot_open(persisted):
    _project_path(persisted.task_id).write_text("{\"version\":", encoding="utf-8")
    row = next(item for item in list_saved_sessions() if item["task_id"] == persisted.task_id)
    assert row["can_open"] is False and row["status"] == "FAILED"
    with pytest.raises((ValueError, json.JSONDecodeError)):
        restore_saved_session(persisted.task_id)


@pytest.mark.parametrize("style", [
    {"position": "diagonal"}, {"text_color": "red"}, {"background_color": "#zzzzzz"},
    {"blur_original": "false"}, {"position": []},
])
def test_corrupted_caption_style_cannot_restore(persisted, style):
    path = _project_path(persisted.task_id)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["session"]["caption_style"] = style
    path.write_text(json.dumps(record), encoding="utf-8")
    assert list_saved_sessions()[0]["can_open"] is False
    with pytest.raises((ValueError, TypeError)):
        restore_saved_session(persisted.task_id)


def test_exact_nested_evidence_and_measured_cues_survive(persisted):
    segment = persisted.segments[0]
    segment.verification = {"status": "corrected", "provider": "opencode", "model": "muse",
        "evidence": [{"id": "r0", "start": 0, "end": 2, "text_zh": "你好", "confidence": .98,
                      "bbox": [.1, .8, .8, .05]}], "evidence_ids": ["r0"],
        "source_supported": True, "semantic_verified": True, "second_pass_status": "completed",
        "pacing": {"provider": "opencode", "status": "verified", "text": "Xin chào.", "reason": "Đủ nghĩa"}}
    segment.subtitle_cues = [{"text": "Xin chào.", "start": .1, "end": 1.8,
        "words": [{"text": "Xin", "start": .1, "end": .5}, {"text": "chào.", "start": .6, "end": 1.8}]}]
    segment.subtitle_timing_source = "edge-word-boundary"
    persisted.screen_texts = [{"start": 0, "end": 2, "bbox": [.1, .8, .8, .05],
        "source_method": "local-ocr", "kind": "subtitle", "confidence": .98,
        "needs_review": True, "source_region_verified": True}]
    persisted.caption_style = {"background_color": "#123456", "text_color": "#FFFFFF", "position": "top", "blur_original": True}
    persisted.persist()
    restored = restore_saved_session(persisted.task_id)
    assert restored.segments[0].verification == segment.verification
    assert restored.segments[0].subtitle_cues == segment.subtitle_cues
    assert restored.screen_texts == persisted.screen_texts
    assert restored.caption_style == persisted.caption_style
    assert restored.caption_metadata(restored.segments[0].to_dict())["caption_layout"]["source_masks"]


@pytest.mark.parametrize("stage,code,consensus", [
    ("audio_evidence", "asr_failed", False),
    ("audio_semantic_review", "provider_timeout", True),
    ("semantic_second_pass", "invalid_response", False),
])
def test_review_diagnostics_and_independent_audio_evidence_round_trip(persisted, stage, code, consensus):
    segment = persisted.segments[0]
    segment.needs_review = True
    segment.verification = {"status": "incomplete", "semantic_verified": False,
        "diagnostic": {"stage": stage, "code": code, "run_id": "persist-test_review_1", "segment_ids": [0]},
        "audio_evidence": [
            {"engine": "sensevoice", "text_zh": "你好", "start": 0.0, "end": 2.0},
            {"engine": "faster-whisper-small", "text_zh": "你好" if consensus else "您好", "start": 0.0, "end": 2.0},
        ], "audio_consensus": consensus, "audio_audit_status": "failed"}
    persisted.review_summary = {"status": "incomplete", "checked": 1, "incomplete": 1}
    persisted.persist()
    saved = json.loads(_project_path(persisted.task_id).read_text(encoding="utf-8"))
    assert saved["segments"][0]["verification"] == segment.verification
    restored = restore_saved_session(persisted.task_id)
    assert restored.segments[0].verification == segment.verification
    assert restored.get_progress()["status"] == "FAILED"
    assert restored.get_progress()["can_review"]
    restored.persist()
    active_streaming_sessions.clear()
    assert restore_saved_session(persisted.task_id).segments[0].verification == segment.verification


def test_new_diagnostic_fields_never_persist_secrets_or_raw_provider_data(persisted, monkeypatch):
    secret = "storage-test-private-key"
    monkeypatch.setattr(settings, "OPENCODE_API_KEY", secret)
    persisted.segments[0].verification = {"status": "unresolved",
        "diagnostic": {"stage": "audio_evidence", "code": "asr_failed", "run_id": secret,
            "segment_ids": [0], "reason": "raw private response", "text": "raw private prompt",
            "headers": {"Authorization": "Bearer arbitrary-provider-token"},
            "response_body": "private provider body", "exception": "private decoder exception"},
        "audio_evidence": [{"engine": "sensevoice", "text_zh": f"你好 {secret}", "start": 0, "end": 2,
            "api_key": secret, "headers": {"Cookie": "private-session"}, "reason": "private provider response"}],
        "audio_consensus": False, "audio_audit_status": "failed"}
    path = persisted.persist()
    text = path.read_text(encoding="utf-8")
    for omitted in (secret, "raw private", "arbitrary-provider-token", "private provider", "private-session",
                    "private decoder", "Authorization", "headers", "response_body", "exception", "api_key"):
        assert omitted not in text
    restored = restore_saved_session(persisted.task_id).segments[0].verification
    assert restored["diagnostic"] == {"stage": "audio_evidence", "code": "asr_failed",
                                      "run_id": "[redacted]", "segment_ids": [0]}
    assert restored["audio_evidence"] == [{"engine": "sensevoice", "text_zh": "你好 [redacted]", "start": 0, "end": 2}]


def test_loaded_diagnostics_are_filtered_by_the_same_safe_schema(persisted):
    path = _project_path(persisted.task_id)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["segments"][0]["verification"] = {"status": "unresolved",
        "diagnostic": {"stage": "Authorization: private", "code": "https://private.example/?token=private",
                       "run_id": "Bearer private-token", "segment_ids": [0, "private"], "text": "private"},
        "audio_evidence": [{"engine": "sensevoice", "text_zh": "你好", "start": 0, "end": 2,
                            "headers": "private", "diagnostic": {"code": "provider_failed"}},
                           {"engine": "private engine", "text_zh": "private", "start": 0, "end": 2}],
        "audio_consensus": "true", "audio_audit_status": {"error": "private"}}
    path.write_text(json.dumps(record), encoding="utf-8")
    audit = restore_saved_session(persisted.task_id).segments[0].verification
    assert audit == {"status": "unresolved", "diagnostic": {},
                     "audio_evidence": [{"engine": "sensevoice", "text_zh": "你好", "start": 0, "end": 2}]}


@pytest.mark.parametrize("content", [b"not wav", b"RIFF", b""])
def test_invalid_audio_never_restores_ready(persisted, content):
    Path(persisted.segments[0].audio_path).write_bytes(content)
    restored = restore_saved_session(persisted.task_id)
    assert restored.error and restored.can_retry
    assert restored.segments[0].audio_url is None
    assert restored.segments[0].status == "FAILED"
    assert list_saved_sessions()[0]["status"] == "FAILED"


def test_truncated_wav_with_valid_header_is_not_ready(persisted):
    path = Path(persisted.segments[0].audio_path)
    path.write_bytes(path.read_bytes()[:45])
    restored = restore_saved_session(persisted.task_id)
    assert restored.segments[0].status == "FAILED"


def test_interrupted_review_is_explicit_and_does_not_call_provider(persisted, monkeypatch):
    persisted.review_summary = {"status": "running", "checked": 0}
    persisted.persist()
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    import core.video_intelligence as intelligence
    monkeypatch.setattr(intelligence, "validate_visual_provider", Mock(side_effect=AssertionError("no provider lookup")))
    restored = restore_saved_session(persisted.task_id)
    assert restored.review_summary["status"] == "incomplete"
    assert restored.get_progress()["status"] == "FAILED"
    assert restored.get_progress()["can_review"]
    assert not restored.is_running and restored.worker_task is None and restored.review_task is None


def test_stop_keeps_saved_work_and_completed_rows_reopen_editably(persisted):
    before = Path(persisted.segments[0].audio_path).read_bytes()
    persisted.stop()
    assert _project_path(persisted.task_id).exists()
    assert Path(persisted.segments[0].audio_path).read_bytes() == before
    restored = restore_saved_session(persisted.task_id)
    assert not restored.is_stopped and not restored.is_running
    assert restored.get_progress()["status"] == "COMPLETED"
    assert restored.segments[0].final_vi == persisted.segments[0].final_vi


def test_stop_mid_synthesis_preserves_ready_rows_and_retries_only_missing(persisted, monkeypatch):
    first_audio = Path(persisted.segments[0].audio_path).read_bytes()
    later = SegmentItem(1, 2, 3, 1)
    later.text_zh, later.final_vi, later.status = "再见", "Tạm biệt.", "TTS"
    persisted.segments[1] = later
    persisted.total_duration = 3
    persisted.is_running = True
    persisted.stop()
    restored = restore_saved_session(persisted.task_id)
    assert restored.can_retry and restored.segments[0].status == "READY"
    assert restored.segments[1].status == "FAILED"
    calls = []

    async def synthesize(segment):
        calls.append(segment.id)
        segment.status = "READY"

    monkeypatch.setattr(restored, "_synthesize_segment", synthesize)

    async def resume():
        await restored.retry_failed_synthesis()
        await restored.worker_task

    asyncio.run(resume())
    assert calls == [1]
    assert Path(persisted.segments[0].audio_path).read_bytes() == first_audio


def test_pending_download_restores_without_network(persisted):
    session = StreamingPipelineSession("pending-download", None)
    session.source_url = "https://www.douyin.com/jingxuan?modal_id=123&token=private"
    session.is_running = True
    session.persist()
    restored = restore_saved_session(session.task_id)
    assert restored.source_url == "https://www.douyin.com/jingxuan?modal_id=123"
    assert restored.can_retry and restored._startup_failed
    assert restored.start_task is None and restored.video_path is None


def test_manifest_never_serializes_credentials_engines_or_exception_text(persisted, monkeypatch):
    monkeypatch.setattr(settings, "OPENCODE_API_KEY", "secret-for-storage-test")
    persisted.error = "HTTP failed Authorization: secret-for-storage-test"
    persisted.segments[0].verification = {"status": "verified", "api_key": "secret-for-storage-test",
        "reason": "secret-for-storage-test", "headers": {"Authorization": "secret-for-storage-test"}}
    path = persisted.persist()
    text = path.read_text(encoding="utf-8")
    assert "secret-for-storage-test" not in text
    assert "Authorization" not in text and "api_key" not in text and "headers" not in text
    assert "worker_task" not in text and "translator" not in text


def test_unknown_project_is_file_not_found(persisted):
    with pytest.raises(FileNotFoundError):
        restore_saved_session("unknown-project")


@pytest.mark.parametrize("damage", ["foreign_audio", "output_traversal", "bad_id", "invalid_time"])
def test_unsafe_manifest_is_rejected(persisted, damage):
    path = _project_path(persisted.task_id)
    record = json.loads(path.read_text(encoding="utf-8"))
    if damage == "foreign_audio":
        record["segments"][0]["audio_path"] = str(settings.BASE_DIR / "secret.wav")
    elif damage == "output_traversal":
        record["session"]["output_filename"] = "../secret.mp4"
    elif damage == "bad_id":
        record["task_id"] = "different-project"
    else:
        record["segments"][0]["end"] = float("nan")
    path.write_text(json.dumps(record), encoding="utf-8")
    assert not list_saved_sessions()[0]["can_open"]
    with pytest.raises(ValueError):
        restore_saved_session(persisted.task_id)


def test_failed_atomic_save_keeps_previous_project(persisted, monkeypatch):
    path = _project_path(persisted.task_id)
    before = path.read_bytes()
    persisted.segments[0].final_vi = "Bản chưa lưu được."
    monkeypatch.setattr(Path, "replace", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError):
        persisted.persist()
    assert path.read_bytes() == before
    assert not list(path.parent.glob("*.tmp"))


def test_fresh_process_restores_visual_project_without_provider_credentials(persisted):
    persisted.visual_translation = True
    persisted.caption_style = {"background_color": "#113355", "text_color": "#FFFFFF", "position": "top", "blur_original": False}
    persisted.caption_style_revision = 4
    persisted.current_playback_time = 1.25
    persisted.segments[0].revision = 7
    persisted.segments[0].verification = {"status": "manual", "reason": "Đã sửa."}
    persisted.persist()
    code = """
import json, sys
from pathlib import Path
from config import settings
root = Path(sys.argv[1])
settings.BASE_DIR = root
settings.WORKSPACE_DIR = root / 'workspace'
settings.OUTPUT_DIR = root / 'workspace' / 'outputs'
settings.TEMP_DIR = root / 'workspace' / 'temp'
settings.LLM_PROVIDER = 'gemini'
settings.GEMINI_API_KEY = ''
settings.OPENCODE_API_KEY = ''
import core.video_intelligence as intelligence
def forbidden(*a, **kw):
    raise AssertionError('restoring must never initialize provider')
intelligence.validate_visual_provider = forbidden
intelligence.VideoIntelligence = forbidden
from core.streaming.session_store import restore_saved_session
s = restore_saved_session('persist-test')
assert s.video_intelligence is None and not s.is_running
assert s.start_task is None and s.worker_task is None
assert s.get_progress()['status'] == 'COMPLETED'
assert s.segments[0].final_vi == 'Xin chào.'
assert s.segments[0].revision == 7 and s.segments[0].verification['status'] == 'manual'
assert s.caption_style['position'] == 'top' and s.caption_style_revision == 4
assert s.current_playback_time == 1.25
assert s.segments[0].audio_url == '/api/streaming/audio/persist-test/0?rev=7'
assert s.source_video_url.startswith('/api/local-file?path=')
print(json.dumps({'restored': s.task_id}))
"""
    result = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", code, str(settings.BASE_DIR)],
                            capture_output=True, text=True, timeout=30,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["restored"] == persisted.task_id


def test_missing_selected_voice_keeps_explicit_failure_on_retry(persisted, monkeypatch):
    session = StreamingPipelineSession("missing-voice", persisted.video_path, tts_engine_name="vieneu")
    monkeypatch.setattr("core.streaming.pipeline.VieNeuEngine", Mock(return_value=Mock(is_available=False)))
    for _ in range(2):
        with pytest.raises(ValueError, match="VieNeu"):
            session._ensure_tts_engine()
        assert session.tts_engine is session.edge_tts


def test_edit_after_restore_never_initializes_translation_provider(persisted, monkeypatch):
    persisted.visual_translation = True
    persisted.persist()
    monkeypatch.setattr("core.video_intelligence.validate_visual_provider", Mock(side_effect=AssertionError("provider not needed")))
    restored = restore_saved_session(persisted.task_id)

    def synthesis(**kwargs):
        with wave.open(str(kwargs["output_path"]), "wb") as audio:
            audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\x10\x27" * 24000)
        return {"tts_duration": 1, "speed_ratio": 1, "boundaries": []}

    monkeypatch.setattr("core.streaming.pipeline.synthesize_natural_speech", synthesis)
    asyncio.run(restored.edit_segment(0, "Lời do người dùng sửa."))
    active_streaming_sessions.clear()
    again = restore_saved_session(persisted.task_id)
    assert again.segments[0].final_vi == "Lời do người dùng sửa."
    assert again.segments[0].verification["status"] == "manual"
    assert again.segments[0].revision == 1
    assert again.segments[0].subtitle_cues
    assert again.video_intelligence is None


def test_deleted_audio_is_failed_and_resumeable(persisted):
    Path(persisted.segments[0].audio_path).unlink()
    row = next(item for item in list_saved_sessions() if item["task_id"] == persisted.task_id)
    assert row["status"] == "FAILED" and "âm thanh" in row["missing_media"]
    active_streaming_sessions.clear()
    restored = restore_saved_session(persisted.task_id)
    assert restored.segments[0].status == "FAILED"
    assert restored.segments[0].failed_stage == "TTS"
    assert restored.can_retry is True


def test_deleted_source_is_not_reported_as_completed(persisted):
    persisted.video_path.unlink()
    row = next(item for item in list_saved_sessions() if item["task_id"] == persisted.task_id)
    assert row["can_open"] is False and row["status"] == "FAILED"


def test_real_failure_shape_resumes_tts_with_completed_review_and_auto_export(persisted, monkeypatch):
    """755a2fe4: reviewed visual rows, first TTS failed, next row waiting."""
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    persisted.visual_translation = True
    persisted.translation_sources = [{"provider": "opencode", "model": "muse-spark-1.3-contributor-free",
        "evidence_mode": "fresh-ocr-text-review"}]
    persisted.review_summary = {"status": "completed", "checked": 2, "verified": 0, "corrected": 0, "unresolved": 2}
    first = persisted.segments[0]
    first.status, first.failed_stage = "FAILED", "TTS"
    first.audio_path = None
    later = SegmentItem(1, 2, 3, 1)
    later.text_zh, later.final_vi, later.status = "再见", "Tạm biệt.", "WAITING"
    persisted.segments[1] = later
    for row in persisted.segments.values():
        row.source_method, row.translation_provider = "text-ai", "opencode"
        row.needs_review = True
        row.verification = {"status": "unresolved", "reason": "Cần đối chiếu thêm nguồn."}
    persisted.total_duration = 3
    persisted.error = "Edge-TTS chưa trả về âm thanh sau 3 lần thử."
    persisted.persist()
    assert "auto_export_result" not in json.loads(_project_path(persisted.task_id).read_text(encoding="utf-8"))["session"]
    events, calls = [], []
    restored = restore_saved_session(persisted.task_id, lambda kind, data: events.append((kind, data)))
    assert restored.auto_export_result and restored.can_retry
    assert restored.review_summary == persisted.review_summary
    assert restored.source_url == persisted.source_url
    monkeypatch.setattr(restored, "_process_segment", Mock(side_effect=AssertionError("do not translate again")))
    monkeypatch.setattr(restored, "_review_translations", Mock(side_effect=AssertionError("do not review again")))
    monkeypatch.setattr(restored.faster_whisper, "transcribe", Mock(side_effect=AssertionError("do not recognize again")))

    async def synthesize(row):
        calls.append((row.id, row.final_vi))
        row.audio_path = str(restored.segments_dir / f"seg_{row.id}.wav")
        with wave.open(row.audio_path, "wb") as audio:
            audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\x10\x27" * 100)
        row.status = "READY"

    monkeypatch.setattr(restored, "_synthesize_segment", synthesize)

    async def resume():
        await restored.retry_failed_synthesis()
        await restored.worker_task

    asyncio.run(resume())
    assert calls == [(0, first.final_vi), (1, later.final_vi)]
    assert events[-1][0] == "finished" and events[-1][1]["status"] == "finished"
    assert events[-1][1]["review_summary"] == persisted.review_summary
    assert not restored.is_running and not restored.error and restored.auto_export_result
    active_streaming_sessions.clear()
    again = restore_saved_session(restored.task_id)
    assert again.auto_export_result and again.get_progress()["status"] == "COMPLETED"


@pytest.mark.parametrize("explicit", [False, True])
def test_explicit_auto_export_setting_survives_restart(persisted, explicit):
    persisted.auto_export_result = explicit
    persisted.persist()
    assert restore_saved_session(persisted.task_id).auto_export_result is explicit


def test_legacy_non_visual_session_never_enables_auto_export(persisted):
    restored = restore_saved_session(persisted.task_id)
    assert restored.auto_export_result is False


def test_shutdown_between_review_and_init_resumes_without_provider(persisted, monkeypatch):
    persisted.visual_translation = True
    persisted.initialized = False
    persisted.review_summary = {"status": "completed", "checked": 1, "verified": 1}
    row = persisted.segments[0]
    row.status, row.source_method, row.translation_provider = "WAITING", "text-ai", "opencode"
    row.audio_path = None
    persisted.persist()
    monkeypatch.setattr("core.video_intelligence.validate_visual_provider", Mock(side_effect=AssertionError("no provider on open")))
    restored = restore_saved_session(persisted.task_id)
    assert restored.initialized and restored.can_retry and not restored._startup_failed
    assert restored.segments[0].failed_stage == "TTS"
    assert restored.review_summary == persisted.review_summary
    assert restored.video_intelligence is None and restored.worker_task is None
