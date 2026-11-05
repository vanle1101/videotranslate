"""A published partial MP4 stays downloadable without completing the project."""
from pathlib import Path
import wave

import pytest

from config import settings
from core.streaming import session_store as store
from core.streaming.pipeline import SegmentItem, StreamingPipelineSession, active_streaming_sessions


@pytest.fixture
def partial_project(tmp_path, monkeypatch):
    for name, path in {
        "BASE_DIR": tmp_path, "WORKSPACE_DIR": tmp_path / "workspace",
        "INPUT_DIR": tmp_path / "workspace" / "inputs", "OUTPUT_DIR": tmp_path / "workspace" / "outputs",
        "TEMP_DIR": tmp_path / "workspace" / "temp",
    }.items():
        path.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(settings, name, path)
    source = settings.INPUT_DIR / "source.mp4"
    source.write_bytes(b"source fixture")
    session = StreamingPipelineSession("partial-persist", source)
    session.initialized, session.total_duration = True, 4
    good, failed = SegmentItem(0, 0, 2, 2), SegmentItem(1, 2, 4, 2)
    good.status, good.text_zh, good.final_vi = "READY", "你好", "Xin chào."
    good.audio_path = str(session.segments_dir / "seg_0.wav")
    with wave.open(good.audio_path, "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x01\x00" * 100)
    failed.status, failed.failed_stage = "FAILED", "TTS"
    failed.text_zh, failed.final_vi, failed.error = "再见", "Tạm biệt.", "TTS chưa có giọng."
    session.segments = {0: good, 1: failed}
    session.error = "Một câu chưa tạo được giọng."
    session.review_summary = {"status": "incomplete"}
    session.output_filename = "partial.mp4"
    session.output_incomplete, session.output_missing_segment_ids = True, [1]
    (settings.OUTPUT_DIR / session.output_filename).write_bytes(b"validated output fixture")
    monkeypatch.setattr(store, "_valid_output", lambda *args: True)
    previous = dict(active_streaming_sessions)
    active_streaming_sessions.clear()
    session.persist()
    yield session
    active_streaming_sessions.clear()
    active_streaming_sessions.update(previous)


@pytest.mark.parametrize("missing_text", [False, True])
def test_partial_download_survives_repeated_restore_without_completing_failed_row(partial_project, missing_text):
    if missing_text:
        partial_project.segments[1].final_vi = ""
        partial_project.segments[1].failed_stage = "TRANSLATING"
        partial_project.persist()
    for _ in range(2):
        history, = store.list_saved_sessions()
        assert history["output_video_url"] == "/api/outputs/partial.mp4"
        assert history["output_incomplete"] and history["output_missing_segment_ids"] == [1]
        assert history["status"] == "FAILED" and history["progress_pct"] is None
        assert "1 câu" in history["output_warning"]
        restored = store.restore_saved_session(partial_project.task_id)
        assert restored.output_filename == "partial.mp4"
        assert restored.output_incomplete and restored.output_missing_segment_ids == [1]
        assert restored.segments[1].status == "FAILED"
        assert restored.segments[1].final_vi == partial_project.segments[1].final_vi
        assert restored.review_summary["status"] == "incomplete"
        restored.persist()
        active_streaming_sessions.pop(restored.task_id)


@pytest.mark.parametrize("damage", ["unmarked", "stale", "invalid"])
def test_partial_opt_in_does_not_expose_unmarked_stale_or_invalid_output(partial_project, monkeypatch, damage):
    if damage == "unmarked":
        partial_project.output_incomplete = False
        partial_project.output_missing_segment_ids = []
    elif damage == "stale":
        partial_project.caption_output_outdated = True
    else:
        monkeypatch.setattr(store, "_valid_output", lambda *args: False)
    partial_project.persist()
    history, = store.list_saved_sessions()
    assert history["output_video_url"] == ""
    assert not history["output_incomplete"]
    restored = store.restore_saved_session(partial_project.task_id)
    assert restored.output_filename == "" and not restored.output_incomplete
    assert restored.output_missing_segment_ids == []


@pytest.mark.parametrize("field,value", [
    ("output_incomplete", "true"), ("output_incomplete", 1),
    ("output_missing_segment_ids", "1"), ("output_missing_segment_ids", [True]),
    ("output_missing_segment_ids", [1, 1]), ("output_missing_segment_ids", [99]),
    ("output_missing_segment_ids", [-1]), ("output_missing_segment_ids", ["1"]),
])
def test_partial_metadata_rejects_invalid_types_and_nonmember_ids(partial_project, field, value):
    before = store._project_path(partial_project.task_id).read_bytes()
    setattr(partial_project, field, value)
    with pytest.raises(ValueError):
        partial_project.persist()
    assert store._project_path(partial_project.task_id).read_bytes() == before


def test_review_only_partial_export_can_have_no_missing_voice_rows(partial_project):
    partial_project.output_missing_segment_ids = []
    partial_project.persist()
    history, = store.list_saved_sessions()
    assert history["output_video_url"] and history["output_incomplete"]
    assert "kiểm tra chưa hoàn tất" in history["output_warning"]


def test_output_invalidation_clears_durable_partial_publication(partial_project):
    partial_project._invalidate_output()
    partial_project.persist()
    history, = store.list_saved_sessions()
    assert not history["output_video_url"] and not history["output_incomplete"]
    assert history["output_missing_segment_ids"] == []
    assert (settings.OUTPUT_DIR / "partial.mp4").is_file()


def test_failed_edit_rollback_keeps_partial_publication_markers(partial_project, monkeypatch, tmp_path):
    from core.streaming.pipeline import ProjectEditSaveError
    partial_project._persistence_enabled = True
    monkeypatch.setattr(partial_project, "persist", lambda: (_ for _ in ()).throw(OSError("locked")))
    with pytest.raises(ProjectEditSaveError):
        with partial_project._durable_edit_publication(partial_project.segments[0], {}, tmp_path / "staged.wav"):
            partial_project._invalidate_output()
    assert partial_project.output_filename == "partial.mp4"
    assert partial_project.output_incomplete and partial_project.output_missing_segment_ids == [1]
