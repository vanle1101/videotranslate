"""Opening saved projects does not start the translation workflow."""
import asyncio
import wave
import threading
import time
from fastapi import HTTPException
from pathlib import Path
from unittest.mock import Mock

import pytest

import main
from config import settings
from core.streaming.pipeline import StreamingPipelineSession, SegmentItem, active_streaming_sessions


@pytest.fixture
def saved_project(tmp_path, monkeypatch):
    for name in ("BASE_DIR", "WORKSPACE_DIR", "INPUT_DIR", "OUTPUT_DIR", "TEMP_DIR"):
        path = tmp_path if name == "BASE_DIR" else tmp_path / "workspace" if name == "WORKSPACE_DIR" else tmp_path / name
        path.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(settings, name, path)
    session = StreamingPipelineSession("history-test", settings.INPUT_DIR / "source.mp4")
    session.video_path.write_bytes(b"existing source")
    session.initialized = True
    session.total_duration = 2
    session.video_size = (1920, 1080)
    segment = SegmentItem(0, 0, 2, 2)
    segment.final_vi = "Lời đã sửa."
    segment.text_zh = "你好"
    segment.status = "READY"
    segment.revision = 3
    segment.verification = {"status": "manual"}
    segment.audio_path = str(session.segments_dir / "seg_0.wav")
    with wave.open(segment.audio_path, "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\x01\x00" * 48000)
    session.segments[0] = segment
    session.caption_style = {"background_color": "#123456", "text_color": "#FFFFFF", "position": "top", "blur_original": False}
    session.caption_style_revision = 2
    session.caption_output_outdated = True
    session.persist()
    monkeypatch.setattr(main, "active_export_tasks", {})
    monkeypatch.setattr(main, "task_history", [])
    old = dict(active_streaming_sessions)
    active_streaming_sessions.clear()
    yield session
    active_streaming_sessions.clear()
    active_streaming_sessions.update(old)


def test_history_then_attach_restores_exact_edits_without_provider(saved_project, monkeypatch):
    monkeypatch.setattr(StreamingPipelineSession, "start", Mock(side_effect=AssertionError("must not start")))
    async def run():
        history = (await main.list_tasks())["tasks"]
        row = next(row for row in history if row["task_id"] == saved_project.task_id)
        assert row["saved"] and row["can_open"] and row["title"] == "source.mp4"
        assert not row["video_url"]
        snapshot = await main.streaming_snapshot(saved_project.task_id)
        assert snapshot["segments"][0]["final_vi"] == "Lời đã sửa."
        assert snapshot["segments"][0]["revision"] == 3
        from core.subtitle_cues import normalize_caption_style
        assert snapshot["caption_style"] == normalize_caption_style(saved_project.caption_style)
        assert snapshot["voice"] == saved_project.voice
        assert snapshot["tts_engine"] == saved_project.tts_engine_name
        assert snapshot["output_outdated"] is True
        assert snapshot["progress"]["status"] == "COMPLETED"
        restored = active_streaming_sessions[saved_project.task_id]
        assert restored.worker_task is None and restored.start_task is None
        assert Path(restored.segments[0].audio_path).read_bytes() == Path(saved_project.segments[0].audio_path).read_bytes()
    asyncio.run(run())


def test_live_saved_and_export_are_one_video_without_changing_raw_tasks(saved_project, monkeypatch):
    session = saved_project
    active_streaming_sessions[session.task_id] = session
    source_url = "https://www.douyin.com/video/7692745161054506290"
    session.source_url = source_url
    session.source_video_url = "/api/inputs/source.mp4"
    session.progress.update(status="RUNNING", stage="Đang dịch", progress_pct=22.9)
    session.is_running = True
    main.active_export_tasks[f"export_{session.task_id}"] = {
        "parent_session_id": session.task_id, "status": "RUNNING", "progress": 47.5,
        "stage": "Đang dựng video", "start_time": time.time(), "duration": 2,
    }
    def saved_rows(*, excluded_task_ids):
        return [{"task_id": "older-version", "video_path": str(session.video_path),
            "source_url": "", "title": "source.mp4", "updated_at": 1,
            "status": "STOPPED", "can_open": True, "saved": True}]
    monkeypatch.setattr(main, "list_saved_sessions", saved_rows)
    result = asyncio.run(main.list_tasks())
    assert len(result["tasks"]) == 3
    assert len(result["videos"]) == 1
    video = result["videos"][0]
    assert video["task_id"] == f"export_{session.task_id}"
    assert video["session_id"] == session.task_id
    assert video["is_export"] and video["can_stop"] and video["can_open"]
    assert video["progress_pct"] == 47.5 and video["stage"] == "Đang dựng video"
    assert video["source_video_url"] == session.source_video_url
    assert video["version_count"] == 2
    assert {row["task_id"] for row in video["versions"]} == {session.task_id, "older-version"}
    raw_session = next(row for row in result["tasks"] if row["task_id"] == session.task_id)
    assert raw_session["stage"] == "Đang dịch" and raw_session["progress_pct"] == 22.9


def test_saved_history_exposes_source_identity_without_restoring_or_starting(saved_project, monkeypatch):
    monkeypatch.setattr(StreamingPipelineSession, "start", Mock(side_effect=AssertionError("must not start")))
    result = asyncio.run(main.list_tasks())
    task = result["tasks"][0]
    assert task["video_path"] == str(saved_project.video_path.resolve())
    assert task["source_video_url"].startswith("/api/local-file?path=")
    assert result["videos"][0]["session_id"] == saved_project.task_id
    assert result["videos"][0]["version_count"] == 1
    assert not active_streaming_sessions


@pytest.mark.parametrize("cancel", [False, True])
def test_real_export_failure_or_cancel_stays_visible_after_owner_checkpoint(saved_project, monkeypatch, cancel):
    session = saved_project
    active_streaming_sessions[session.task_id] = session
    session.is_running = False
    session.progress.update(status="COMPLETED", phase="complete", progress_pct=100)
    checkpoint_times = []
    persist = session.persist

    def save():
        persist()
        checkpoint_times.append(session._history_updated_at)

    monkeypatch.setattr(session, "persist", save)

    def failed_render(self, **kwargs):
        if cancel:
            main.active_export_tasks[f"export_{session.task_id}"]["cancelled"] = True
        raise RuntimeError("test render failure")

    monkeypatch.setattr(main.HQExporter, "export", failed_render)

    async def run():
        with pytest.raises(HTTPException) as failure:
            await main.export_hq(main.ExportHQRequest(task_id=session.task_id))
        assert failure.value.status_code == (409 if cancel else 500)
        export = main.active_export_tasks[f"export_{session.task_id}"]
        assert checkpoint_times and export["start_time"] <= checkpoint_times[-1] <= export["updated_at"]
        video = (await main.list_tasks())["videos"][0]
        assert video["task_id"] == f"export_{session.task_id}"
        assert video["status"] == ("CANCELLED" if cancel else "FAILED")
        assert video["can_open"] and video["session_id"] == session.task_id
        # A genuine later edit/save supersedes that completed failed attempt.
        session.caption_style_revision += 1
        session.persist()
        if session._history_updated_at <= export["updated_at"]:
            await asyncio.sleep(.002)
            session.persist()
        newer = (await main.list_tasks())["videos"][0]
        assert newer["task_id"] == session.task_id

    asyncio.run(run())


def test_cancelled_export_coroutine_checkpoints_before_terminal_history_time(saved_project, monkeypatch):
    session = saved_project
    active_streaming_sessions[session.task_id] = session
    session.is_running = False
    session.progress.update(status="COMPLETED", phase="complete", progress_pct=100)
    started = threading.Event()
    release = threading.Event()

    def cancellable_render(self, **kwargs):
        started.set()
        assert release.wait(5)
        assert kwargs["cancel_check"]()
        raise RuntimeError("cancel acknowledged")

    monkeypatch.setattr(main.HQExporter, "export", cancellable_render)

    async def run():
        task = asyncio.create_task(main.export_hq(main.ExportHQRequest(task_id=session.task_id)))
        try:
            for _ in range(100):
                if started.is_set():
                    break
                await asyncio.sleep(.005)
            assert started.is_set()
            task.cancel()
            await asyncio.sleep(.005)
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        export = main.active_export_tasks[f"export_{session.task_id}"]
        assert export["start_time"] <= session._history_updated_at <= export["updated_at"]
        row = (await main.list_tasks())["videos"][0]
        assert row["status"] == "CANCELLED" and row["task_id"] == f"export_{session.task_id}"
        assert session.export_task is None

    asyncio.run(run())


def test_removed_source_is_reported_in_history(saved_project):
    saved_project.video_path.unlink()
    rows = asyncio.run(main.list_tasks())["tasks"]
    row = next(row for row in rows if row["task_id"] == saved_project.task_id)
    assert row["status"] == "FAILED" and "Thiếu video" in row["stage"]
    snapshot = asyncio.run(main.streaming_snapshot(saved_project.task_id))
    assert snapshot["progress"]["status"] == "FAILED"
    assert snapshot["output_video_url"] == ""


def test_history_media_probe_runs_off_event_loop(saved_project, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def blocked_history(*, excluded_task_ids=()):
        entered.set()
        release.wait(3)
        return []
    monkeypatch.setattr(main, "list_saved_sessions", blocked_history)

    async def run():
        listing = asyncio.create_task(main.list_tasks())
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(.005)
        assert entered.is_set()
        pulse = asyncio.Event()
        async def tick():
            await asyncio.sleep(.03)
            pulse.set()
        ticker = asyncio.create_task(tick())
        started = time.monotonic()
        await asyncio.wait_for(pulse.wait(), .25)
        assert time.monotonic() - started < .25
        release.set()
        await ticker
        assert isinstance((await listing)["tasks"], list)

    asyncio.run(run())


def test_task_listing_excludes_live_ids_before_reading_saved_manifest(saved_project, monkeypatch):
    import core.streaming.session_store as store
    session = saved_project
    active_streaming_sessions[session.task_id] = session
    original_read = store._read
    read_ids = []
    def read(task_id):
        read_ids.append(task_id)
        return original_read(task_id)
    monkeypatch.setattr(store, "_read", read)

    rows = asyncio.run(main.list_tasks())["tasks"]
    assert [row["task_id"] for row in rows] == [session.task_id]
    assert session.task_id not in read_ids
    assert rows[0]["task_type"] == "Realtime Dubbing"


def test_task_listing_passes_frozen_ids_already_present_in_snapshot(saved_project, monkeypatch):
    session = saved_project
    active_streaming_sessions[session.task_id] = session
    main.active_export_tasks["export-fixture"] = {"status": "RUNNING"}
    main.task_history.append({"task_id": "history-fixture", "status": "STOPPED"})
    observed = []
    def saved_rows(*, excluded_task_ids):
        observed.append(excluded_task_ids)
        assert isinstance(excluded_task_ids, frozenset)
        return []
    monkeypatch.setattr(main, "list_saved_sessions", saved_rows)

    result = asyncio.run(main.list_tasks())["tasks"]
    assert observed == [frozenset({session.task_id, "export-fixture", "history-fixture"})]
    assert {row["task_id"] for row in result} == set(observed[0])


@pytest.mark.parametrize("elapsed", [None, 3.25])
def test_task_listing_preserves_measured_runtime_elapsed_instead_of_project_age(saved_project, monkeypatch, elapsed):
    session = saved_project
    active_streaming_sessions[session.task_id] = session
    session.start_wall_time = time.time() - 3600
    original = session.get_progress
    monkeypatch.setattr(session, "get_progress", lambda: {**original(), "elapsed_seconds": elapsed})
    result = asyncio.run(main.list_tasks())["tasks"]
    current = next(row for row in result if row["task_id"] == session.task_id)
    assert current["elapsed_seconds"] == elapsed


@pytest.mark.parametrize("damage", ["missing", "corrupt", "foreign"])
def test_live_result_links_are_invalidated_and_reexport_remains_available(saved_project, damage):
    session = saved_project
    active_streaming_sessions[session.task_id] = session
    name = "live-result.mp4" if damage != "foreign" else "../foreign.mp4"
    if damage == "corrupt":
        (settings.OUTPUT_DIR / name).write_bytes(b"not a valid MP4" * 30)
    session.output_filename = name
    session.output_video_url = f"/api/outputs/{name}"
    session.caption_output_outdated = False
    main.active_export_tasks[f"export_{session.task_id}"] = {"status":"COMPLETED", "video_url":session.output_video_url, "output_filename":name}
    async def run():
        snapshot = await main.streaming_snapshot(session.task_id)
        assert snapshot["output_video_url"] == snapshot["output_filename"] == ""
        assert snapshot["output_outdated"] is True
        assert session.segments[0].final_vi == "Lời đã sửa."
        assert any("hãy xuất video lại" in text for text in session.warnings)
        job = await main.get_export_hq_status(session.task_id)
        assert job["status"] == "FAILED" and not job["video_url"]
        assert job["updated_at"] >= session._history_updated_at
    asyncio.run(run())


def test_live_output_probe_is_off_loop_and_cannot_invalidate_newer_render(saved_project, monkeypatch):
    import core.streaming.session_store as store
    entered, release = threading.Event(), threading.Event()
    def probe(*args):
        entered.set()
        release.wait(3)
        return False
    monkeypatch.setattr(store, "_valid_output", probe)
    session = saved_project
    session.output_filename, session.output_video_url = "old.mp4", "/api/outputs/old.mp4"
    async def run():
        checking = asyncio.create_task(main.validate_live_output(session))
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(.005)
            assert entered.is_set()
            assert (await asyncio.wait_for(main.health(), .25))["status"] == "ok"
            session.output_filename, session.output_video_url = "new.mp4", "/api/outputs/new.mp4"
            release.set()
            await checking
            assert session.output_filename == "new.mp4" and not session.warnings
        finally:
            release.set()
            await checking
    asyncio.run(run())


def test_inconclusive_live_probe_preserves_publication_and_completed_export(saved_project, monkeypatch):
    import core.streaming.session_store as store
    session = saved_project
    session.output_filename, session.output_video_url = "result.mp4", "/api/outputs/result.mp4"
    (settings.OUTPUT_DIR / "result.mp4").write_bytes(b"fixture identity")
    monkeypatch.setattr(store, "_valid_output", lambda *args: None)
    job = {"status": "COMPLETED", "video_url": session.output_video_url}
    monkeypatch.setitem(main.active_export_tasks, f"export_{session.task_id}", job)
    asyncio.run(main.validate_live_output(session))
    assert session.output_filename == "result.mp4"
    assert session.output_video_url == "/api/outputs/result.mp4"
    assert job["status"] == "COMPLETED" and not session.warnings


def test_stale_probe_cannot_clear_replacement_at_same_output_name(saved_project, monkeypatch):
    import core.streaming.session_store as store
    entered, release = threading.Event(), threading.Event()

    def probe(*args):
        entered.set()
        release.wait(3)
        return False

    monkeypatch.setattr(store, "_valid_output", probe)
    session = saved_project
    session.output_filename = "result.mp4"
    session.output_video_url = "/api/outputs/result.mp4"
    output = settings.OUTPUT_DIR / session.output_filename
    output.write_bytes(b"old invalid result")

    async def run():
        checking = asyncio.create_task(main.validate_live_output(session))
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(.005)
            assert entered.is_set()
            # Export publishes a replacement without changing its public URL
            # or caption revision while the old file's failed probe finishes.
            replacement = output.with_suffix(".publishing")
            replacement.write_bytes(b"new replacement media" * 30)
            replacement.replace(output)
            release.set()
            await checking
            assert session.output_filename == "result.mp4"
            assert session.output_video_url == "/api/outputs/result.mp4"
            assert not session.warnings
            assert output.read_bytes().startswith(b"new replacement media")
        finally:
            release.set()
            await checking

    asyncio.run(run())
