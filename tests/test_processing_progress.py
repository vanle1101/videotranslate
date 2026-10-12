"""Measured runtime progress; these fixtures are not provider acceptance tests."""
import asyncio
import wave

import pytest

from config import settings
from core.streaming.pipeline import SegmentItem, StreamingPipelineSession


@pytest.fixture
def session(tmp_path, monkeypatch):
    # A fresh API-module import mounts static assets under the isolated root.
    (tmp_path / "static").mkdir()
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    current = StreamingPipelineSession("measured-processing", None, translation_mode="preview",
                                       tts_engine_name="edge-tts", _restoring=True)
    current.total_duration = 320
    current._chunked_source_started = True
    current.auto_export_result = True
    return current


def row(session, identity, start, end, *, ready=False, speech=True):
    item = SegmentItem(identity, start, end, end - start)
    item.text_zh = item.asr_text = "源话" if speech else ""
    if ready:
        item.final_vi = "Lời thoại." if speech else ""
        item.status = "READY"
        if speech:
            path = session.segments_dir / f"{identity}.wav"
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(16000)
                handle.writeframes(b"\0\0" * 8000)
            item.audio_path = str(path)
            item.audio_url = f"/audio/{identity}?rev={item.revision}"
    session.segments[identity] = item
    return item


def test_unknown_source_denominator_stays_indeterminate(session):
    snapshot = session.get_progress()
    assert snapshot["processing_scope"] == "preview"
    assert snapshot["processing_progress_pct"] is None
    assert not snapshot["processing_total_known"]
    assert snapshot["processing_completed_segments"] == snapshot["processing_total_segments"] == 0
    row(session, 0, 0, 2, ready=True)
    session._source_prepared_seconds = 2
    snapshot = session.get_progress()
    assert snapshot["processing_completed_segments"] == snapshot["processing_total_segments"] == 1
    assert snapshot["processing_progress_pct"] is None


def test_preview_percentage_excludes_recognition_lookahead(session):
    row(session, 0, 0, 2, ready=True)
    row(session, 1, 8, 10)
    row(session, 2, 16, 18, ready=True)
    row(session, 3, 24, 26)
    session._source_prepared_seconds = 32
    session.is_running = True
    session.progress = {"status": "RUNNING", "phase": "review", "progress_pct": None}
    snapshot = session.get_progress()
    assert snapshot["processing_total_known"]
    assert snapshot["processing_scope"] == "preview"
    assert snapshot["processing_completed_segments"] == 2
    assert snapshot["processing_total_segments"] == 3
    assert snapshot["processing_progress_pct"] == 66.7
    assert snapshot["progress_pct"] is None and snapshot["status"] == "RUNNING"


def test_preview_includes_complete_sentence_crossing_boundary(session):
    row(session, 0, 0, 2, ready=True)
    row(session, 1, 23, 25)
    row(session, 2, 25, 27)
    session._source_prepared_seconds = 24
    assert session.get_progress()["processing_progress_pct"] is None
    session._source_prepared_seconds = 32
    snapshot = session.get_progress()
    assert snapshot["processing_total_segments"] == 2
    assert snapshot["processing_progress_pct"] == 50


def test_playable_review_required_draft_counts_without_approving_semantics(session):
    item = row(session, 0, 0, 2, ready=True)
    item.needs_review = True
    item.verification = {"status": "unresolved", "semantic_verified": False}
    session._source_prepared_seconds = session._visual_completed_seconds = 24
    session._preview_ready = True
    snapshot = session.get_progress()
    assert snapshot["processing_progress_pct"] == 100
    assert snapshot["processing_completed_segments"] == 1
    assert snapshot["content_review_state"] == "REVIEW_REQUIRED"
    assert snapshot["segment_states"]["COMPLETED"] == 0
    assert snapshot["segment_states"]["REVIEW_REQUIRED"] == 1
    assert snapshot["status"] == "PREVIEW_READY" and snapshot["progress_pct"] is None
    assert not item.verification["semantic_verified"]


@pytest.mark.parametrize("fault", ["missing", "empty", "corrupt", "truncated", "too_long", "no_url"])
def test_ready_status_cannot_count_invalid_speech_as_completed(session, fault):
    from pathlib import Path
    item = row(session, 0, 0, 2, ready=True)
    session._source_prepared_seconds = 24
    assert session.get_progress()["processing_progress_pct"] == 100
    path = Path(item.audio_path)
    if fault == "missing":
        path.unlink()
    elif fault == "empty":
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"")
    elif fault == "corrupt":
        path.write_bytes(b"invalid audio")
    elif fault == "truncated":
        path.write_bytes(path.read_bytes()[:100])
    elif fault == "too_long":
        item.end = item.duration = .4
    else:
        item.audio_url = None
    snapshot = session.get_progress()
    assert snapshot["processing_completed_segments"] == 0
    assert snapshot["processing_progress_pct"] == 0


def test_untranslated_source_never_counts_as_silence(session):
    item = row(session, 0, 0, 2)
    item.status = "READY"
    item.needs_review = True
    item.subtitle_timing_source = "empty"
    session._source_prepared_seconds = 24
    assert session.get_progress()["processing_progress_pct"] == 0
    item.confirmed_silence = True
    assert session.get_progress()["processing_progress_pct"] == 100


def test_actual_asr_silence_counts_only_after_processing(session):
    item = row(session, 0, 0, 2, ready=True, speech=False)
    session._source_prepared_seconds = 24
    assert session.get_progress()["processing_progress_pct"] == 0
    item.subtitle_timing_source = "empty"
    assert session.get_progress()["processing_progress_pct"] == 100
    item.needs_review = True
    assert session.get_progress()["processing_progress_pct"] == 0


def test_review_invalidation_recomputes_media_work_instead_of_pinning_maximum(session):
    item = row(session, 0, 0, 2, ready=True)
    session._source_prepared_seconds = 24
    assert session.get_progress()["processing_progress_pct"] == 100
    item.status = "WAITING"
    item.audio_path = item.audio_url = None
    assert session.get_progress()["processing_progress_pct"] == 0


def test_full_scope_counts_admitted_units_but_waits_for_complete_denominator(session):
    session.translation_mode = "full"
    row(session, 0, 0, 2, ready=True)
    row(session, 1, 8, 10)
    row(session, 2, 24, 26)
    session._source_prepared_seconds = 32
    session._chunk_jobs = [{"start": 0, "end": 24, "state": "RUNNING"}]
    snapshot = session.get_progress()
    assert snapshot["processing_scope"] == "full"
    assert snapshot["processing_total_segments"] == 2
    assert snapshot["processing_completed_segments"] == 1
    assert not snapshot["processing_total_known"] and snapshot["processing_progress_pct"] is None
    session._source_prepared_seconds = 320
    snapshot = session.get_progress()
    assert snapshot["processing_total_known"] and snapshot["processing_total_segments"] == 3
    assert snapshot["processing_progress_pct"] == 33.3


def test_playable_prefix_never_jumps_over_pending_or_invalid_audio(session):
    session.translation_mode = "full"
    first = row(session, 0, 0, 10, ready=True)
    middle = row(session, 1, 10, 20)
    row(session, 2, 20, 30, ready=True)
    session._source_prepared_seconds = 40
    session._chunk_jobs = [{"start": 0, "end": 40, "state": "RUNNING"}]
    assert session._processing_progress_metadata()["processing_ready_seconds"] == 10
    middle.confirmed_silence, middle.status = True, "READY"
    assert session._processing_progress_metadata()["processing_ready_seconds"] == 30
    first.audio_path = None
    assert session._processing_progress_metadata()["processing_ready_seconds"] == 0


def test_stage_completion_does_not_publish_overall_completion_before_validated_mp4(session):
    row(session, 0, 0, 2, ready=True)
    session.translation_mode = "full"
    session._source_prepared_seconds = 320
    session.is_running = True
    asyncio.run(session.report_progress("review", "Review work", 100))
    snapshot = session.get_progress()
    assert snapshot["processing_progress_pct"] == snapshot["stage_progress_pct"] == 100
    assert snapshot["progress_pct"] is None and snapshot["status"] == "RUNNING"


def test_empty_prepared_silent_preview_has_no_fake_segment_percentage(session):
    session._source_prepared_seconds = session._visual_completed_seconds = 24
    session._preview_ready = True
    snapshot = session.get_progress()
    assert snapshot["processing_total_known"] and snapshot["processing_total_segments"] == 0
    assert snapshot["processing_progress_pct"] is None
    assert snapshot["status"] == "PREVIEW_READY"


def test_progress_fields_survive_live_project_and_task_list_api_boundaries(session, monkeypatch):
    import httpx
    import main
    registry = {session.task_id: session}
    monkeypatch.setattr(main, "active_streaming_sessions", registry)
    monkeypatch.setattr("core.streaming.pipeline.active_streaming_sessions", registry)
    monkeypatch.setattr(main, "active_export_tasks", {})
    monkeypatch.setattr(main, "task_history", [])
    session.is_running = True
    session._source_prepared_seconds = 24
    row(session, 0, 0, 2, ready=True)
    row(session, 1, 8, 10)
    expected = session._processing_progress_metadata()

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                     base_url="http://test") as client:
            project = await client.get(f"/api/streaming/{session.task_id}")
            tasks = await client.get("/api/tasks")
        assert project.status_code == tasks.status_code == 200
        task = next(item for item in tasks.json()["tasks"] if item["task_id"] == session.task_id)
        for key, value in expected.items():
            assert project.json()["progress"][key] == task[key] == value

    asyncio.run(check())
