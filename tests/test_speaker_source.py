"""Source integration faults; real inference acceptance is documented separately."""
import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from config import settings
from core.streaming.pipeline import SegmentItem
from core.streaming.session_store import _clean_segment_field
from core.streaming.speaker_source import annotate_source_rows, recover_speaker_evidence


@pytest.fixture
def source_session(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DIARIZATION_ENABLED", True)
    events, calls = [], []
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source fixture")
    session = SimpleNamespace(video_path=source, cache_dir=tmp_path, total_duration=180.,
        task_id="source-fixture", warnings=[], is_stopped=False, _chunked_source_started=True,
        segments={}, pause_event=asyncio.Event(), saves=0)
    session.pause_event.set()
    async def blocking(function, *args, **kwargs):
        return function(*args, **kwargs)
    async def report(*args, **kwargs):
        events.append(("progress", args, kwargs))
    async def emit(*args):
        events.append(args)
    def save():
        session.saves += 1
    session._run_blocking, session.report_progress, session.emit = blocking, report, emit
    session._persist_if_enabled = save
    session._published_row = lambda row: True
    monkeypatch.setattr("core.streaming.chunked_source.ensure_source_identity", lambda _: "source-identity")
    class Adapter:
        def __init__(self, *args):
            calls.append(("init", args))
        def diarize(self, path, start, end, **kwargs):
            calls.append(("infer", str(path), start, end, kwargs["owned_start"], kwargs["owned_end"]))
            return {"scope_id": "scope", "request_key": "a" * 64}
        def annotate(self, rows, result):
            return [{**deepcopy(row), "speaker_id": "voice-000001",
                "speaker_evidence": {"speaker_id": "voice-000001", "verified": False,
                    "method": "audio_diarization", "scope_id": "scope", "model": "real adapter fixture"},
                "speaker_diagnostics": {"status": "PROVISIONAL", "calibrated": False,
                    "review_required": True, "request_key": "a" * 64,
                    "silhouette": [-.2, None], "matches": []}} for row in rows]
    monkeypatch.setattr("core.engines.asr.speaker_diarization.BoundedSpeakerDiarizer", Adapter)
    return session, calls, events, Adapter


def add_rows(session):
    for sid, start in enumerate((0., 20., 60., 80., 120., 140.)):
        row = SegmentItem(sid, start, start + 2, 2)
        row.text_zh, row.final_vi, row.status = "你好", "Xin chào.", "READY"
        row.audio_path, row.revision = f"healthy-{sid}.wav", 4
        row.verification = {"status": "verified", "semantic_verified": True}
        session.segments[sid] = row


def test_recovery_is_bounded_durable_and_preserves_healthy_speech_and_manual_edit(source_session):
    session, calls, _, _ = source_session
    add_rows(session)
    manual = session.segments[0]
    manual.verification = {"status": "manual"}
    original = deepcopy(manual.to_dict())
    asyncio.run(recover_speaker_evidence(session))
    assert manual.to_dict() == original
    inferences = [call for call in calls if call[0] == "infer"]
    assert len(inferences) == 3 and all(call[3] - call[2] <= 60 for call in inferences)
    assert session.saves == 3
    for row in list(session.segments.values())[1:]:
        assert row.audio_path == f"healthy-{row.id}.wav" and row.final_vi == "Xin chào." and row.revision == 4
        assert row.status == "READY" and row.verification["status"] == "verified"
        assert row.speaker_evidence["verified"] is False and "confidence" not in row.speaker_evidence
        assert _clean_segment_field("speaker_diagnostics", row.speaker_diagnostics) == row.speaker_diagnostics


def test_missing_spoken_draft_gets_fresh_review_without_confirming_speaker(source_session):
    session, _, _, _ = source_session
    add_rows(session)
    row = session.segments[1]
    row.final_vi, row.needs_review = "", True
    row.verification = {"status": "unresolved", "source_supported": True, "semantic_verified": False}
    asyncio.run(recover_speaker_evidence(session))
    assert row.final_vi == "" and row.needs_review and row.verification["status"] == "incomplete"
    assert row.verification["source_supported"] and not row.verification["semantic_verified"]
    assert row.speaker_evidence["verified"] is False


def test_evidence_failure_preserves_source_and_other_stages_remain_available(source_session, monkeypatch):
    session, _, _, adapter = source_session
    monkeypatch.setattr(adapter, "diarize", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError()))
    rows = [{"id": 0, "start": 2., "end": 3., "text_zh": "你好"}]
    assert asyncio.run(annotate_source_rows(session, rows, 0., 10.)) == rows
    assert len(session.warnings) == 1 and "source" not in session.warnings[0]
    assert not session.is_stopped


def test_stop_never_becomes_successful_evidence(source_session, monkeypatch):
    session, _, _, adapter = source_session
    def cancelled(*args, **kwargs):
        session.is_stopped = True
        raise asyncio.CancelledError
    monkeypatch.setattr(adapter, "diarize", cancelled)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(annotate_source_rows(session, [{"id": 0}], 0., 10.))
    assert session.saves == 0


def test_saved_diarization_scores_cannot_become_calibrated_proof(source_session):
    session, _, _, _ = source_session
    add_rows(session)
    asyncio.run(recover_speaker_evidence(session))
    value = deepcopy(session.segments[1].speaker_diagnostics)
    value["calibrated"] = True
    with pytest.raises(ValueError):
        _clean_segment_field("speaker_diagnostics", value)
