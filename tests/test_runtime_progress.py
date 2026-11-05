"""Terminal job duration must exclude idle time and reset for real retries."""
import asyncio
import wave
from unittest.mock import AsyncMock

from config import settings
from core.streaming import pipeline


def make_session(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "WORKSPACE_DIR", tmp_path / "workspace")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    return pipeline.StreamingPipelineSession("duration-fixture", None)


def test_finished_failure_duration_stays_fixed_when_reopened_hours_later(tmp_path, monkeypatch):
    session = make_session(tmp_path, monkeypatch)
    now = [100.]
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: now[0])
    session._run_started_at = 80.
    session.review_summary = {"status": "failed"}
    asyncio.run(session.emit("finished", {}))
    now[0] = 36100.
    assert session.get_progress()["status"] == "FAILED"
    assert session.get_progress()["elapsed_seconds"] == 20.
    assert session.get_progress()["eta_seconds"] is None


def test_fresh_review_does_not_inherit_previous_terminal_duration(tmp_path, monkeypatch):
    session = make_session(tmp_path, monkeypatch)
    audio = tmp_path / "speech.wav"
    with wave.open(str(audio), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(b"\0\0" * 1600)
    segment = pipeline.SegmentItem(0, 0., .1, .1)
    segment.status = "READY"
    segment.text_zh, segment.final_vi = "测试", "Kiểm tra"
    segment.audio_path = str(audio)
    session.segments[segment.id] = segment
    now = [100.]
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: now[0])
    session.initialized = True
    session._run_started_at, session._run_finished_at = 10., 20.
    async def review(**options):
        assert session._run_finished_at is None
        now[0] = 125.
        session.review_summary = {"status": "completed"}
    monkeypatch.setattr(session, "_review_translations", review)
    monkeypatch.setattr(session, "_release_runtime", lambda: None)
    async def run():
        await session.start_automatic_review()
        await session.review_task
    asyncio.run(run())
    now[0] = 999.
    assert session.get_progress()["elapsed_seconds"] == 25.
