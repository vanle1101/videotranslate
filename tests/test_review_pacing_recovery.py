"""Fault injection for a reviewed correction whose replacement speech cannot fit.

Synthetic PCM and a deterministic review response exercise publication and
recovery ownership only; these tests do not claim provider or real-video QA.
"""
import asyncio
from copy import deepcopy
from pathlib import Path
import wave

import pytest

from config import settings
from core.streaming import pipeline
from core.streaming.pipeline import SegmentItem, StreamingPipelineSession
from core.streaming.session_store import _read
from core.translation_review import AutomaticTranslationReviewer


@pytest.fixture
def reviewed_session(tmp_path, monkeypatch):
    for name, value in {
        "BASE_DIR": tmp_path, "WORKSPACE_DIR": tmp_path / "workspace",
        "INPUT_DIR": tmp_path / "workspace/inputs", "OUTPUT_DIR": tmp_path / "workspace/outputs",
        "TEMP_DIR": tmp_path / "workspace/temp",
    }.items():
        value.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(settings, name, value)
    source = settings.INPUT_DIR / "fixture.mp4"
    source.write_bytes(b"source placeholder for isolated lifecycle test; never rendered")
    session = StreamingPipelineSession("review-pacing", source, tts_engine_name="edge-tts")
    session.initialized, session.total_duration = True, 180.
    session._chunked_source_started, session._source_prepared_seconds = True, 180.
    session.is_running = True
    session.tts_engine = object()
    monkeypatch.setattr(session, "_ensure_tts_engine", lambda: None)
    focus = SegmentItem(112, 169.9, 170.84, .94)
    focus.status, focus.text_zh, focus.final_vi = "READY", "旧文本", "Lời cũ."
    focus.audio_path = str(session.segments_dir / "previous.wav")
    with wave.open(focus.audio_path, "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x10\x00" * 12000)
    focus.verification = {"status": "verified", "semantic_verified": True}
    following = SegmentItem(113, 170.84, 171.68, .84)
    following.status, following.text_zh, following.final_vi = "WAITING", "旧续句", "Để đùa đâu."
    session.segments = {112: focus, 113: following}
    return session


def review_response():
    return {"segments": {
        112: {"id": 112, "text_zh": "我真的不是回来", "final_vi": "Thật sự không phải về đây.",
            "needs_review": False, "verification": {"status": "corrected", "semantic_verified": True}},
        113: {"id": 113, "text_zh": "跟你开玩笑的", "final_vi": "Để đùa đâu.",
            "needs_review": False, "verification": {"status": "verified", "semantic_verified": True}},
    }, "summary": {"checked": 2, "verified": 1, "corrected": 1, "unresolved": 0, "manual": 0}}


@pytest.mark.parametrize("failure_kind", ["budget", "rejected_rewrite", "unmeasured_rewrite", "provider"])
def test_review_audio_failure_retains_corrected_revision_and_only_measured_timing(
        reviewed_session, monkeypatch, failure_kind):
    session = reviewed_session
    focus = session.segments[112]
    old_path, old_bytes = focus.audio_path, Path(focus.audio_path).read_bytes()
    response = review_response()
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", lambda *args, **kwargs: deepcopy(response))
    if failure_kind == "provider":
        error = RuntimeError("TTS provider disconnected")
    elif failure_kind == "budget":
        error = pipeline.SpeechBudgetError("Measured waveform exceeds safe slot")
    else:
        error = pipeline.PacingReviewRejected("Rejected shortening is not publishable",
            candidate="Một đề xuất sai.", reason="Does not preserve source", code="semantic_mismatch")
    measured = failure_kind in {"budget", "rejected_rewrite"}
    if measured:
        error.required_dub_duration = 1.398
        error.candidate_text = response["segments"][112]["final_vi"]
        error.pacing_verification = None

    async def fail_fit(*args, **kwargs):
        raise error
    monkeypatch.setattr(session, "_fit_dub", fail_fit)
    asyncio.run(session._review_translations(regenerate_audio=True))

    assert focus.status == "FAILED" and focus.failed_stage == "TTS"
    assert focus.revision == 1
    assert focus.text_zh == "我真的不是回来"
    assert focus.final_vi == "Thật sự không phải về đây."
    assert focus.verification["status"] == "corrected"
    assert focus.audio_path is None and focus.subtitle_cues == []
    assert Path(old_path).read_bytes() == old_bytes
    assert focus.error == str(error)
    assert 112 in session._pacing_failures if measured else not session._pacing_failures
    if measured:
        assert focus.timing_issue["code"] == "TIMING_CONFLICT"
        assert focus.timing_issue["required_seconds"] == 1.398
        record = session._pacing_failures[112]
        assert record["candidate"] == focus.final_vi and record["revision"] == focus.revision
        assert {row["id"]: row["text_zh"] for row in record["context"]} == {
            112: "我真的不是回来", 113: "跟你开玩笑的"}
        assert session._pacing_failure_owned(focus, record)
        session.segments[113].text_zh = "A later independent source correction"
        assert not session._pacing_failure_owned(focus, record)
    else:
        assert focus.timing_issue is None
    session.persist()
    saved = next(row for row in _read(session.task_id)["segments"] if row["id"] == 112)
    assert saved["timing_issue"] == focus.timing_issue
    assert saved["status"] == "FAILED" and saved["audio_path"] is None
    assert saved["text_zh"] == focus.text_zh and saved["final_vi"] == focus.final_vi
