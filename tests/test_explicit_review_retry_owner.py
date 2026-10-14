"""Explicit Review transfers durable retry ownership; offline providers only."""
import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, Mock

import pytest

from config import settings
from core.review_retry import review_input_hashes, schedule_review_failures
from core.streaming.pipeline import SegmentEditConflict, SegmentItem
from test_transcript_edit import session


def prepare(session, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    session.video_path.write_bytes(b"isolated source fixture")
    session.is_running = False
    session._restart_intent = "completed"
    monkeypatch.setattr(session, "_persist_if_enabled", Mock())


def schedule(session):
    schedule_review_failures(session,
        [{"segment_ids": [0], "code": "provider_timeout"}], review_input_hashes(session))


def test_completed_review_hands_off_one_owner_with_real_cooldown(session, monkeypatch):
    prepare(session, monkeypatch)
    entered = asyncio.Event()
    calls = []
    async def review(**kwargs):
        calls.append(kwargs)
        assert session._restart_intent == "active"
        schedule(session)
    async def delay(record):
        assert record["next_attempt_at"] >= record["updated_at"] + 300
        assert session.review_task is None
        assert not session._exclusive_review
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(session, "_review_translations", review)
    monkeypatch.setattr(session, "_retry_delay", delay)
    monkeypatch.setattr(session, "_update_ready", AsyncMock())
    async def run():
        await session.start_automatic_review()
        owner = session.review_task
        await owner
        await asyncio.wait_for(entered.wait(), 2)
        assert session.is_running and session._restart_intent == "retry_pending"
        assert session.worker_task is not None and session.worker_task is not owner
        assert not any(kind == "finished" for kind, _ in session.events)
        with pytest.raises(SegmentEditConflict):
            await session.start_automatic_review()
        session.stop()
        await asyncio.gather(session.worker_task, return_exceptions=True)
        assert session._restart_intent == "user_stopped"
        assert session._retry_schedule[0]["state"] == "RETRY_PENDING"
    asyncio.run(run())
    assert len(calls) == 1


def test_pause_at_review_completion_keeps_worker_dormant_until_resume(session, monkeypatch):
    prepare(session, monkeypatch)
    reached_delay = asyncio.Event()
    async def review(**kwargs):
        schedule(session)
        session.pause()
    async def delay(record):
        reached_delay.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(session, "_review_translations", review)
    monkeypatch.setattr(session, "_retry_delay", delay)
    monkeypatch.setattr(session, "_update_ready", AsyncMock())
    async def run():
        await session.start_automatic_review()
        await session.review_task
        await asyncio.sleep(0)
        assert session.is_paused and session._restart_intent == "paused"
        assert not reached_delay.is_set()
        assert not any(kind == "finished" for kind, _ in session.events)
        session.resume()
        await asyncio.wait_for(reached_delay.wait(), 2)
        session.stop()
        await asyncio.gather(session.worker_task, return_exceptions=True)
    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["stop", "capacity", "terminal", "manual"])
def test_stopped_or_invalid_review_never_launches_retry_worker(session, monkeypatch, outcome):
    prepare(session, monkeypatch)
    worker = AsyncMock()
    monkeypatch.setattr(session, "_worker_loop", worker)
    async def review(**kwargs):
        if outcome != "terminal":
            schedule(session)
        if outcome == "stop":
            session.stop()
            raise asyncio.CancelledError
        if outcome == "capacity":
            session._persistence_capacity_failed = True
        if outcome == "manual":
            session.segments[0].verification = {"status": "manual"}
        if outcome == "terminal":
            raise ValueError("invalid schema, no transient descriptor")
    monkeypatch.setattr(session, "_review_translations", review)
    async def run():
        await session.start_automatic_review()
        await asyncio.gather(session.review_task, return_exceptions=True)
        assert session.worker_task is None
        assert not session.is_running
    asyncio.run(run())
    worker.assert_not_called()


def test_review_handoff_preserves_unrelated_failed_speech(session, monkeypatch):
    prepare(session, monkeypatch)
    failed = SegmentItem(1, 6, 8, 2)
    failed.status, failed.failed_stage, failed.final_vi = "FAILED", "ALIGNING", "Chưa vừa"
    session.segments[1] = failed
    session.segments[0].needs_review = True
    before = deepcopy(vars(failed))
    async def review(**kwargs):
        schedule(session)
    entered = asyncio.Event()
    async def delay(record):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(session, "_review_translations", review)
    monkeypatch.setattr(session, "_retry_delay", delay)
    monkeypatch.setattr(session, "_update_ready", AsyncMock())
    async def run():
        await session.start_automatic_review()
        await session.review_task
        await asyncio.wait_for(entered.wait(), 2)
        assert session.queue.empty()
        assert vars(failed) == before
        session.stop()
        await asyncio.gather(session.worker_task, return_exceptions=True)
    asyncio.run(run())
    session.tts_engine.synthesize.assert_not_called()


def test_review_retry_finishes_without_attempting_unrelated_pacing_recovery(session, monkeypatch):
    prepare(session, monkeypatch)
    attempts = []
    async def review(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            schedule_review_failures(session,
                [{"segment_ids": [0], "code": "provider_timeout"}],
                review_input_hashes(session), now=0)
    monkeypatch.setattr(session, "_review_translations", review)
    monkeypatch.setattr(session, "_update_ready", AsyncMock())
    recovery = AsyncMock()
    monkeypatch.setattr(session, "_recover_pacing_after_source", recovery)
    async def run():
        await session.start_automatic_review()
        await session.review_task
        await session.worker_task
        assert len(attempts) == 2
        assert not session._retry_schedule
        assert not session.is_running
        assert sum(kind == "finished" for kind, _ in session.events) == 1
    asyncio.run(run())
    recovery.assert_not_called()
