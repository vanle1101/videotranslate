"""Offline production scheduler integration; controlled reviewer, no providers."""
import asyncio
from unittest.mock import AsyncMock, Mock

from test_transcript_edit import session
from core.review_retry import review_input_hashes, schedule_review_failures
from core.streaming.pipeline import SegmentItem
from core.streaming.retry_schedule import schedule_retry
import pytest


def prepare(owner, monkeypatch):
    owner.is_running, owner.is_stopped = True, False
    owner.pause_event.set()
    owner._restart_intent = "active"
    owner._retry_schedule = []
    monkeypatch.setattr(owner, "_persist_if_enabled", Mock())
    monkeypatch.setattr(owner, "report_progress", AsyncMock())


def enqueue_review(owner, ids, now=0):
    return schedule_review_failures(owner,[{"segment_ids":ids,"code":"provider_timeout"}],
                                    review_input_hashes(owner),now=now)


def test_ready_speech_precedes_future_review(session, monkeypatch):
    prepare(session,monkeypatch)
    row = SegmentItem(1,6,8,2)
    row.status, row.failed_stage = "FAILED", "TTS"
    row.final_vi = "Giọng đang chờ"
    session.segments[1] = row
    enqueue_review(session,[0],now=9999999999)
    schedule_retry(session,"speech","1",TimeoutError(),session._speech_retry_hash(row),now=0)
    reviewer = AsyncMock()
    monkeypatch.setattr(session,"_review_translations",reviewer)
    assert asyncio.run(session._queue_due_speech_retries())
    assert session.queue.get_nowait() == (6,1)
    reviewer.assert_not_called()


def test_review_only_scheduler_bounds_groups_and_retires_success(session, monkeypatch):
    prepare(session,monkeypatch)
    for sid in range(1,10):
        row = SegmentItem(sid, sid*4, sid*4+2,2)
        row.status,row.final_vi,row.text_zh="READY","Bản nháp","源"
        session.segments[sid] = row
    enqueue_review(session,list(session.segments))
    calls=[]
    async def review(**kwargs):
        calls.append(kwargs)
    monkeypatch.setattr(session,"_review_translations",review)
    assert not asyncio.run(session._queue_due_speech_retries())
    assert sorted(len(call["segment_ids"]) for call in calls) == [2,4,4]
    assert all(call["defer_audio"] and call["regenerate_audio"] for call in calls)
    assert not session._retry_schedule


def test_review_updated_audio_returns_to_speech_before_next_group(session, monkeypatch):
    prepare(session,monkeypatch)
    enqueue_review(session,[0])
    async def review(**kwargs):
        row=session.segments[0]
        row.status,row._retry_synthesis="WAITING",True
    monkeypatch.setattr(session,"_review_translations",review)
    assert asyncio.run(session._queue_due_speech_retries())
    assert session.queue.get_nowait() == (2,0)


def test_terminal_review_failure_is_not_retried_forever(session, monkeypatch):
    prepare(session,monkeypatch)
    enqueue_review(session,[0])
    reviewer=AsyncMock(side_effect=ValueError("schema failed"))
    monkeypatch.setattr(session,"_review_translations",reviewer)
    assert not asyncio.run(session._queue_due_speech_retries())
    assert reviewer.await_count == 1 and not session._retry_schedule
    assert session.segments[0].status == "READY"


def test_changed_manual_target_never_dispatched(session, monkeypatch):
    prepare(session,monkeypatch)
    enqueue_review(session,[0])
    session.segments[0].verification={"status":"manual"}
    reviewer=AsyncMock()
    monkeypatch.setattr(session,"_review_translations",reviewer)
    assert not asyncio.run(session._queue_due_speech_retries())
    reviewer.assert_not_called()


def test_review_only_saved_session_can_resume_without_fake_speech_error(session):
    enqueue_review(session,[0])
    session.video_path.write_bytes(b"isolated source fixture")
    session.is_running=False
    session.error=None
    assert session.can_retry


def test_cancelled_review_keeps_claim_for_restart_recovery(session,monkeypatch):
    prepare(session,monkeypatch)
    enqueue_review(session,[0])
    entered=asyncio.Event()
    async def review(**kwargs):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(session,"_review_translations",review)
    async def run():
        task=asyncio.create_task(session._queue_due_speech_retries())
        await entered.wait()
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)
    asyncio.run(run())
    assert session._retry_schedule[0]["state"] == "RUNNING"


def test_transient_review_reschedules_full_cooldown_before_next_attempt(session,monkeypatch):
    prepare(session,monkeypatch)
    enqueue_review(session,[0])
    calls=[]
    async def review(**kwargs):
        calls.append(1)
        schedule_review_failures(session,[{"segment_ids":[0],"code":"provider_timeout"}],
                                review_input_hashes(session))
    async def delay(record):
        if calls:
            assert record["rounds"] == 2
            assert record["next_attempt_at"] >= record["updated_at"]+300
            session.is_stopped=True
            raise asyncio.CancelledError
    monkeypatch.setattr(session,"_review_translations",review)
    monkeypatch.setattr(session,"_retry_delay",delay)
    async def run():
        try:
            await session._queue_due_speech_retries()
        except asyncio.CancelledError:
            pass
    asyncio.run(run())
    assert calls == [1]
    assert session._retry_schedule[0]["state"] == "RETRY_PENDING"


@pytest.mark.parametrize("failure_code", ["provider_timeout", "asr_timeout", "asr_failed"])
def test_real_review_publication_persists_transient_then_retires_exact_success(session, monkeypatch, failure_code):
    from core.translation_review import AutomaticTranslationReviewer
    row = session.segments[0]
    original_text = row.final_vi
    original_audio = row.audio_path
    session._restart_intent = "active"
    session._retry_schedule = []
    result = {"segments": {0: {**row.to_dict(), "needs_review": True,
              "verification": {"status": "incomplete", "semantic_verified": False}}},
              "summary": {"checked": 1, "incomplete": 1},
              "retry_failures": [{"segment_ids": [0], "code": failure_code}]}
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", lambda *a, **kw: result)
    asyncio.run(session._review_translations(segment_ids={0}))
    record = session._retry_schedule[0]
    assert record["stage"] == "review" and record["scope_id"] == "0"
    assert record["next_attempt_at"] >= record["updated_at"] + 300
    assert row.final_vi == original_text and row.audio_path == original_audio
    assert row.verification["semantic_verified"] is False
    result["retry_failures"] = []
    result["segments"][0]["verification"] = {"status": "unresolved", "semantic_verified": False}
    asyncio.run(session._review_translations(segment_ids={0}))
    assert not session._retry_schedule
    assert row.verification["status"] == "unresolved"


def test_real_review_raised_transport_keeps_typed_scope(session, monkeypatch):
    from core.translation_review import AutomaticTranslationReviewer
    from core.engines.translation.opencode_client import OpenCodeTimeoutError
    error = OpenCodeTimeoutError("offline injected timeout")
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", Mock(side_effect=error))
    with pytest.raises(OpenCodeTimeoutError):
        asyncio.run(session._review_translations(segment_ids={0}))
    assert len(session._retry_schedule) == 1
    assert session._retry_schedule[0]["error_code"] == "provider_timeout"
