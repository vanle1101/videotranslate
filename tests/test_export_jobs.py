"""HTTP creation is bounded while render owns a separately polled task."""
import asyncio
import threading
import wave
from unittest.mock import AsyncMock

import httpx
import pytest

import main
from test_auto_review_export import session


@pytest.fixture
def ready(session, tmp_path, monkeypatch):
    item, exporter = session
    path = tmp_path / "voice.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x01\x00" * 24000)
    item.segments[0].audio_path = str(path)
    monkeypatch.setattr(main, "validate_live_output", AsyncMock())
    return item, exporter


@pytest.mark.parametrize("cancel", [False, True])
def test_job_returns_before_render_finishes_and_poll_reports_terminal_result(ready, tmp_path, cancel):
    item, exporter = ready
    item.review_summary["status"] = "incomplete"
    entered, release = threading.Event(), threading.Event()
    final = tmp_path / "reviewed.mp4"
    final.write_bytes(b"prior validated result")
    def render(**kwargs):
        entered.set()
        assert release.wait(5)
        if kwargs["cancel_check"]():
            raise RuntimeError("cancelled")
        rendered = tmp_path / "rendered.mp4"
        rendered.write_bytes(b"new validated result")
        kwargs["publish_callback"](rendered, final)
        return {"output_filename": final.name, "elapsed_seconds": 1}
    exporter.export.side_effect = render
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as api:
            response = await asyncio.wait_for(api.post("/api/streaming/export-hq/jobs", json={"task_id": item.task_id}), 2)
            assert response.status_code == 202 and response.json()["status"] == "RUNNING"
            owner = item.export_task
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                duplicate = await api.post("/api/streaming/export-hq/jobs", json={"task_id": item.task_id})
                assert duplicate.status_code == 409
                assert (await api.get(response.json()["status_url"])).json()["status"] == "RUNNING"
                if cancel:
                    assert (await api.post(f"/api/streaming/export-hq/cancel/{item.task_id}")).status_code == 200
                release.set()
                await asyncio.gather(owner, return_exceptions=True)
                result = (await api.get(response.json()["status_url"])).json()
                assert result["status"] == ("CANCELLED" if cancel else "COMPLETED")
                if cancel:
                    assert final.read_bytes() == b"prior validated result"
                else:
                    assert final.read_bytes() == b"new validated result"
                    assert result["output_is_draft"] and not result["output_incomplete"]
                    assert result["output_review_segment_ids"] == [0]
                    assert result["output_quality_label"] == "Bản nháp — cần kiểm tra"
            finally:
                release.set()
                await asyncio.gather(owner, return_exceptions=True)
    asyncio.run(run())


@pytest.mark.parametrize("gap", ["missing_audio", "corrupt_audio", "source", "text"])
def test_job_rejects_media_gaps_before_registration(ready, gap):
    item, exporter = ready
    if gap == "missing_audio":
        item.segments[0].audio_path = "not-present.wav"
    elif gap == "corrupt_audio":
        from pathlib import Path
        Path(item.segments[0].audio_path).write_bytes(b"RIFF")
    elif gap == "source":
        item.initialized = False
    else:
        item.segments[0].final_vi = ""
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as api:
            response = await api.post("/api/streaming/export-hq/jobs", json={"task_id": item.task_id})
            assert response.status_code == 409
    asyncio.run(run())
    exporter.export.assert_not_called()
    assert not main.active_export_tasks


def test_startup_restores_only_through_intent_aware_lifecycle(monkeypatch):
    lifecycle = AsyncMock(return_value=[])
    monkeypatch.setattr("core.streaming.retry_lifecycle.resume_interrupted_sessions", lifecycle)
    events = AsyncMock()
    monkeypatch.setattr(main, "broadcast_session_event", events)
    async def run():
        await main.resume_saved_video_work()
        callback = lifecycle.call_args.kwargs["event_callback_factory"]("owned")
        await callback("progress", {"stage": "resume"})
    asyncio.run(run())
    events.assert_awaited_once_with("owned", "progress", {"stage": "resume"})


def test_shutdown_drains_owned_task_without_user_stop(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    async def run():
        drained = asyncio.Event()
        async def worker():
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                drained.set()
        owner = asyncio.create_task(worker())
        await asyncio.sleep(0)
        session = SimpleNamespace(start_task=owner, shutdown=Mock(side_effect=owner.cancel), stop=Mock())
        monkeypatch.setattr(main, "active_streaming_sessions", {"owned": session})
        await main.drain_video_work()
        assert drained.is_set() and owner.done()
        session.shutdown.assert_called_once()
        session.stop.assert_not_called()
    asyncio.run(run())
