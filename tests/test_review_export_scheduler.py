"""Real coordinator/export handoff; injected renderer is not media acceptance."""
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

import main
from core.streaming.export_lease import acquire_review_export, release_review_export
from test_transcript_edit import session
from test_review_retry_scheduler import prepare, enqueue_review


def complete_media(owner, monkeypatch):
    prepare(owner, monkeypatch)
    owner.translation_mode = "full"
    owner._visual_prepass_complete = True
    owner._source_prepared_seconds = owner._visual_completed_seconds = owner.total_duration
    owner._chunk_jobs = [{"id": "chunk-0", "start": 0, "end": owner.total_duration, "state": "COMPLETED"}]
    owner.review_summary = {"status": "incomplete"}
    monkeypatch.setattr(owner, "final_media_metadata", lambda: {"final_media_blocked": False})
    enqueue_review(owner, [0])


def test_real_coordinator_exports_then_continues_existing_review(session, monkeypatch, tmp_path):
    complete_media(session, monkeypatch)
    session.auto_export_result = True
    monkeypatch.setattr(main, "stream_sockets", {})
    monkeypatch.setattr(main.settings, "OUTPUT_DIR", tmp_path)
    order = []
    exporter = Mock()
    def render(**kwargs):
        assert session._review_export_boundary
        assert session._review_export_lease is not None
        assert session._retry_schedule[0]["state"] == "RETRY_PENDING"
        order.append("render")
        return {"output_filename": "draft.mp4", "elapsed_seconds": 1}
    exporter.export.side_effect = render
    monkeypatch.setattr(main, "HQExporter", Mock(return_value=exporter))
    async def event(kind, data):
        await main.broadcast_session_event(session.task_id, kind, data)
    session.event_callback = event
    async def review(**kwargs):
        assert not session._review_export_boundary
        assert session._review_export_lease is None
        assert session.is_running
        order.append("review")
    monkeypatch.setattr(session, "_review_translations", review)
    assert not asyncio.run(session._queue_due_speech_retries())
    assert order == ["render", "review"]
    assert session.output_filename == "draft.mp4"
    assert not session._retry_schedule and session.is_running
    assert not any(call.args[0] == "complete" for call in session.report_progress.await_args_list)


@pytest.mark.parametrize("stop", [False, True])
def test_manual_export_during_cooldown_blocks_review_and_honors_stop(session, monkeypatch, stop):
    complete_media(session, monkeypatch)
    held = asyncio.Event()
    reviewer = AsyncMock()
    monkeypatch.setattr(session, "_review_translations", reviewer)
    async def delay(record):
        lease = acquire_review_export(session)
        assert lease is not None
        held.set()
    monkeypatch.setattr(session, "_retry_delay", delay)
    async def run():
        owner = asyncio.create_task(session._queue_due_speech_retries())
        await asyncio.wait_for(held.wait(), 2)
        await asyncio.sleep(0)
        assert not owner.done() and session._review_export_boundary
        assert session._retry_schedule[0]["state"] == "RETRY_PENDING"
        reviewer.assert_not_called()
        if stop:
            session._stop_draining = True
        release_review_export(session, session._review_export_lease)
        if stop:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(owner, 2)
            reviewer.assert_not_called()
            assert session._retry_schedule[0]["state"] == "RETRY_PENDING"
        else:
            assert not await asyncio.wait_for(owner, 2)
            reviewer.assert_awaited_once()
        assert not session._review_export_boundary
    asyncio.run(run())


def test_cancel_during_export_keeps_coordinator_boundary_until_drain(session, monkeypatch):
    complete_media(session, monkeypatch)
    entered = asyncio.Event()
    reviewer = AsyncMock()
    monkeypatch.setattr(session, "_review_translations", reviewer)
    async def event(kind, data):
        if kind == "media_ready_review_pending":
            assert acquire_review_export(session) is not None
            entered.set()
    session.event_callback = event
    async def run():
        owner = asyncio.create_task(session._queue_due_speech_retries())
        await asyncio.wait_for(entered.wait(), 2)
        owner.cancel()
        await asyncio.sleep(0)
        assert not owner.done() and session._review_export_boundary
        assert session._retry_schedule[0]["state"] == "RETRY_PENDING"
        release_review_export(session, session._review_export_lease)
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(owner, 2)
        assert not session._review_export_boundary
        reviewer.assert_not_called()
    asyncio.run(run())


def test_worker_exit_with_pending_review_never_marks_complete(session, monkeypatch):
    complete_media(session, monkeypatch)
    monkeypatch.setattr(session, "_update_ready", AsyncMock())
    monkeypatch.setattr(session, "_queue_due_speech_retries", AsyncMock(return_value=False))
    monkeypatch.setattr(session, "_release_runtime", Mock())
    asyncio.run(session._worker_loop(recover_pacing=False))
    assert session._restart_intent == "retry_pending"
    assert session._retry_schedule[0]["state"] == "RETRY_PENDING"
    assert not any(call.args[0] == "complete" for call in session.report_progress.await_args_list)


def test_progress_exposes_export_only_at_unleased_review_boundary(session, monkeypatch):
    complete_media(session, monkeypatch)
    assert session.get_progress()["can_export_review_draft"] is False
    session._review_export_boundary = True
    assert session.get_progress()["can_export_review_draft"] is True
    async def run():
        lease = acquire_review_export(session)
        assert lease is not None
        assert session.get_progress()["can_export_review_draft"] is False
        release_review_export(session, lease)
        assert session.get_progress()["can_export_review_draft"] is True
        session._stop_draining = True
        assert session.get_progress()["can_export_review_draft"] is False
    asyncio.run(run())
