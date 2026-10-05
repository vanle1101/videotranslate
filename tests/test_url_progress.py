"""URL task lifecycle/progress tests with bounded stub downloads, no network/models."""
import asyncio
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi.testclient import TestClient

import main
from config import settings
from core.streaming.pipeline import SegmentItem, StreamingPipelineSession


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    monkeypatch.setattr(main, "task_history", [])
    monkeypatch.setattr(main, "stream_sockets", {})
    monkeypatch.setattr(main, "active_export_tasks", {})
    registry = {}
    monkeypatch.setattr(main, "active_streaming_sessions", registry)
    monkeypatch.setattr("core.streaming.pipeline.active_streaming_sessions", registry)
    # URL normalization has its own downloader tests; isolate network here.
    monkeypatch.setattr(main.downloader, "normalize_url", lambda text: text, raising=False)
    return tmp_path


async def wait_for(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.005)


def test_url_returns_task_before_download_finishes_and_stop_reaps_it(isolated, monkeypatch):
    started, stopped = threading.Event(), threading.Event()

    def download(url, progress_callback, cancel_check):
        started.set()
        progress_callback({"phase": "download", "stage": "Đang tải video", "progress_pct": 25,
                           "downloaded_bytes": 25, "total_bytes": 100})
        try:
            deadline = time.monotonic() + 4
            while not cancel_check() and time.monotonic() < deadline:
                time.sleep(0.005)
            raise RuntimeError("Đã hủy tải video")
        finally:
            stopped.set()

    monkeypatch.setattr(main.downloader, "download", download)
    prepare = AsyncMock()
    monkeypatch.setattr(StreamingPipelineSession, "_start", prepare)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            response = await asyncio.wait_for(client.post("/api/streaming/start-url", json={"url": "https://v.douyin.com/test/"}), 0.5)
            assert response.status_code == 200
            data = response.json()
            assert data["video_url"] is None and data["status"] == "started"
            task_id = data["task_id"]
            await wait_for(started.is_set)
            await wait_for(lambda: main.active_streaming_sessions[task_id].progress.get("progress_pct") == 25)
            task = (await client.get("/api/tasks")).json()["tasks"][0]
            assert task["status"] == "RUNNING" and task["phase"] == "download"
            assert task["progress_pct"] == 25 and task["can_stop"] and not task["can_pause"]
            assert (await client.post(f"/api/tasks/{task_id}/pause")).status_code == 409
            assert (await client.post(f"/api/tasks/{task_id}/stop")).status_code == 200
            assert stopped.is_set()
            prepare.assert_not_called()
            assert task_id not in main.active_streaming_sessions
            history = (await client.get("/api/tasks")).json()["tasks"][0]
            assert history["status"] == "STOPPED" and history["progress_pct"] == 25

    asyncio.run(run())


def test_download_failure_is_persisted_for_task_and_late_websocket(isolated, monkeypatch):
    monkeypatch.setattr(main.downloader, "download", Mock(side_effect=RuntimeError("Douyin yêu cầu đăng nhập")))

    async def run():
        result = await main.start_streaming_url(main.StreamUrlRequest(url="https://v.douyin.com/test/"))
        session = main.active_streaming_sessions[result["task_id"]]
        await wait_for(lambda: session.error is not None)
        tasks = await main.list_tasks()
        assert tasks["tasks"][0]["status"] == "FAILED"
        assert tasks["tasks"][0]["stage"] == "Douyin yêu cầu đăng nhập"
        return session

    session = asyncio.run(run())
    with TestClient(main.app) as client, client.websocket_connect(f"/ws/stream/{session.task_id}") as websocket:
        progress, error = websocket.receive_json(), websocket.receive_json()
        assert progress["type"] == "progress" and progress["status"] == "FAILED"
        assert error["type"] == "error" and error["message"] == session.error


def test_late_websocket_replays_source_and_unknown_preparation_percentage(isolated):
    session = main.create_streaming_session("source-test", isolated / "download.mp4")
    session.source_video_url = "/api/inputs/download.mp4"
    session.is_running = True
    with TestClient(main.app) as client, client.websocket_connect(f"/ws/stream/{session.task_id}") as websocket:
        progress, source = websocket.receive_json(), websocket.receive_json()
        assert progress["phase"] == "prepare" and progress["progress_pct"] is None
        assert not progress["can_pause"]
        assert source["type"] == "source_ready" and source["video_url"] == session.source_video_url


def test_late_websocket_can_resume_paused_session(isolated):
    session = main.create_streaming_session("paused-test", isolated / "video.mp4")
    session.initialized = True
    session.is_running = True
    session.pause()
    with TestClient(main.app) as client, client.websocket_connect(f"/ws/stream/{session.task_id}") as websocket:
        progress = websocket.receive_json()
        assert progress["type"] == "progress" and progress["status"] == "PAUSED"
        assert progress["can_resume"] and progress["can_stop"] and not progress["can_pause"]
        assert websocket.receive_json()["type"] == "init"
        assert websocket.receive_json()["type"] == "telemetry"


def test_successful_download_emits_source_before_pipeline_start(isolated, monkeypatch):
    video = isolated / "video_abc.mp4"
    video.write_bytes(b"test")
    monkeypatch.setattr(main.downloader, "download", Mock(return_value={"file_path": str(video), "is_local": False}))
    events = []
    session = StreamingPipelineSession("source-success", None, event_callback=lambda kind, data: events.append((kind, data)))

    async def prepare():
        assert session.video_path == video
        assert events[-1][0] == "source_ready"
        await session.report_progress("prepare", "Đang tách âm thanh...")

    monkeypatch.setattr(session, "_start", prepare)
    asyncio.run(session.start_from_url(main.downloader, "https://v.douyin.com/test/"))
    assert events[1][1]["video_url"] == "/api/inputs/video_abc.mp4"
    assert session.get_progress()["progress_pct"] is None


def test_cancel_racing_download_return_deletes_only_owned_paths(isolated, monkeypatch):
    prefix = isolated / "video_owned"
    video = isolated / "video_owned.mp4"
    unrelated = isolated / "user.mp4"
    unrelated.write_bytes(b"keep")
    session = StreamingPipelineSession("cancel-race", None)
    entered, release = threading.Event(), threading.Event()

    def download(url, **kwargs):
        entered.set()
        assert release.wait(3)
        video.write_bytes(b"owned")
        return {"file_path": str(video), "is_local": False, "owned_prefix": str(prefix),
                "owned_paths": [str(video), str(unrelated)]}

    monkeypatch.setattr(main.downloader, "download", download)

    async def run():
        task = asyncio.create_task(session.start_from_url(main.downloader, "https://example.com/video"))
        await wait_for(entered.is_set)
        session.stop()
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled()
        assert not video.exists()
        assert unrelated.read_bytes() == b"keep"
        assert not session.cache_dir.exists()

    asyncio.run(run())


def test_sentence_percentage_counts_completed_audio_and_failure_never_completes(isolated):
    session = StreamingPipelineSession("sentences", isolated / "video.mp4")
    session.initialized = True
    session.is_running = True
    session.segments = {index: SegmentItem(index, index, index + 1, 1) for index in range(4)}
    session.segments[0].status = "READY"
    session.segments[1].status = "TTS"
    asyncio.run(session._segment_progress("tts", "Đang tạo giọng"))
    assert session.get_progress()["progress_pct"] == 25
    assert session.get_progress()["can_pause"]
    assert not session.get_progress()["can_resume"]
    assert session.get_progress()["can_stop"]
    session.pause()
    assert session.get_progress()["status"] == "PAUSED"
    assert not session.get_progress()["can_pause"]
    assert session.get_progress()["can_resume"]
    assert session.get_progress()["can_stop"]
    session.error = "TTS unavailable"
    assert session.get_progress()["status"] == "FAILED"
    assert session.get_progress()["progress_pct"] == 25
    assert not session.get_progress()["can_resume"]
    assert not session.get_progress()["can_stop"]


def test_diagnostics_rejects_arbitrary_file_category(isolated):
    with TestClient(main.app) as client:
        for path in ("../config", "../../.env", "unexpected"):
            assert client.get("/api/diagnostics/logs", params={"category": path}).status_code == 422
            assert client.post("/api/diagnostics/logs/clear", params={"category": path}).status_code == 422


def test_worker_cancellation_does_not_report_false_completion(isolated, monkeypatch):
    session = StreamingPipelineSession("cancel-worker", isolated / "video.mp4")
    session.is_running = True
    session.initialized = True
    session.segments = {0: SegmentItem(0, 0, 1, 1)}
    entered = asyncio.Event()

    async def process(segment):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(session, "_process_segment", process)

    async def run():
        await session.queue.put((0, 0))
        task = asyncio.create_task(session._worker_loop())
        await entered.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert session.get_progress()["status"] == "STOPPED"
        assert session.get_progress()["phase"] == "stopped"
        assert session.get_progress()["progress_pct"] is None

    asyncio.run(run())


def test_invalid_url_is_rejected_before_session_creation(isolated, monkeypatch):
    monkeypatch.setattr(main.downloader, "normalize_url", main.VideoDownloader.normalize_url)
    create = Mock()
    monkeypatch.setattr(main, "create_streaming_session", create)
    with TestClient(main.app) as client:
        for value in ("không có link", "https://", "ftp://example.com/video.mp4"):
            response = client.post("/api/streaming/start-url", json={"url": value})
            assert response.status_code == 422
    create.assert_not_called()


def test_worker_failure_log_identifies_task_and_stage_without_exception_secrets(isolated, monkeypatch, caplog):
    session = StreamingPipelineSession("log-fixture", isolated / "video.mp4")
    session.is_running = True
    segment = SegmentItem(0, 0, 1, 1)
    segment.status = "TRANSLATING"
    session.segments = {0: segment}
    monkeypatch.setattr(session, "_process_segment", AsyncMock(side_effect=RuntimeError("api_key=do-not-log-this")))

    async def run():
        await session.queue.put((0, 0))
        await session._worker_loop()

    with caplog.at_level("ERROR", logger="errors"):
        asyncio.run(run())
    assert "log-fixture" in caplog.text
    assert "TRANSLATING" in caplog.text and "RuntimeError" in caplog.text
    assert "do-not-log-this" not in caplog.text
