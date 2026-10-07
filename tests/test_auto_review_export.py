"""Reviewed sessions produce a final file without asking the user to audit Chinese."""
import asyncio
import errno
import io
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException, UploadFile

import main
from core.streaming.pipeline import SegmentItem


@pytest.fixture
def session(monkeypatch, tmp_path):
    # /api/tasks merges durable history as well as the in-memory registry.
    # Keep that real discovery path, but never scan the developer's projects.
    monkeypatch.setattr(main.settings, "BASE_DIR", tmp_path)
    monkeypatch.setattr(main.settings, "WORKSPACE_DIR", tmp_path / "workspace")
    segment = SegmentItem(0, 0, 2, 2)
    segment.status = "READY"
    segment.text_zh = "假的"
    segment.final_vi = "Sai."
    segment.audio_path = "segment.wav"
    segment.needs_review = True
    segment.review_reason = "Nguồn chưa đủ rõ."
    segment.verification = {"status": "unresolved"}
    item = SimpleNamespace(
        task_id="review-export", segments={0: segment}, video_path=Path("input.mp4"),
        total_duration=2, is_running=False, is_editing=False, is_stopped=False,
        is_paused=False, error=None, start_wall_time=0,
        review_summary={"status": "completed", "checked": 1, "verified": 0, "corrected": 0, "unresolved": 1},
        auto_export_result=True, screen_texts=[], visual_translation=True,
        initialized=True, source_video_url="/api/inputs/input.mp4", initial_buffer_seconds=10,
        get_progress=Mock(return_value={"status": "COMPLETED", "progress_pct": 100}),
        get_telemetry=Mock(return_value={}), segment_snapshot=lambda s: s.to_dict(),
        bgm_url=None, translation_sources=[], warnings=[], source_processing_label=lambda: "OpenCode",
        vocal_suppressor=SimpleNamespace(name="DSP", suppression_level_db=-20), suppression_stats={},
        start=AsyncMock(), start_from_url=AsyncMock(), start_automatic_review=AsyncMock(return_value={}),
    )
    monkeypatch.setattr(main, "active_streaming_sessions", {item.task_id: item})
    # The pipeline accessor references its own registry, so use this session explicitly.
    monkeypatch.setattr(main, "get_streaming_session", lambda task_id: item if task_id == item.task_id else None)
    monkeypatch.setattr(main, "active_export_tasks", {})
    monkeypatch.setattr(main, "task_history", [])
    monkeypatch.setattr(main, "stream_sockets", {})
    monkeypatch.setattr(main.settings, "OUTPUT_DIR", tmp_path)
    exporter = Mock()
    exporter.export.return_value = {"output_filename": "reviewed.mp4", "elapsed_seconds": 1}
    monkeypatch.setattr(main, "HQExporter", Mock(return_value=exporter))
    return item, exporter


def test_completed_review_exports_unresolved_with_warning_and_captions(session):
    item, exporter = session
    result = asyncio.run(main.export_hq(main.ExportHQRequest(task_id=item.task_id)))
    assert result["output_video_url"] == "/api/outputs/reviewed.mp4"
    assert result["review_url"] == "/api/outputs/reviewed.review.json"
    assert result["review_summary"]["unresolved"] == 1
    assert result["review_warning"] and result["review_report"][0]["segment_id"] == 0
    assert item.segments[0].needs_review is True
    rendered = exporter.export.call_args.kwargs["segments"][0]
    assert rendered["preview_is_draft"] is True and rendered["final_vi"] == "Sai."
    assert rendered["verification"]["status"] == "unresolved"
    assert main.active_export_tasks["export_review-export"]["review_warning"]


@pytest.mark.parametrize("status", [None, "running", "failed", "completed"])
def test_uncertain_rows_without_a_completed_audit_stay_blocked(session, status):
    item, exporter = session
    item.review_summary = {"status": status}
    item.segments[0].verification = None
    with pytest.raises(HTTPException) as error:
        asyncio.run(main.export_hq(main.ExportHQRequest(task_id=item.task_id)))
    assert error.value.status_code == 409
    exporter.export.assert_not_called()


@pytest.mark.parametrize("status", ["running", "failed"])
def test_failed_or_running_review_blocks_even_previously_clear_rows(session, status):
    item, exporter = session
    item.review_summary = {"status": status}
    item.segments[0].needs_review = False
    with pytest.raises(HTTPException):
        asyncio.run(main.export_hq(main.ExportHQRequest(task_id=item.task_id)))
    exporter.export.assert_not_called()


def test_finished_event_automatically_exports_once_per_revision_and_publishes(session):
    item, exporter = session
    socket = SimpleNamespace(send_json=AsyncMock())
    main.stream_sockets[item.task_id] = [socket]

    async def run():
        await main.broadcast_session_event(item.task_id, "finished", {})
        first_task = item.auto_export_task
        await main.broadcast_session_event(item.task_id, "finished", {})
        assert item.auto_export_task is first_task
        await first_task
        await main.broadcast_session_event(item.task_id, "finished", {})
        assert item.auto_export_task is first_task
        assert exporter.export.call_count == 1
        item.segments[0].revision += 1
        item.segments[0].final_vi = "Không đúng."
        await main.broadcast_session_event(item.task_id, "finished", {})
        await item.auto_export_task

    asyncio.run(run())
    assert exporter.export.call_count == 2
    ready = [call.args[0] for call in socket.send_json.call_args_list if call.args[0]["type"] == "result_ready"]
    assert len(ready) == 2 and ready[-1]["output_filename"] == "reviewed.mp4"
    assert ready[-1]["review_summary"]["status"] == "completed"
    assert ready[-1]["review_warning"]


@pytest.mark.parametrize("change", ["legacy", "failed", "running", "stopped", "synthesis", "busy"])
def test_automatic_export_does_not_launch_for_ineligible_session(session, change):
    item, exporter = session
    if change == "legacy":
        del item.auto_export_result
    elif change == "failed":
        item.review_summary = {"status": "failed"}
    elif change == "running":
        item.is_running = True
    elif change == "stopped":
        item.is_stopped = True
    elif change == "synthesis":
        item.segments[0].status = "TTS"
    elif change == "busy":
        main.active_export_tasks[f"export_{item.task_id}"] = {"status": "RUNNING"}
    asyncio.run(main.broadcast_session_event(item.task_id, "finished", {}))
    assert not hasattr(item, "auto_export_task")
    exporter.export.assert_not_called()


def test_automatic_export_error_is_safe_and_manual_retry_remains_available(session):
    item, exporter = session
    socket = SimpleNamespace(send_json=AsyncMock())
    main.stream_sockets[item.task_id] = [socket]
    exporter.export.side_effect = RuntimeError("secret-token-must-not-be-shown")

    async def run():
        await main.broadcast_session_event(item.task_id, "finished", {})
        await item.auto_export_task
        assert main.active_export_tasks[f"export_{item.task_id}"]["status"] == "FAILED"
        assert "secret-token" not in str(main.active_export_tasks)
        exporter.export.side_effect = None
        return await main.export_hq(main.ExportHQRequest(task_id=item.task_id))

    result = asyncio.run(run())
    error = next(call.args[0] for call in socket.send_json.call_args_list if call.args[0]["type"] == "result_error")
    assert "secret-token" not in str(error)
    assert "Xuất video" in error["message"]
    assert result["status"] == "ok"


@pytest.mark.parametrize("failure,expected", [
    (OSError(errno.ENOSPC, "secret-token-provider"), "Ổ đĩa không đủ chỗ trống"),
    (RuntimeError("quá nhiều vùng secret-token-provider"), "Tắt làm mờ sub gốc"),
])
def test_automatic_export_preserves_one_classified_error_event(session, failure, expected):
    item, exporter = session
    socket = SimpleNamespace(send_json=AsyncMock())
    main.stream_sockets[item.task_id] = [socket]
    exporter.export.side_effect = failure

    async def run():
        main.schedule_reviewed_export(item.task_id)
        await item.auto_export_task

    asyncio.run(run())
    errors = [call.args[0] for call in socket.send_json.call_args_list if call.args[0]["type"] == "result_error"]
    assert len(errors) == 1
    assert expected in errors[0]["message"]
    assert errors[0]["message"] == main.active_export_tasks[f"export_{item.task_id}"]["stage"]
    assert "secret-token-provider" not in str(errors)


def test_automatic_export_preflight_error_keeps_original_actionable_detail(session, monkeypatch):
    item, _ = session
    socket = SimpleNamespace(send_json=AsyncMock())
    main.stream_sockets[item.task_id] = [socket]
    message = "AI kiểm tra lại chưa hoàn tất. Bấm AI kiểm tra lại để tiếp tục trước khi xuất."
    monkeypatch.setattr(main, "export_hq", AsyncMock(side_effect=HTTPException(409, detail=message)))

    async def run():
        main.schedule_reviewed_export(item.task_id)
        await item.auto_export_task

    asyncio.run(run())
    errors = [call.args[0] for call in socket.send_json.call_args_list if call.args[0]["type"] == "result_error"]
    assert len(errors) == 1 and errors[0]["message"] == message


def test_export_report_write_failure_preserves_published_mp4_and_warns(session, monkeypatch, tmp_path):
    item, exporter = session
    socket = SimpleNamespace(send_json=AsyncMock())
    main.stream_sockets[item.task_id] = [socket]
    rendered, final = tmp_path / "rendered.mp4", tmp_path / "reviewed.mp4"
    sidecar = tmp_path / "reviewed.review.json"
    sidecar.write_text('{"old_report": true}', encoding="utf-8")
    item.output_review_url = "/api/outputs/reviewed.review.json"
    item.persist = Mock()
    log_warning = Mock()
    monkeypatch.setattr(main.logging.getLogger("errors"), "warning", log_warning)
    original_replace = Path.replace

    def block_report_replace(path, target):
        if path.name.startswith(".reviewed.review.json."):
            raise PermissionError(errno.EACCES, "secret-token-must-not-be-shown")
        return original_replace(path, target)

    def render(**kwargs):
        rendered.write_bytes(b"validated MP4 from exporter")
        kwargs["publish_callback"](rendered, final)
        return {"output_filename": final.name, "elapsed_seconds": 1}

    exporter.export.side_effect = render
    monkeypatch.setattr(Path, "replace", block_report_replace)
    result = asyncio.run(main.export_hq(main.ExportHQRequest(task_id=item.task_id)))
    assert result["status"] == "ok"
    assert result["output_video_url"] == "/api/outputs/reviewed.mp4"
    assert final.read_bytes() == b"validated MP4 from exporter"
    assert result["review_url"] == item.output_review_url == ""
    assert "chưa lưu được báo cáo kiểm tra" in result["metadata_warning"]
    assert result["metadata_warning"] in result["warnings"] == item.warnings
    assert sidecar.read_text(encoding="utf-8") == '{"old_report": true}'
    assert not list(tmp_path.glob(".*.tmp"))
    task = main.active_export_tasks[f"export_{item.task_id}"]
    assert task["status"] == "COMPLETED" and task["published"] is True
    assert task["review_url"] == "" and task["stage"] == result["metadata_warning"]
    item.persist.assert_called_once()
    events = [call.args[0] for call in socket.send_json.call_args_list]
    assert not any(event["type"] == "result_error" for event in events)
    ready = next(event for event in events if event["type"] == "result_ready")
    assert ready["warnings"] == item.warnings and ready["metadata_warning"]
    assert "EXPORT_REPORT_SAVE_FAILED" in str(log_warning.call_args_list)
    assert "secret-token" not in str(log_warning.call_args_list)

    monkeypatch.setattr(Path, "replace", original_replace)
    retry = asyncio.run(main.export_hq(main.ExportHQRequest(task_id=item.task_id)))
    assert retry["review_url"] == "/api/outputs/reviewed.review.json"
    assert not retry["metadata_warning"] and not retry["warnings"]
    assert not list(tmp_path.glob(".*.tmp"))


def test_export_project_save_failure_keeps_download_and_warns(session):
    item, _ = session
    item.persist = Mock(side_effect=OSError(errno.ENOSPC, "secret-token-must-not-be-shown"))
    socket = SimpleNamespace(send_json=AsyncMock())
    main.stream_sockets[item.task_id] = [socket]

    result = asyncio.run(main.export_hq(main.ExportHQRequest(task_id=item.task_id)))
    assert result["status"] == "ok" and result["output_video_url"]
    assert "Không lưu được phiên xuống ổ đĩa" in result["metadata_warning"]
    assert "secret-token" not in str(result)
    task = main.active_export_tasks[f"export_{item.task_id}"]
    assert task["status"] == "COMPLETED" and task["stage"] == result["metadata_warning"]
    ready = next(call.args[0] for call in socket.send_json.call_args_list if call.args[0]["type"] == "result_ready")
    assert ready["warnings"] == item.warnings and ready["metadata_warning"]


def test_automatic_export_is_tracked_cancellable_and_rejects_manual_duplicate(session):
    item, exporter = session
    release = threading.Event()

    async def run():
        loop = asyncio.get_running_loop()
        started = asyncio.Event()

        def render(**kwargs):
            loop.call_soon_threadsafe(started.set)
            assert release.wait(5)
            assert kwargs["cancel_check"]() is True
            raise RuntimeError("cancelled")

        exporter.export.side_effect = render
        try:
            await main.broadcast_session_event(item.task_id, "finished", {})
            await asyncio.wait_for(started.wait(), timeout=5)
            export = main.active_export_tasks[f"export_{item.task_id}"]
            assert export["status"] == "RUNNING"
            with pytest.raises(HTTPException) as error:
                await main.export_hq(main.ExportHQRequest(task_id=item.task_id))
            assert error.value.status_code == 409
            await main.cancel_export_hq(item.task_id)
            assert export["status"] == "CANCELLING"
        finally:
            release.set()
            await item.auto_export_task
        assert export["status"] == "CANCELLED"
        assert item.output_video_url == ""

    asyncio.run(run())
    assert exporter.export.call_count == 1


def test_final_output_and_audit_report_survive_snapshot_and_task_poll(session):
    item, _ = session

    async def run():
        await main.export_hq(main.ExportHQRequest(task_id=item.task_id))
        return await main.streaming_snapshot(item.task_id), await main.list_tasks()

    snapshot, tasks = asyncio.run(run())
    assert snapshot["video_url"] == item.source_video_url
    assert snapshot["output_video_url"] == "/api/outputs/reviewed.mp4"
    assert snapshot["review_url"] == "/api/outputs/reviewed.review.json"
    assert snapshot["review_report"][0]["verification"]["status"] == "unresolved"
    assert {task["task_id"] for task in tasks["tasks"]} == {item.task_id, f"export_{item.task_id}"}
    for task in tasks["tasks"]:
        assert task["video_url"] == task["output_video_url"] == "/api/outputs/reviewed.mp4"
        assert task["output_filename"] == "reviewed.mp4" and task["review_warning"]
        assert task["review_url"] == "/api/outputs/reviewed.review.json"


@pytest.mark.parametrize("provider,visual,expected", [("opencode", True, True), ("opencode", False, False), ("gemini", True, False)])
@pytest.mark.parametrize("source", ["url", "upload", "local"])
def test_new_visual_opencode_sessions_opt_into_final_output(session, monkeypatch, provider, visual, expected, source):
    item, _ = session
    monkeypatch.setattr(main.settings, "LLM_PROVIDER", provider)
    monkeypatch.setattr(main, "validate_visual_translation", lambda value: None)
    monkeypatch.setattr(main, "validated_voice", lambda *args: ("edge-tts", "vi-VN-HoaiMyNeural"))
    monkeypatch.setattr(main, "create_streaming_session", lambda **kwargs: item)
    monkeypatch.setattr(main.downloader, "normalize_url", lambda value: value)
    with TemporaryDirectory(prefix="review_export_") as folder:
        video = Path(folder) / "source.mp4"
        video.write_bytes(b"test video")
        monkeypatch.setattr(main.settings, "INPUT_DIR", Path(folder))

        async def run():
            if source == "local":
                await main.start_streaming_local_file(main.StreamLocalFileRequest(file_path=str(video), visual_translation=visual))
            elif source == "url":
                await main.start_streaming_url(main.StreamUrlRequest(url="https://example.com/video", visual_translation=visual))
            else:
                await main.start_streaming_upload(UploadFile(io.BytesIO(b"test"), filename="video.mp4"),
                    initial_buffer_seconds=10, voice=None, voice_id=None, tts_engine=None,
                    asr_engine="faster-whisper", visual_translation=visual, ref_audio=None)
            await asyncio.sleep(0)

        asyncio.run(run())
    assert item.auto_export_result is expected


def test_review_existing_session_enables_automatic_result(session):
    item, _ = session
    item.auto_export_result = False
    result = asyncio.run(main.review_streaming_translation(item.task_id))
    assert result["status"] == "reviewing" and item.auto_export_result is True


def test_stop_waits_for_review_cleanup_before_acknowledging(session):
    item, _ = session

    async def run():
        started, cleaned = asyncio.Event(), asyncio.Event()

        async def review():
            try:
                started.set()
                await asyncio.Future()
            finally:
                await asyncio.sleep(0)
                cleaned.set()

        item.review_task = asyncio.create_task(review())
        item.stop = lambda: item.review_task.cancel()
        await started.wait()
        result = await main.stop_task(item.task_id)
        assert result["action"] == "stopped"
        assert cleaned.is_set() and item.review_task.done()

    asyncio.run(run())


def test_stop_also_cancels_and_drains_automatic_export(session):
    item, exporter = session
    release = threading.Event()

    async def run():
        loop = asyncio.get_running_loop()
        started = asyncio.Event()

        def render(**kwargs):
            loop.call_soon_threadsafe(started.set)
            for _ in range(500):
                if kwargs["cancel_check"]() or release.wait(.01):
                    break
            assert kwargs["cancel_check"]()
            raise RuntimeError("cancelled")

        def stop():
            item.is_stopped = True
            release.set()

        exporter.export.side_effect = render
        item.stop = stop
        try:
            await main.broadcast_session_event(item.task_id, "finished", {})
            await asyncio.wait_for(started.wait(), timeout=5)
            await main.stop_task(item.task_id)
            assert item.auto_export_task.done()
            assert main.active_export_tasks[f"export_{item.task_id}"]["status"] == "CANCELLED"
        finally:
            release.set()
            await item.auto_export_task

    asyncio.run(run())


def test_cancelling_export_coroutine_waits_for_its_real_worker(session):
    item, exporter = session
    release = threading.Event()

    async def run():
        loop = asyncio.get_running_loop()
        started, cancellation_seen = asyncio.Event(), asyncio.Event()

        def render(**kwargs):
            loop.call_soon_threadsafe(started.set)
            for _ in range(500):
                if kwargs["cancel_check"]():
                    loop.call_soon_threadsafe(cancellation_seen.set)
                    break
                release.wait(.01)
            assert release.wait(5)
            raise RuntimeError("cancelled")

        exporter.export.side_effect = render
        task = asyncio.create_task(main.export_hq(main.ExportHQRequest(task_id=item.task_id)))
        try:
            await asyncio.wait_for(started.wait(), 5)
            task.cancel()
            await asyncio.wait_for(cancellation_seen.wait(), 5)
            assert not task.done()
            assert item.export_task is task
            assert main.active_export_tasks[f"export_{item.task_id}"]["status"] == "CANCELLING"
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert item.export_task is None
        assert main.active_export_tasks[f"export_{item.task_id}"]["status"] == "CANCELLED"

    asyncio.run(run())


@pytest.mark.parametrize("cancel_before_commit", [True, False])
def test_export_publication_and_cancel_share_one_commit_boundary(session, tmp_path, cancel_before_commit):
    item, exporter = session
    rendered, final = tmp_path / "rendered.mp4", tmp_path / "reviewed.mp4"
    rendered.write_bytes(b"validated new media")
    final.write_bytes(b"previous output")
    ready, release = threading.Event(), threading.Event()

    def render(**kwargs):
        if not cancel_before_commit:
            kwargs["publish_callback"](rendered, final)
        ready.set()
        assert release.wait(5)
        if cancel_before_commit:
            kwargs["publish_callback"](rendered, final)
        return {"output_filename": final.name, "elapsed_seconds": 1}

    exporter.export.side_effect = render

    async def run():
        task = asyncio.create_task(main.export_hq(main.ExportHQRequest(task_id=item.task_id)))
        try:
            assert await asyncio.to_thread(ready.wait, 5)
            if cancel_before_commit:
                await main.cancel_export_hq(item.task_id)
                release.set()
                with pytest.raises(HTTPException):
                    await task
                assert final.read_bytes() == b"previous output"
                assert main.active_export_tasks[f"export_{item.task_id}"]["status"] == "CANCELLED"
            else:
                with pytest.raises(HTTPException) as error:
                    await main.cancel_export_hq(item.task_id)
                assert error.value.status_code == 409
                task.cancel()
                release.set()
                result = await task
                assert result["status"] == "ok"
                assert final.read_bytes() == b"validated new media"
                assert main.active_export_tasks[f"export_{item.task_id}"]["status"] == "COMPLETED"
        finally:
            release.set()

    asyncio.run(run())
