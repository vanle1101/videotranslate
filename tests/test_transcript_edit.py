"""Transcript text/audio transactions, lifecycle and export consistency; offline."""
import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

import main
from config import settings
from core.streaming.pipeline import SegmentItem, StreamingPipelineSession


@pytest.fixture
def session(tmp_path, monkeypatch):
    for name, path in {"BASE_DIR": tmp_path, "TEMP_DIR": tmp_path / "temp",
                       "WORKSPACE_DIR": tmp_path / "workspace"}.items():
        monkeypatch.setattr(settings, name, path)
        path.mkdir(parents=True, exist_ok=True)
    registry = {}
    monkeypatch.setattr(main, "active_streaming_sessions", registry)
    monkeypatch.setattr("core.streaming.pipeline.active_streaming_sessions", registry)
    monkeypatch.setattr(main, "active_export_tasks", {})
    monkeypatch.setattr(main, "task_history", [])
    events = []
    sess = StreamingPipelineSession("transcript-test", tmp_path / "source.mp4",
                                    voice="vi-VN-HoaiMyNeural", tts_engine_name="edge-tts",
                                    event_callback=lambda kind, data: events.append((kind, data)))
    sess.events = events
    sess.initialized = True
    sess.total_duration = 12
    seg = SegmentItem(0, 2, 5, 3)
    seg.status = "READY"
    seg.text_zh, seg.final_vi = "原文", "Lời thoại cũ"
    audio = sess.segments_dir / "seg_0.wav"
    audio.write_bytes(b"old audio")
    seg.audio_path = str(audio)
    seg.audio_url = "/api/streaming/audio/transcript-test/0"
    sess.segments[0] = seg
    sess.rolling_context = [{"zh": seg.text_zh, "vi": seg.final_vi}]

    def synthesize(*, text, output_path, **kwargs):
        Path(output_path).write_bytes(text.encode())

    def align(raw, output, ratio, *, fit_duration):
        assert fit_duration == 3
        Path(output).write_bytes(Path(raw).read_bytes() + b" aligned")
        return ratio

    sess.tts_engine = SimpleNamespace(synthesize=Mock(side_effect=synthesize))
    sess.aligner = SimpleNamespace(min_speed=0.9, max_speed=1.15,
                                   get_audio_duration=Mock(return_value=4.5),
                                   apply_atempo=Mock(side_effect=align))
    registry[sess.task_id] = sess
    return sess


def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test")


def route(session, segment_id=0):
    return f"/api/streaming/{session.task_id}/segments/{segment_id}"


async def wait_until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.005)


def test_edit_completed_segment_publishes_text_and_fitted_audio_with_new_revision(session):
    session.segments[0].status = "PLAYED"

    async def run():
        async with client() as api:
            response = await api.patch(route(session), json={"final_vi": "  Lời thoại mới  "})
            assert response.status_code == 200
            saved = response.json()["segment"]
            assert (saved["start"], saved["end"], saved["duration"]) == (2, 5, 3)
            assert saved["final_vi"] == saved["text_vi"] == "Lời thoại mới"
            assert saved["status"] == "PLAYED" and saved["revision"] == 1
            assert saved["audio_url"].endswith("?rev=1")
            assert saved["tts_duration"] == 4.5 and saved["speed_ratio"] == 1.5
            audio = await api.get(saved["audio_url"])
            assert audio.content == "Lời thoại mới aligned".encode()
            assert audio.headers["cache-control"] == "no-store"
            second = await api.patch(route(session), json={"final_vi": "Lời thoại khác"})
            assert second.json()["segment"]["revision"] == 2

    asyncio.run(run())
    assert session.rolling_context[0]["vi"] == "Lời thoại khác"
    assert session.events[-1][0] == "segment_update"
    assert session.events[-1][1]["final_vi"] == "Lời thoại khác"
    assert session.events[-1][1]["audio_url"].endswith("?rev=2")
    assert not session.edit_tasks
    assert not list(session.cache_dir.glob("edit_*"))
    assert [p.name for p in session.segments_dir.iterdir()] == ["seg_0.wav"]


@pytest.mark.parametrize("stage", ["synthesis", "alignment", "replace"])
def test_failure_preserves_previous_text_audio_revision_and_cleans_temporary_files(session, monkeypatch, stage):
    old = session.segments[0].to_dict()
    if stage == "synthesis":
        session.tts_engine.synthesize.side_effect = RuntimeError("api_key=private-test-value")
    elif stage == "alignment":
        def fail(raw, output, *args, **kwargs):
            Path(output).write_bytes(b"partial audio")
            raise RuntimeError("api_key=private-test-value")
        session.aligner.apply_atempo.side_effect = fail
    else:
        monkeypatch.setattr(Path, "replace", Mock(side_effect=PermissionError("file open")))

    async def run():
        async with client() as api:
            response = await api.patch(route(session), json={"final_vi": "Bản mới"})
            assert response.status_code == 503
            assert "private-test-value" not in response.text

    asyncio.run(run())
    assert session.segments[0].to_dict() == old
    assert Path(session.segments[0].audio_path).read_bytes() == b"old audio"
    assert not session.events and not session.edit_tasks
    assert not list(session.cache_dir.glob("edit_*"))
    assert [p.name for p in session.segments_dir.iterdir()] == ["seg_0.wav"]


@pytest.mark.parametrize("value", ["", "   ", "x" * 2001, "bad\x00text", None, 42])
def test_invalid_text_never_synthesizes(session, value):
    async def run():
        async with client() as api:
            response = await api.patch(route(session), json={"final_vi": value})
            assert response.status_code == 422
    asyncio.run(run())
    session.tts_engine.synthesize.assert_not_called()


@pytest.mark.parametrize("segment_id,status", [(99, 404), (-1, 422), ("NaN", 422), ("Infinity", 422), (2147483648, 422)])
def test_invalid_or_unknown_segment_id(session, segment_id, status):
    async def run():
        async with client() as api:
            response = await api.patch(route(session, segment_id), json={"final_vi": "Bản mới"})
            assert response.status_code == status
    asyncio.run(run())
    session.tts_engine.synthesize.assert_not_called()


@pytest.mark.parametrize("status", ["WAITING", "ASR", "TRANSLATING", "TTS", "ALIGNING", "FAILED"])
def test_unfinished_segment_cannot_be_edited(session, status):
    session.segments[0].status = status
    async def run():
        async with client() as api:
            response = await api.patch(route(session), json={"final_vi": "Bản mới"})
            assert response.status_code == 409
    asyncio.run(run())
    session.tts_engine.synthesize.assert_not_called()


def test_inflight_edit_blocks_other_edits_and_export_but_keeps_existing_audio(session):
    entered, release = threading.Event(), threading.Event()

    def synthesize(*, text, output_path, **kwargs):
        entered.set()
        assert release.wait(3)
        Path(output_path).write_bytes(text.encode())

    session.tts_engine.synthesize.side_effect = synthesize

    async def run():
        async with client() as api:
            editing = asyncio.create_task(api.patch(route(session), json={"final_vi": "Bản mới"}))
            try:
                await wait_until(entered.is_set)
                blocked = await api.patch(route(session), json={"final_vi": "Bản ghi đè"})
                assert blocked.status_code == 409
                export = await api.post("/api/streaming/export-hq", json={"task_id": session.task_id})
                assert export.status_code == 409
                assert Path(session.segments[0].audio_path).read_bytes() == b"old audio"
                assert session.segments[0].final_vi == "Lời thoại cũ"
            finally:
                release.set()
                response = await editing
            assert response.status_code == 200
    asyncio.run(run())


def test_export_uses_revised_text_and_audio_and_blocks_edits_until_done(session, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    seen = {}

    def export(**kwargs):
        segment = kwargs["segments"][0]
        seen.update(segment)
        seen["bytes"] = Path(segment["audio_path"]).read_bytes()
        entered.set()
        assert release.wait(3)
        return {"output_filename": "final.mp4", "elapsed_seconds": 0.1}

    monkeypatch.setattr(main, "HQExporter", lambda: SimpleNamespace(export=export))

    async def run():
        async with client() as api:
            assert (await api.patch(route(session), json={"final_vi": "Bản xuất"})).status_code == 200
            exporting = asyncio.create_task(api.post("/api/streaming/export-hq", json={"task_id": session.task_id}))
            try:
                await wait_until(entered.is_set)
                response = await api.patch(route(session), json={"final_vi": "Ghi đè giữa chừng"})
                assert response.status_code == 409
            finally:
                release.set()
                result = await exporting
            assert result.status_code == 200
    asyncio.run(run())
    assert seen["final_vi"] == "Bản xuất" and seen["bytes"] == "Bản xuất aligned".encode()
    assert (seen["start"], seen["end"]) == (2, 5)


def test_stop_waits_for_synthesis_thread_before_removing_owned_audio(session):
    entered, release = threading.Event(), threading.Event()

    def synthesize(*, output_path, **kwargs):
        entered.set()
        assert release.wait(3)
        Path(output_path).write_bytes(b"late write")

    session.tts_engine.synthesize.side_effect = synthesize
    old_audio = Path(session.segments[0].audio_path)

    async def run():
        editing = asyncio.create_task(session.edit_segment(0, "Bản mới"))
        await wait_until(entered.is_set)
        stopping = asyncio.create_task(main.stop_task(session.task_id))
        try:
            await wait_until(lambda: session.is_stopped)
            assert not stopping.done()
            assert old_audio.read_bytes() == b"old audio"
        finally:
            release.set()
            await asyncio.gather(editing, return_exceptions=True)
            assert (await stopping)["action"] == "stopped"
        assert editing.cancelled()
    asyncio.run(run())
    assert session.segments[0].final_vi == "Lời thoại cũ"
    assert not session.cache_dir.exists() and not session.edit_tasks


def test_ready_segment_edit_waits_for_pipeline_tts_lock_without_requiring_pause(session):
    session.is_running = True

    async def run():
        async with session._tts_lock:
            editing = asyncio.create_task(session.edit_segment(0, "Bản mới"))
            await wait_until(lambda: session.is_editing)
            session.tts_engine.synthesize.assert_not_called()
        result = await editing
        assert result["final_vi"] == "Bản mới" and not session.is_paused
    asyncio.run(run())


def test_shutdown_from_another_thread_cancels_edit_on_its_own_event_loop(session):
    entered, release = threading.Event(), threading.Event()

    def synthesize(*, output_path, **kwargs):
        entered.set()
        assert release.wait(3)
        Path(output_path).write_bytes(b"late write")

    session.tts_engine.synthesize.side_effect = synthesize

    async def run():
        editing = asyncio.create_task(session.edit_segment(0, "Bản mới"))
        await wait_until(entered.is_set)
        try:
            await asyncio.to_thread(session.stop)
            await wait_until(lambda: session.is_stopped)
            assert session.cache_dir.exists()
        finally:
            release.set()
            await asyncio.gather(editing, return_exceptions=True)
        assert editing.cancelled()
    asyncio.run(run())
    assert not session.cache_dir.exists() and not session.edit_tasks


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), -1, 0])
def test_invalid_timing_cannot_change_text_or_start_tts(session, duration):
    session.segments[0].duration = duration

    async def run():
        async with client() as api:
            response = await api.patch(route(session), json={"final_vi": "Bản mới"})
            assert response.status_code == 422
    asyncio.run(run())
    session.tts_engine.synthesize.assert_not_called()
    assert session.segments[0].final_vi == "Lời thoại cũ"
