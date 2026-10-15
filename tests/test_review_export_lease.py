"""Review/export ownership tests. Mocked rendering is not MP4 acceptance."""
import asyncio
import copy
import threading
import wave

import pytest
from fastapi import HTTPException

import main
from core.streaming.export_lease import acquire_review_export, release_review_export, wait_review_export
from test_auto_review_export import session


def ready_boundary(item):
    item.is_running = True
    item.translation_mode = "full"
    item._review_export_boundary = True
    item._visual_prepass_complete = True
    item._source_prepared_seconds = item._visual_completed_seconds = item.total_duration
    item._chunk_jobs = [{"state": "COMPLETED"}]
    item.queue = asyncio.PriorityQueue()
    item._retry_schedule = [{"stage": "review", "state": "RETRY_PENDING", "scope_id": "0",
                             "input_hash": "a" * 64, "next_attempt_at": 900.}]
    item._restart_intent = "retry_pending"
    item.review_summary = {"status": "incomplete", "incomplete": 1}
    item.final_media_metadata = lambda: {"final_media_blocked": False}
    item._cached_dub_audio_duration = lambda path: 2.
    item.get_progress.return_value = {"status": "RUNNING", "phase": "retry", "retry_pending_count": 1}


@pytest.mark.parametrize("first_duration,allowed", [(1., True), (1.75, False), (2.1, False)])
def test_measured_wav_occupancy_matches_actual_renderer_despite_overlapping_slots(session, tmp_path, first_duration, allowed):
    from core.streaming.pipeline import SegmentItem, StreamingPipelineSession
    from core.streaming.export import HQExporter
    from core.streaming.export_lease import review_export_ready
    item, _ = session
    ready_boundary(item)
    item.total_duration = item._source_prepared_seconds = item._visual_completed_seconds = 3.
    rows = []
    for sid, (start, end, duration) in enumerate([(0., 2., first_duration), (1.5, 3., 1.)]):
        path = tmp_path / f"voice_{sid}.wav"
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(44100)
            audio.writeframes(b"\x01\x00" * round(duration * 44100))
        row = SegmentItem(sid, start, end, end-start)
        row.status, row.text_zh, row.final_vi = "READY", "fixture", "Lời kiểm thử"
        row.audio_path, row.audio_url = str(path), f"/fixture/{sid}"
        rows.append(row)
    item.segments = {row.id: row for row in rows}
    item._cached_dub_audio_duration = StreamingPipelineSession._dub_audio_duration
    assert review_export_ready(item) is allowed
    data = [dict(row.to_dict(), audio_path=row.audio_path) for row in rows]
    if allowed:
        output = tmp_path / "timeline.wav"
        HQExporter._assemble_voice_timeline(data, 3., output)
        with wave.open(str(output), "rb") as audio:
            assert audio.getnframes() / audio.getframerate() == 3.
    else:
        with pytest.raises(ValueError):
            HQExporter._assemble_voice_timeline(data, 3., tmp_path / "rejected.wav")


def test_complete_draft_exports_at_review_boundary_without_finishing_review(session):
    item, exporter = session
    ready_boundary(item)
    ledger = copy.deepcopy(item._retry_schedule)
    async def run():
        await main.broadcast_session_event(item.task_id, "media_ready_review_pending", {})
        lease = item._review_export_lease
        assert lease is not None and not lease.done()
        await item.auto_export_task
        assert lease.done() and item._review_export_lease is None
    asyncio.run(run())
    exporter.export.assert_called_once()
    assert item.output_filename == "reviewed.mp4"
    assert item.is_running and item._retry_schedule == ledger and item._restart_intent == "retry_pending"
    assert item.review_summary["status"] == "incomplete"
    item.report_progress.assert_not_awaited()


def test_cancelled_coordinator_retains_boundary_until_exporter_drains(session):
    item, _ = session
    ready_boundary(item)
    async def run():
        lease = acquire_review_export(item)
        async def coordinate():
            try:
                await wait_review_export(item)
            finally:
                item._review_export_boundary = False
        owner = asyncio.create_task(coordinate())
        await asyncio.sleep(0)
        owner.cancel()
        await asyncio.sleep(0)
        owner.cancel()
        await asyncio.sleep(0)
        assert not owner.done() and item._review_export_boundary
        assert not lease.done()
        release_review_export(item, lease)
        with pytest.raises(asyncio.CancelledError):
            await owner
        assert not item._review_export_boundary
    asyncio.run(run())


@pytest.mark.parametrize("condition", ["text", "audio", "source", "chunk", "queue", "speech_retry",
    "claimed_review", "review_running", "paused", "stopped", "editing", "not_boundary", "timeline"])
def test_export_lease_never_bypasses_required_media_or_mutation_gates(session, condition):
    item, exporter = session
    ready_boundary(item)
    if condition == "text": item.segments[0].final_vi = ""
    elif condition == "audio": item.final_media_metadata = lambda: {"final_media_blocked": True}
    elif condition == "source": item._source_prepared_seconds = 1
    elif condition == "chunk": item._chunk_jobs[0]["state"] = "RUNNING"
    elif condition == "queue": item.queue.put_nowait((0, 0))
    elif condition == "speech_retry": item._retry_schedule[0]["stage"] = "speech"
    elif condition == "claimed_review": item._retry_schedule[0]["state"] = "RUNNING"
    elif condition == "review_running": item.review_summary["status"] = "running"
    elif condition == "paused": item.is_paused = True
    elif condition == "stopped": item.is_stopped = True
    elif condition == "editing": item.is_editing = True
    elif condition == "timeline": item.segments[0].end = 3
    else: item._review_export_boundary = False
    async def run():
        assert acquire_review_export(item) is None
        with pytest.raises(HTTPException):
            await main.export_hq(main.ExportHQRequest(task_id=item.task_id))
        assert getattr(item, "_review_export_lease", None) is None
    asyncio.run(run())
    exporter.export.assert_not_called()


def test_export_lease_is_exclusive_and_released_on_render_failure_preserving_old_output(session):
    item, exporter = session
    ready_boundary(item)
    item.output_filename, item.output_video_url = "previous.mp4", "/api/outputs/previous.mp4"
    entered, release = threading.Event(), threading.Event()
    def render(**kwargs):
        entered.set()
        assert release.wait(3)
        raise RuntimeError("controlled render failure")
    exporter.export.side_effect = render
    async def run():
        task = asyncio.create_task(main.export_hq(main.ExportHQRequest(task_id=item.task_id)))
        try:
            while not entered.is_set(): await asyncio.sleep(.01)
            assert acquire_review_export(item) is None
            with pytest.raises(HTTPException):
                await main.export_hq(main.ExportHQRequest(task_id=item.task_id))
            assert item._review_export_lease is not None
            release.set()
            with pytest.raises(HTTPException): await task
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        assert item._review_export_lease is None
    asyncio.run(run())
    assert item.output_filename == "previous.mp4" and item.output_video_url.endswith("previous.mp4")
    assert item._retry_schedule and item._restart_intent == "retry_pending"


def test_stale_lease_release_cannot_clear_new_owner(session):
    item, _ = session
    ready_boundary(item)
    async def run():
        first = acquire_review_export(item)
        release_review_export(item, first)
        second = acquire_review_export(item)
        release_review_export(item, first)
        assert item._review_export_lease is second and not second.done()
        release_review_export(item, second)
    asyncio.run(run())


def test_reserved_lease_blocks_edit_before_export_task_has_started(session):
    item, _ = session
    ready_boundary(item)
    async def run():
        lease = acquire_review_export(item)
        try:
            with pytest.raises(HTTPException) as failure:
                await main.edit_streaming_segment(item.task_id, 0, main.SegmentEditRequest(final_vi="Changed"))
            assert failure.value.status_code == 409
        finally:
            release_review_export(item, lease)
    asyncio.run(run())


def test_stop_during_borrowed_export_keeps_lease_until_media_thread_drains(session):
    item, exporter = session
    ready_boundary(item)
    entered, release, drained = threading.Event(), threading.Event(), threading.Event()
    def render(**kwargs):
        entered.set()
        try:
            assert release.wait(3)
            assert kwargs["cancel_check"]()
            raise RuntimeError("cancelled render")
        finally:
            drained.set()
    exporter.export.side_effect = render
    async def run():
        task = asyncio.create_task(main.export_hq(main.ExportHQRequest(task_id=item.task_id)))
        try:
            while not entered.is_set(): await asyncio.sleep(.01)
            item.is_stopped = True
            task.cancel()
            await asyncio.sleep(.03)
            assert not task.done() and item._review_export_lease is not None
            release.set()
            with pytest.raises(asyncio.CancelledError): await task
            assert drained.is_set() and item._review_export_lease is None
            assert item._retry_schedule
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


def test_stop_closes_review_admission_before_releasing_export(session):
    item, exporter = session
    ready_boundary(item)
    item.stop = lambda: setattr(item, "is_stopped", True)
    entered, release = threading.Event(), threading.Event()
    def render(**kwargs):
        entered.set()
        assert release.wait(3)
        assert kwargs["cancel_check"]()
        raise RuntimeError("cancelled render")
    exporter.export.side_effect = render
    async def run():
        owner = asyncio.create_task(main.export_hq(main.ExportHQRequest(task_id=item.task_id)))
        stopper = None
        try:
            while not entered.is_set(): await asyncio.sleep(.01)
            stopper = asyncio.create_task(main.stop_task(item.task_id))
            await asyncio.sleep(.01)
            assert item._stop_draining and not stopper.done()
            assert item._review_export_lease is not None
            release.set()
            await stopper
            assert item.is_stopped and not item._stop_draining
            assert item._review_export_lease is None and item._retry_schedule
        finally:
            release.set()
            await asyncio.gather(owner, *([stopper] if stopper else []), return_exceptions=True)
    asyncio.run(run())


def test_changed_revision_cannot_publish_and_later_review_invalidates_previous_output(session,tmp_path):
    from core.streaming.pipeline import StreamingPipelineSession
    item, exporter = session
    ready_boundary(item)
    previous=tmp_path/"previous.mp4"
    previous.write_bytes(b"existing valid output identity")
    item.output_filename, item.output_video_url = previous.name, "/api/outputs/previous.mp4"
    def render(**kwargs):
        staged=tmp_path/"staged.mp4"
        staged.write_bytes(b"unpublished candidate")
        item.segments[0].revision += 1  # Simulate a misbehaving writer; commit still rejects it.
        kwargs["publish_callback"](staged,previous)
    exporter.export.side_effect=render
    async def run():
        with pytest.raises(HTTPException):
            await main.export_hq(main.ExportHQRequest(task_id=item.task_id))
        assert item._review_export_lease is None
    asyncio.run(run())
    assert previous.read_bytes()==b"existing valid output identity"
    assert item.output_video_url.endswith("previous.mp4")
    StreamingPipelineSession._invalidate_output(item)
    assert not item.output_video_url and item.auto_export_signature is None
    assert previous.is_file() and item._retry_schedule
