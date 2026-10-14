"""Real queue/coordinator tests with injected stage operations; no provider calls."""
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from test_transcript_edit import session
from core.engines.tts.edge_fallback import EdgeTTSRequestError
from core.streaming.retry_schedule import schedule_retry, transient_failure, mark_intent


@pytest.mark.parametrize("code,status", [("tts_empty_audio", None), ("tts_transport", None),
    ("tts_timeout", None), ("tts_rate_limited", 429), ("tts_http_error", 408), ("tts_http_error", 503)])
def test_typed_edge_transient_codes_are_retryable(code, status):
    error = EdgeTTSRequestError("fixture", code=code, status=status, retryable=True)
    assert transient_failure(error) == code
    wrapped = RuntimeError("wrapped")
    wrapped.__cause__ = error
    assert transient_failure(wrapped) == code


@pytest.mark.parametrize("code,status,retryable", [("tts_http_error", 401, True),
    ("tts_http_error", 403, True), ("tts_http_error", 400, True), ("tts_http_error", "503", True),
    ("tts_tls_certificate", None, True), ("tts_tls_handshake", None, True),
    ("tts_client_outdated", None, True), ("tts_timeout", None, False), ("tts_unknown", None, True)])
def test_edge_hard_failures_override_nested_timeout(code, status, retryable):
    error = EdgeTTSRequestError("fixture", code=code, status=status, retryable=retryable)
    error.__cause__ = TimeoutError()
    assert transient_failure(error) is None


@pytest.fixture
def retry_session(session, monkeypatch):
    session.is_running = True
    session.is_stopped = False
    session.is_paused = False
    session.pause_event.set()
    session._restart_intent = "active"
    session._retry_schedule = []
    session.queue = asyncio.PriorityQueue()
    row = session.segments[0]
    row.status, row.failed_stage, row.error = "FAILED", "TTS", "temporary"
    row._retry_synthesis = True
    monkeypatch.setattr(session, "_persist_if_enabled", Mock())
    monkeypatch.setattr(session, "report_progress", AsyncMock())
    return session


def schedule_speech(owner, now):
    row = owner.segments[0]
    return schedule_retry(owner, "speech", str(row.id), TimeoutError(), owner._speech_retry_hash(row), now=now)


def test_actual_queue_waits_full_cooldown_then_claims_one_wave(retry_session, monkeypatch):
    owner = retry_session
    now = [100.]
    monkeypatch.setattr("time.time", lambda: now[0])
    schedule_speech(owner, 100)
    async def scenario():
        task = asyncio.create_task(owner._queue_due_speech_retries())
        await asyncio.sleep(.02)
        assert not task.done() and owner.queue.empty()
        now[0] = 399
        await asyncio.sleep(.27)
        assert not task.done() and owner.queue.empty()
        now[0] = 400
        assert await asyncio.wait_for(task, 1)
        assert owner.queue.qsize() == 1
        assert await owner.queue.get() == (2, 0)
        assert owner.segments[0].status == "WAITING"
        assert owner._retry_schedule[0]["state"] == "RUNNING"
    asyncio.run(scenario())


def test_pause_keeps_retry_waiting_until_resume(retry_session, monkeypatch):
    owner = retry_session
    now = [100.]
    monkeypatch.setattr("time.time", lambda: now[0])
    schedule_speech(owner, 100)
    async def scenario():
        task = asyncio.create_task(owner._queue_due_speech_retries())
        await asyncio.sleep(.02)
        owner.pause()
        now[0] = 500
        await asyncio.sleep(.3)
        assert not task.done() and owner.queue.empty()
        assert owner._restart_intent == "paused"
        owner.resume()
        assert await asyncio.wait_for(task, 1)
    asyncio.run(scenario())


def test_already_paused_owner_preserves_durable_intent_when_entering_retry_wait(retry_session):
    owner = retry_session
    schedule_speech(owner, 9999999999)
    async def scenario():
        owner.pause()
        task = asyncio.create_task(owner._queue_due_speech_retries())
        try:
            await asyncio.sleep(.02)
            assert owner._restart_intent == "paused"
            assert owner.queue.empty()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_stop_interrupts_cooldown_without_queueing(retry_session, monkeypatch):
    owner = retry_session
    schedule_speech(owner, 9999999999)
    monkeypatch.setattr(owner, "_release_runtime", Mock())
    async def scenario():
        task = asyncio.create_task(owner._queue_due_speech_retries())
        await asyncio.sleep(.02)
        owner.stop()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert owner.queue.empty() and owner._restart_intent == "user_stopped"
    asyncio.run(scenario())


def test_changed_revision_drops_old_speech_retry_without_touching_edit(retry_session):
    owner = retry_session
    schedule_speech(owner, 0)
    row = owner.segments[0]
    row.revision += 1
    row.final_vi = "Người dùng sửa lời"
    assert not asyncio.run(owner._queue_due_speech_retries())
    assert not owner._retry_schedule and owner.queue.empty()
    assert row.final_vi == "Người dùng sửa lời"


def test_speech_becoming_ready_during_wait_is_not_requeued(retry_session, monkeypatch):
    owner = retry_session
    schedule_speech(owner, 0)
    async def completed_during_wait(record):
        owner.segments[0].status = "READY"
    monkeypatch.setattr(owner, "_retry_delay", completed_during_wait)
    assert not asyncio.run(owner._queue_due_speech_retries())
    assert owner.segments[0].status == "READY"
    assert owner.queue.empty() and not owner._retry_schedule


def test_revision_changed_during_wait_removes_obsolete_retry(retry_session, monkeypatch):
    owner = retry_session
    schedule_speech(owner, 0)
    async def edited_during_wait(record):
        owner.segments[0].revision += 1
    monkeypatch.setattr(owner, "_retry_delay", edited_during_wait)
    assert not asyncio.run(owner._queue_due_speech_retries())
    assert not owner._retry_schedule and owner.queue.empty()


def test_source_cooldown_does_not_block_unscheduled_sibling(retry_session):
    owner = retry_session
    from core.streaming.recovery import interval_id
    schedule_retry(owner, "source", interval_id(0, 30), TimeoutError(), "a" * 64, now=9999999999)
    intervals = [(0, 30), (30, 60)]
    assert asyncio.run(owner._next_retry_interval(intervals, may_wait=False)) == (30, 60)
    assert asyncio.run(owner._next_retry_interval(intervals, may_wait=False)) is None
    assert intervals == [(0, 30)]


def test_source_retry_does_not_claim_obsolete_chunk_revision(retry_session):
    owner = retry_session
    from core.streaming.recovery import interval_id, reserve
    key = interval_id(0, 30)
    schedule_retry(owner, "source", key, TimeoutError(), "a" * 64, now=0)
    owner._chunk_jobs = []
    reserve(owner._chunk_jobs, 0, 30, "b" * 64)
    assert asyncio.run(owner._next_retry_interval([(0, 30)])) == (0, 30)
    assert not owner._retry_schedule, "old hash must not remain RUNNING after new source is selected"


@pytest.mark.parametrize("paused,expected", [(False,"shutdown"),(True,"paused")])
def test_shutdown_preserves_retry_or_pause_intent(retry_session, monkeypatch, paused, expected):
    owner = retry_session
    monkeypatch.setattr(owner, "_release_runtime", Mock())
    if paused:
        owner.pause()
    owner.shutdown()
    assert owner._restart_intent == expected and owner.is_stopped


def test_shutdown_does_not_reactivate_user_stopped_project(retry_session, monkeypatch):
    owner = retry_session
    monkeypatch.setattr(owner, "_release_runtime", Mock())
    owner.stop()
    owner.shutdown()
    assert owner._restart_intent == "user_stopped"


def test_worker_retries_only_failed_speech_after_durable_cooldown(retry_session, monkeypatch):
    owner = retry_session
    clock = [100.]
    monkeypatch.setattr("time.time", lambda: clock[0])
    monkeypatch.setattr(owner, "_update_ready", AsyncMock())
    monkeypatch.setattr(owner, "_recover_pacing_after_source", AsyncMock())
    monkeypatch.setattr(owner, "_release_runtime", Mock())
    monkeypatch.setattr(owner, "emit", AsyncMock())
    monkeypatch.setattr(owner, "get_telemetry", lambda: {})
    calls = []
    async def synthesis(row):
        calls.append(row.id)
        row.status = "TTS"
        if len(calls) == 1:
            raise EdgeTTSRequestError("empty", code="tts_empty_audio", retryable=True)
        row.status = "READY"
    async def advance_after_checkpoint(record):
        assert owner._persist_if_enabled.call_count > 0
        assert record["next_attempt_at"] == 400
        clock[0] = record["next_attempt_at"]
    monkeypatch.setattr(owner, "_synthesize_segment", synthesis)
    monkeypatch.setattr(owner, "_retry_delay", advance_after_checkpoint)
    owner.queue.put_nowait((2, 0))
    asyncio.run(owner._worker_loop())
    assert calls == [0, 0]
    assert owner.segments[0].status == "READY"
    assert owner._retry_schedule == [] and owner.queue.empty()


def test_worker_late_failure_does_not_overwrite_new_edit(retry_session, monkeypatch):
    owner = retry_session
    monkeypatch.setattr(owner, "_update_ready", AsyncMock())
    monkeypatch.setattr(owner, "_recover_pacing_after_source", AsyncMock())
    monkeypatch.setattr(owner, "_release_runtime", Mock())
    monkeypatch.setattr(owner, "emit", AsyncMock())
    monkeypatch.setattr(owner, "get_telemetry", lambda: {})
    async def late_failure(row):
        row.revision += 1
        row.final_vi, row.status, row.error = "Bản sửa mới", "READY", None
        raise TimeoutError("obsolete request")
    monkeypatch.setattr(owner, "_synthesize_segment", late_failure)
    owner.queue.put_nowait((2, 0))
    asyncio.run(owner._worker_loop())
    assert owner.segments[0].final_vi == "Bản sửa mới"
    assert owner.segments[0].status == "READY" and owner.segments[0].error is None
    assert owner._retry_schedule == []
