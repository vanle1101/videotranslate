"""Explicit prefix export preserves full-job gates and validates real media."""
import asyncio
from copy import deepcopy
from pathlib import Path
import subprocess
import threading
import wave
from unittest.mock import AsyncMock, Mock

import pytest

import main
from config import settings
from core.streaming.pipeline import StreamingPipelineSession, SegmentItem, active_streaming_sessions
from core.streaming.partial_export import ready_prefix, prefix_metadata
from core.streaming.session_store import restore_saved_session


@pytest.fixture
def partial(tmp_path, monkeypatch):
    for name in ("BASE_DIR", "WORKSPACE_DIR", "INPUT_DIR", "OUTPUT_DIR", "TEMP_DIR"):
        path = tmp_path if name == "BASE_DIR" else tmp_path / name
        path.mkdir(exist_ok=True)
        monkeypatch.setattr(settings, name, path)
    source = settings.INPUT_DIR / "source.mp4"
    source.write_bytes(b"source fixture")
    session = StreamingPipelineSession("partial-test", source)
    session.initialized, session.total_duration = True, 4.
    session.is_stopped = True
    session.video_size = (320, 240)
    session.output_filename, session.output_video_url = "old-full.mp4", "/api/outputs/old-full.mp4"
    for sid in range(2):
        row = SegmentItem(sid, sid * 2., sid * 2. + 2., 2.)
        row.text_zh, row.final_vi = "你好", "Xin chào."
        row.status = "READY" if sid == 0 else "FAILED"
        row.verification = {"status": "unresolved", "semantic_verified": False}
        row.needs_review = True
        if sid == 0:
            row.audio_path = str(session.segments_dir / "seg_0.wav")
            row.audio_url = "/isolated.wav"
            with wave.open(row.audio_path, "wb") as wav:
                wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                wav.writeframes(b"\x01\x00" * 24000)
        session.segments[sid] = row
    monkeypatch.setattr(main, "active_export_tasks", {})
    monkeypatch.setattr(main, "_accepted_export_owners", {})
    monkeypatch.setattr(main, "broadcast_session_event", AsyncMock())
    old = dict(active_streaming_sessions)
    active_streaming_sessions.clear()
    active_streaming_sessions[session.task_id] = session
    yield session
    active_streaming_sessions.clear()
    active_streaming_sessions.update(old)


def fake_export(monkeypatch, action=None):
    def render(**kwargs):
        if action:
            action(kwargs)
        final = settings.OUTPUT_DIR / f"douyin_translated_{kwargs['task_id']}_hq.mp4"
        rendered = settings.TEMP_DIR / "render.mp4"
        rendered.write_bytes(b"controlled rendered fixture")
        kwargs["publish_callback"](rendered, final)
        return {"output_filename": final.name, "elapsed_seconds": .1}
    monkeypatch.setattr(main.HQExporter, "export", Mock(side_effect=render))


def request(session):
    return main.ExportHQRequest(task_id=session.task_id, scope="ready_prefix")


def test_prefix_stops_at_first_missing_without_joining_later_islands(partial):
    later = deepcopy(partial.segments[0])
    later.id, later.start, later.end = 2, 4., 6.
    partial.segments[2], partial.total_duration = later, 6.
    prefix = ready_prefix(partial)
    assert prefix["duration"] == 2. and [r["id"] for r in prefix["segments"]] == [0]
    assert prefix_metadata(partial)["partial_export_available"]
    partial.segments[0].final_vi = ""
    assert not prefix_metadata(partial)["partial_export_available"]


def test_borrowed_voice_crossing_missing_source_is_never_clipped(partial):
    partial.segments[0].dub_start, partial.segments[0].dub_end = 0., 2.2
    assert not prefix_metadata(partial)["partial_export_available"]


def test_no_prefix_beyond_prepared_source_coverage(partial):
    partial._chunked_source_started = True
    partial._source_prepared_seconds, partial._visual_completed_seconds = 4., 1.
    assert not prefix_metadata(partial)["partial_export_available"]


@pytest.mark.parametrize("compressed", [False, True])
def test_partial_publication_survives_restart_and_does_not_complete_full_job(partial, monkeypatch, compressed):
    monkeypatch.setattr("core.streaming.session_store.COMPRESS_AFTER_BYTES", 1 if compressed else 100000000)
    fake_export(monkeypatch)
    before = (partial.output_filename, partial.output_video_url, partial.is_stopped, partial.error)
    result = asyncio.run(main.export_hq(request(partial)))
    assert result["scope"] == "ready_prefix"
    saved = result["partial_output"]
    assert saved["start"] == 0 and saved["end"] == 2 and saved["is_partial"]
    assert "_partial_" in saved["output_filename"]
    assert (partial.output_filename, partial.output_video_url, partial.is_stopped, partial.error) == before
    active_streaming_sessions.pop(partial.task_id)
    restored = restore_saved_session(partial.task_id)
    assert restored.partial_output == partial.partial_output
    monkeypatch.setattr("core.streaming.session_store._valid_output", lambda *args: True)
    asyncio.run(main.validate_live_output(restored))
    assert main.session_output_details(restored)["partial_output"]["output_filename"] == saved["output_filename"]
    restored.segments[0].final_vi = "Nội dung mới."
    assert main.session_output_details(restored)["partial_output"] is None


def test_growing_ready_prefix_keeps_previous_valid_partial_download(partial, monkeypatch):
    fake_export(monkeypatch)
    result = asyncio.run(main.export_hq(request(partial)))
    later = partial.segments[1]
    later.status, later.confirmed_silence, later.final_vi = "READY", True, ""
    metadata = prefix_metadata(partial)
    assert metadata["partial_available_seconds"] == 4.
    assert metadata["partial_output"]["end"] == result["partial_output"]["end"] == 2.


def test_deleted_partial_file_loses_link_without_discarding_checkpoint(partial, monkeypatch):
    fake_export(monkeypatch)
    result = asyncio.run(main.export_hq(request(partial)))
    saved = deepcopy(partial.partial_output)
    (settings.OUTPUT_DIR / result["partial_output"]["output_filename"]).unlink()
    asyncio.run(main.validate_live_output(partial))
    assert prefix_metadata(partial)["partial_output"] is None
    assert partial.partial_output == saved


def test_changed_retained_source_is_rejected_before_render(partial, monkeypatch):
    partial.persist()
    partial.video_path.write_bytes(b"different source video")
    render = Mock(side_effect=AssertionError("must not render changed source"))
    monkeypatch.setattr(main.HQExporter, "export", render)
    with pytest.raises(main.HTTPException) as caught:
        asyncio.run(main.export_hq(request(partial)))
    assert caught.value.status_code == 409
    render.assert_not_called()


def test_failed_reexport_preserves_previous_partial_file_and_manifest(partial, monkeypatch):
    fake_export(monkeypatch)
    result = asyncio.run(main.export_hq(request(partial)))
    saved = deepcopy(partial.partial_output)
    original = settings.OUTPUT_DIR / result["partial_output"]["output_filename"]
    monkeypatch.setattr(main.HQExporter, "export", Mock(side_effect=RuntimeError("isolated render failure")))
    with pytest.raises(main.HTTPException):
        asyncio.run(main.export_hq(request(partial)))
    assert partial.partial_output == saved and original.exists()


@pytest.mark.parametrize("kind", ["edit", "source"])
def test_changed_revision_before_commit_preserves_prior_output(partial, monkeypatch, kind):
    def change(kwargs):
        if kind == "edit":
            partial.segments[0].final_vi = "Thay đổi trong khi xuất."
        else:
            partial.video_path.write_bytes(b"source changed")
    fake_export(monkeypatch, change)
    with pytest.raises(main.HTTPException):
        asyncio.run(main.export_hq(request(partial)))
    assert not getattr(partial, "partial_output", None)
    assert partial.output_filename == "old-full.mp4"
    assert not list(settings.OUTPUT_DIR.glob("*_partial_*"))


def test_active_partial_and_incomplete_full_remain_rejected(partial):
    partial.is_running = True
    with pytest.raises(main.HTTPException) as caught:
        asyncio.run(main.export_hq(request(partial)))
    assert caught.value.status_code == 409
    partial.is_running = False
    with pytest.raises(main.HTTPException) as caught:
        asyncio.run(main.export_hq(main.ExportHQRequest(task_id=partial.task_id)))
    assert caught.value.status_code == 409


def test_async_poll_cancel_reaps_prefix_renderer(partial, monkeypatch):
    entered, exited = threading.Event(), threading.Event()
    def render(**kwargs):
        entered.set()
        try:
            while not kwargs["cancel_check"]():
                threading.Event().wait(.01)
            raise RuntimeError("Cancelled")
        finally:
            exited.set()
    monkeypatch.setattr(main.HQExporter, "export", Mock(side_effect=render))
    async def run():
        result = await main.start_export_hq(request(partial))
        while not entered.is_set():
            await asyncio.sleep(.01)
        assert result["status"] == "ACCEPTED"
        assert (await main.get_export_hq_status(partial.task_id))["scope"] == "ready_prefix"
        await main.cancel_export_hq(partial.task_id)
        owner = main._accepted_export_owners.get(result["export_id"])
        if owner:
            await asyncio.gather(owner, return_exceptions=True)
        assert exited.is_set() and partial.export_task is None
        assert main.active_export_tasks[result["export_id"]]["status"] == "CANCELLED"
    asyncio.run(run())


def test_real_prefix_mp4_has_selected_duration_and_full_audio(partial, monkeypatch):
    monkeypatch.setattr(settings, "SEPARATION_ENGINE", "none")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x240:r=25:d=4",
        "-f", "lavfi", "-i", "sine=frequency=220:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(partial.video_path)], check=True, capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    result = asyncio.run(main.export_hq(request(partial)))
    path = settings.OUTPUT_DIR / result["partial_output"]["output_filename"]
    main.HQExporter.validate_output(path, 2.)
    assert path.stat().st_size > 0
    assert partial.total_duration == 4. and partial.output_filename == "old-full.mp4"
