import json
import wave

import pytest

from config import settings
from core.streaming.pipeline import SegmentItem, StreamingPipelineSession, active_streaming_sessions
from core.streaming import session_store as store


SIGNATURE = "a" * 64


@pytest.fixture
def saved_session(tmp_path, monkeypatch):
    for name, value in {
        "BASE_DIR": tmp_path, "WORKSPACE_DIR": tmp_path / "workspace",
        "INPUT_DIR": tmp_path / "workspace" / "inputs",
        "OUTPUT_DIR": tmp_path / "workspace" / "outputs",
        "TEMP_DIR": tmp_path / "workspace" / "temp",
    }.items():
        value.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(settings, name, value)
    source = settings.INPUT_DIR / "clip.mp4"
    source.write_bytes(b"video")
    session = StreamingPipelineSession("auto-output-state", source)
    session.initialized = True
    session.total_duration = 2
    session.auto_export_result = False
    session.automatic_output = None
    session._auto_export_attempts = None
    segment = SegmentItem(0, 0, 2, 2)
    segment.status = "READY"
    segment.text_zh, segment.final_vi = "你好", "Xin chào."
    segment.audio_path = str(session.segments_dir / "seg_0.wav")
    with wave.open(segment.audio_path, "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x01\x00" * 100)
    session.segments[0] = segment
    store.save_session(session)
    old = dict(active_streaming_sessions)
    active_streaming_sessions.clear()
    try:
        yield session
    finally:
        active_streaming_sessions.clear()
        active_streaming_sessions.update(old)


def output_state(status="pending", reason=""):
    return {"status": status, "policy": "strict_media", "reason": reason}


@pytest.mark.parametrize("status", ["pending", "running", "blocked", "failed", "completed", "cancelled"])
@pytest.mark.parametrize("count", [0, 1, 2])
def test_automatic_output_and_revision_budget_survive_restarts(saved_session, status, count):
    expected = output_state(status, "Chưa đủ bằng chứng." if status == "blocked" else "")
    saved_session.automatic_output = expected
    saved_session._auto_export_attempts = (SIGNATURE, count)
    store.save_session(saved_session)
    for _ in range(3):
        record = store._read(saved_session.task_id)
        assert record["session"]["automatic_output"] == expected
        assert record["session"]["_auto_export_attempts"] == [SIGNATURE, count]
        restored = store.restore_saved_session(saved_session.task_id)
        assert restored.automatic_output == expected
        assert restored._auto_export_attempts == (SIGNATURE, count)
        assert type(restored._auto_export_attempts) is tuple
        assert restored.auto_export_result is False
        store.save_session(restored)
        active_streaming_sessions.pop(saved_session.task_id)


def test_legacy_missing_and_unset_optional_state_are_safe(saved_session):
    for key in ("automatic_output", "_auto_export_attempts"):
        assert key not in store._read(saved_session.task_id)["session"]
        delattr(saved_session, key)
    store.save_session(saved_session)
    restored = store.restore_saved_session(saved_session.task_id)
    assert getattr(restored, "automatic_output", None) is None
    assert getattr(restored, "_auto_export_attempts", None) is None
    assert restored.auto_export_result is False
    assert restored.output_filename == ""


INVALID_STATES = [
    {}, [], True, "completed",
    output_state("skipped"), output_state("approved"), output_state(True),
    output_state([]), output_state(reason=True), output_state(reason=None),
    output_state(reason="x" * 129), output_state(reason="bad\x00reason"),
    {"status": "pending", "policy": "strict_media"},
    {**output_state(), "policy": True},
    {**output_state(), "policy": "allow_skips"},
    {**output_state(), "skipped_segment_ids": [0]},
    {**output_state(), "approved": True},
    {**output_state(), "confirmation_id": "invented"},
]
INVALID_ATTEMPTS = [
    [], [SIGNATURE], [SIGNATURE, 1, 2], {}, True,
    [True, 1], ["g" * 64, 1], ["a" * 63, 1], ["a" * 65, 1],
    [SIGNATURE, True], [SIGNATURE, False], [SIGNATURE, -1],
    [SIGNATURE, 3], [SIGNATURE, 1.0], [SIGNATURE, "1"], [SIGNATURE, None],
]


@pytest.mark.parametrize("field,value", [
    *[("automatic_output", value) for value in INVALID_STATES],
    *[("_auto_export_attempts", value) for value in INVALID_ATTEMPTS],
])
def test_invalid_runtime_state_cannot_replace_committed_project(saved_session, field, value):
    path = store._project_path(saved_session.task_id)
    before = path.read_bytes()
    setattr(saved_session, field, value)
    with pytest.raises(ValueError):
        store.save_session(saved_session)
    assert path.read_bytes() == before


@pytest.mark.parametrize("field,value", [
    *[("automatic_output", value) for value in [None, *INVALID_STATES]],
    *[("_auto_export_attempts", value) for value in [None, *INVALID_ATTEMPTS]],
])
def test_invalid_durable_state_is_rejected_on_read_and_restore(saved_session, field, value):
    path = store._project_path(saved_session.task_id)
    record = store._read(saved_session.task_id)
    record["session"][field] = value
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError):
        store._read(saved_session.task_id)
    with pytest.raises(ValueError):
        store.restore_saved_session(saved_session.task_id)
    assert saved_session.task_id not in active_streaming_sessions


def test_bound_reason_and_revision_change_roundtrip(saved_session):
    saved_session.automatic_output = output_state(reason="x" * 128)
    saved_session._auto_export_attempts = (SIGNATURE, 2)
    store.save_session(saved_session)
    restored = store.restore_saved_session(saved_session.task_id)
    assert restored._auto_export_attempts == (SIGNATURE, 2)
    new_signature = "b" * 64
    restored._auto_export_attempts = (new_signature, 0)
    store.save_session(restored)
    active_streaming_sessions.pop(saved_session.task_id)
    restarted = store.restore_saved_session(saved_session.task_id)
    assert restarted._auto_export_attempts == (new_signature, 0)
    assert restarted.automatic_output["reason"] == "x" * 128


def test_completed_automatic_state_does_not_fabricate_gate_approval(saved_session):
    segment = saved_session.segments[0]
    segment.status = "NEEDS_REVIEW"
    segment.needs_review = True
    segment.verification = {"status": "unresolved", "semantic_verified": False}
    saved_session.automatic_output = output_state("completed")
    saved_session._auto_export_attempts = (SIGNATURE, 2)
    store.save_session(saved_session)
    restored = store.restore_saved_session(saved_session.task_id)
    actual = restored.segments[0]
    assert actual.status == "NEEDS_REVIEW"
    assert actual.needs_review is True
    assert actual.verification == segment.verification
    assert actual.confirmed_silence is False
    assert restored.output_filename == ""
    assert restored.output_video_url == ""
    assert restored.auto_export_result is False
