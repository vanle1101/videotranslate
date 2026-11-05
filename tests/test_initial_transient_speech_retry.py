"""Initial full-run TTS failures retain translation and get bounded late retries."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from config import settings
from core.engines.tts.edge_fallback import EdgeTTSRequestError
from core.streaming.pipeline import SegmentItem, StreamingPipelineSession


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr("core.streaming.pipeline.AUTOMATIC_RECOVERY_DELAYS", (0., 0., 0.))
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    current = StreamingPipelineSession("initial-speech-retry", tmp_path / "source.mp4",
                                       tts_engine_name="edge-tts")
    current.initialized = current.is_running = current._chunked_source_started = True
    current.total_duration = current._visual_completed_seconds = 12.
    current._visual_prepass_complete = True
    for sid in range(3):
        row = SegmentItem(sid, sid * 4., (sid + 1) * 4., 4.)
        row.text_zh, row.final_vi = f"源{sid}", f"Lời đã dịch {sid}."
        row.verification = {"status": "verified"}
        current.segments[sid] = row
    monkeypatch.setattr(current, "_update_ready", AsyncMock())
    monkeypatch.setattr(current, "_release_runtime", lambda: None)
    return current


def transient():
    return EdgeTTSRequestError("Service returned no audio", code="tts_empty_audio", retryable=True)


def install_failure(session, monkeypatch, *, retryable=True):
    calls = []

    async def process(row):
        calls.append(("process", row.id))
        row.status = "TTS"
        if row.id == 0:
            if retryable:
                raise transient()
            raise EdgeTTSRequestError("TLS certificate failed", code="tts_tls_certificate")
        row.status = "READY"

    monkeypatch.setattr(session, "_process_segment", process)
    return calls


def test_initial_failure_retries_speech_only_after_all_siblings(session, monkeypatch):
    calls = install_failure(session, monkeypatch)
    original = session.segments[0].final_vi

    async def speech(row):
        calls.append(("speech", row.id))
        assert row.final_vi == original
        row.status = "READY"

    monkeypatch.setattr(session, "_synthesize_segment", speech)

    async def run():
        await session._finish_visual_chunk([0, 1], set(), {})
        assert session.segments[0].status == "FAILED"
        session.queue.put_nowait((8., 2))
        await session._worker_loop()
        await session._recover_transient_speech_after_source()

    asyncio.run(run())
    assert calls == [("process", 0), ("process", 1), ("process", 2), ("speech", 0)]
    assert session.segments[0].status == "READY"
    assert session.segments[0].error is None
    assert not session.warnings


def test_queue_failure_also_gets_bounded_late_attempts(session, monkeypatch):
    calls = install_failure(session, monkeypatch)

    async def speech(row):
        calls.append(("speech", row.id))
        row.status = "TTS"
        raise transient()

    monkeypatch.setattr(session, "_synthesize_segment", speech)

    async def run():
        for row in session.segments.values():
            session.queue.put_nowait((row.start, row.id))
        await session._worker_loop()
        await session._recover_transient_speech_after_source()

    asyncio.run(run())
    assert calls == [("process", 0), ("process", 1), ("process", 2), *[("speech", 0)] * 3]
    assert session.segments[0].status == "FAILED"
    assert session.segments[0].failed_stage == "TTS"
    assert session.segments[0].final_vi == "Lời đã dịch 0."
    assert session.error and session.warnings


@pytest.mark.parametrize("change", ["manual", "text", "voice", "deleted", "nonretryable", "stopped"])
def test_late_retry_preserves_edits_stop_and_permanent_failure(session, monkeypatch, change):
    install_failure(session, monkeypatch, retryable=change != "nonretryable")
    speech = AsyncMock()
    monkeypatch.setattr(session, "_synthesize_segment", speech)

    async def run():
        await session._finish_visual_chunk([0, 1], set(), {})
        row = session.segments[0]
        if change == "manual":
            row.verification = {"status": "manual"}
        elif change == "text":
            row.final_vi = "Người dùng vừa sửa."
        elif change == "voice":
            session.voice = "vi-VN-NamMinhNeural"
        elif change == "deleted":
            del session.segments[0]
        elif change == "stopped":
            session.is_stopped = True
            with pytest.raises(asyncio.CancelledError):
                await session._recover_transient_speech_after_source()
            return
        await session._recover_transient_speech_after_source()
        assert row.status == "FAILED"

    asyncio.run(run())
    speech.assert_not_awaited()


def test_edit_while_paused_is_rechecked_before_late_retry(session, monkeypatch):
    install_failure(session, monkeypatch)
    speech = AsyncMock()
    monkeypatch.setattr(session, "_synthesize_segment", speech)

    async def run():
        await session._finish_visual_chunk([0], set(), {})
        session.pause_event.clear()
        retry = asyncio.create_task(session._recover_transient_speech_after_source())
        await asyncio.sleep(0)
        row = session.segments[0]
        row.status, row.verification = "READY", {"status": "manual"}
        session.pause_event.set()
        await retry
        assert row.status == "READY"

    asyncio.run(run())
    speech.assert_not_awaited()


def test_independent_review_speech_attempt_retires_pending_late_retry(session, monkeypatch):
    install_failure(session, monkeypatch)
    fit = AsyncMock(side_effect=transient())
    monkeypatch.setattr(session, "_ensure_tts_engine", lambda: None)
    monkeypatch.setattr(session, "_fit_dub", fit)

    async def run():
        await session._finish_visual_chunk([0], set(), {})
        row = session.segments[0]
        assert 0 in session._transient_speech_failures
        with pytest.raises(EdgeTTSRequestError):
            await session._synthesize_segment(row)
        row.status, row.failed_stage = "FAILED", "TTS"
        await session._recover_transient_speech_after_source()
        assert row.status == "FAILED"

    asyncio.run(run())
    fit.assert_awaited_once()


def test_failed_older_attempt_does_not_overwrite_manual_edit(session, monkeypatch):
    async def process(row):
        row.revision += 1
        row.status, row.verification = "READY", {"status": "manual"}
        row.final_vi = "Bản sửa đã được lưu."
        raise transient()

    monkeypatch.setattr(session, "_process_segment", process)
    asyncio.run(session._finish_visual_chunk([0], set(), {}))
    assert session.segments[0].status == "READY"
    assert session.segments[0].final_vi == "Bản sửa đã được lưu."
    assert not session._transient_speech_failures


@pytest.mark.parametrize("chunked", [False, True])
def test_translation_text_change_does_not_hide_later_speech_failure(session, monkeypatch, chunked):
    session.visual_translation = False
    session._chunked_source_started = chunked
    session.segments = {0: session.segments[0]}

    async def process(row):
        row.text_zh, row.final_vi = "新的原文", "Lời Việt vừa dịch."
        row.status = "TTS"
        raise transient()

    async def speech(row):
        assert row.final_vi == "Lời Việt vừa dịch."
        row.status = "TTS"
        raise transient()

    monkeypatch.setattr(session, "_process_segment", process)
    retry = AsyncMock(side_effect=speech)
    monkeypatch.setattr(session, "_synthesize_segment", retry)
    session.queue.put_nowait((0., 0))
    asyncio.run(session._worker_loop())
    assert session.segments[0].status == "FAILED"
    assert session.segments[0].failed_stage == "TTS"
    assert session.segments[0].final_vi == "Lời Việt vừa dịch."
    assert session.error
    assert retry.await_count == (3 if chunked else 0)
