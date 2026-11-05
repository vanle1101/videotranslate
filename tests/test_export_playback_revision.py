import asyncio
import threading
from copy import deepcopy
from pathlib import Path
from types import MethodType

import pytest
from fastapi import HTTPException

import main
from core.streaming.pipeline import StreamingPipelineSession
from test_auto_review_export import session


@pytest.fixture
def playback_session(session):
    item, exporter = session
    item.current_playback_time = 0.0
    item._visual_incremental_started = False
    item._chunked_source_started = False
    for name in ("update_playback_position", "_recalculate_telemetry", "_refresh_processed_duration"):
        setattr(item, name, MethodType(getattr(StreamingPipelineSession, name), item))
    return item, exporter


def test_signature_stays_stable_through_real_playback_progress(playback_session):
    item, _ = playback_session
    row = item.segments[0]
    before = deepcopy(vars(row))
    signature = main.export_revision_signature(item)
    for position, status in ((0.5, "READY"), (3.0, "READY"), (3.01, "PLAYED"), (4.0, "PLAYED"), (0.0, "PLAYED")):
        item.update_playback_position(position)
        assert item.current_playback_time == position
        assert row.status == status
        assert main.export_revision_signature(item) == signature
    after = deepcopy(vars(row))
    assert after.pop("status") == "PLAYED"
    assert before.pop("status") == "READY"
    assert before == after


@pytest.mark.parametrize("status", ["WAITING", "ASR", "TRANSLATING", "TTS", "ALIGNING", "FAILED", "", None])
@pytest.mark.parametrize("initial_status", ["READY", "PLAYED"])
def test_signature_keeps_non_render_ready_status_changes(playback_session, initial_status, status):
    item, _ = playback_session
    item.segments[0].status = initial_status
    signature = main.export_revision_signature(item)
    item.segments[0].status = status
    assert main.export_revision_signature(item) != signature


@pytest.mark.parametrize("field,value", [
    ("id", 1), ("revision", 1), ("start", 0.1), ("end", 2.1),
    ("final_vi", "Đúng."), ("final_vi", ""), ("confirmed_silence", True),
    ("needs_review", False), ("audio_path", "replacement.wav"), ("audio_path", None),
    ("dub_start", 0.1), ("dub_end", 1.9), ("verification", {"status": "verified"}),
])
def test_signature_keeps_segment_content_and_proof_changes(playback_session, field, value):
    item, _ = playback_session
    signature = main.export_revision_signature(item)
    item.update_playback_position(4.0)
    setattr(item.segments[0], field, value)
    assert main.export_revision_signature(item) != signature


@pytest.mark.parametrize("field,value", [
    ("screen_texts", [{"id": 0, "text_vi": "Bảng tên"}]),
    ("caption_style", {"font_size": 42}),
])
def test_signature_keeps_caption_changes(playback_session, field, value):
    item, _ = playback_session
    signature = main.export_revision_signature(item)
    setattr(item, field, value)
    assert main.export_revision_signature(item) != signature


def test_signature_keeps_caption_renderer_version(playback_session, monkeypatch):
    import core.subtitle_cues

    item, _ = playback_session
    signature = main.export_revision_signature(item)
    monkeypatch.setattr(core.subtitle_cues, "CAPTION_RENDERER_VERSION", "changed-renderer")
    assert main.export_revision_signature(item) != signature


@pytest.mark.parametrize("change", [
    "playback", "text", "revision", "audio", "missing_audio", "missing_text",
    "status", "verification", "caption_style",
])
def test_export_commit_only_accepts_playback_change(playback_session, tmp_path, change):
    item, exporter = playback_session
    rendered, final = tmp_path / "rendered.mp4", tmp_path / "reviewed.mp4"
    rendered.write_bytes(b"new rendered result")
    final.write_bytes(b"previous output")
    replacement = tmp_path / "replacement.wav"
    replacement.write_bytes(Path(item.segments[0].audio_path).read_bytes())
    entered, release = threading.Event(), threading.Event()
    commit_errors = []

    def render(**kwargs):
        assert kwargs["segments"][0]["status"] == "READY"
        entered.set()
        assert release.wait(5)
        try:
            kwargs["publish_callback"](rendered, final)
        except RuntimeError as error:
            commit_errors.append(str(error))
            raise
        return {"output_filename": final.name, "elapsed_seconds": 1}

    exporter.export.side_effect = render

    async def run():
        task = asyncio.create_task(main.export_hq(main.ExportHQRequest(task_id=item.task_id)))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            item.update_playback_position(4.0)
            row = item.segments[0]
            assert row.status == "PLAYED"
            if change == "text":
                row.final_vi = "Đúng."
            elif change == "revision":
                row.revision += 1
            elif change == "audio":
                row.audio_path = str(replacement)
            elif change == "missing_audio":
                row.audio_path = None
            elif change == "missing_text":
                row.final_vi = ""
            elif change == "status":
                row.status = "TTS"
            elif change == "verification":
                row.verification = {"status": "verified"}
            elif change == "caption_style":
                item.caption_style = {"font_size": 42}
            release.set()
            if change == "playback":
                result = await asyncio.wait_for(task, 5)
                assert result["status"] == "ok"
                assert final.read_bytes() == b"new rendered result"
                assert not rendered.exists()
                assert not commit_errors
            else:
                with pytest.raises(HTTPException) as failure:
                    await asyncio.wait_for(task, 5)
                assert failure.value.status_code == 500
                assert final.read_bytes() == b"previous output"
                assert rendered.exists()
                assert len(commit_errors) == 1
                if change != "missing_text":
                    assert "Lời thoại hoặc cấu hình đã thay đổi" in commit_errors[0]
            state = main.active_export_tasks[f"export_{item.task_id}"]
            assert state["status"] == ("COMPLETED" if change == "playback" else "FAILED")
            assert bool(state.get("published")) == (change == "playback")
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
