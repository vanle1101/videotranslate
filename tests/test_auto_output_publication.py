import asyncio
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import main
from core.streaming.pipeline import SegmentItem


@pytest.fixture
def publication_session(monkeypatch, tmp_path):
    monkeypatch.setattr(main.settings, "BASE_DIR", tmp_path)
    monkeypatch.setattr(main.settings, "WORKSPACE_DIR", tmp_path / "workspace")
    monkeypatch.setattr(main.settings, "OUTPUT_DIR", tmp_path)
    segment = SegmentItem(0, 0, 2, 2)
    segment.status = "READY"
    segment.text_zh = "假的"
    segment.final_vi = "Sai."
    audio_path = tmp_path / "segment.wav"
    with wave.open(str(audio_path), "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x01\x00" * 24000)
    segment.audio_path = str(audio_path)
    segment.needs_review = True
    segment.review_reason = "Nguồn chưa đủ rõ."
    segment.verification = {"status": "unresolved"}
    persisted = []
    item = SimpleNamespace(
        task_id="publication-state", segments={0: segment}, video_path=Path("input.mp4"),
        total_duration=2, is_running=False, is_editing=False, is_stopped=False,
        is_paused=False, error=None, start_wall_time=0,
        review_summary={"status": "completed", "checked": 1, "verified": 0,
                        "corrected": 0, "unresolved": 1},
        auto_export_result=True, screen_texts=[], visual_translation=True,
        initialized=True, source_video_url="/api/inputs/input.mp4", initial_buffer_seconds=10,
        output_filename="", output_video_url="", output_review_url="",
        get_progress=Mock(return_value={"status": "COMPLETED", "progress_pct": 100}),
        get_telemetry=Mock(return_value={}), segment_snapshot=lambda s: s.to_dict(),
        bgm_url=None, translation_sources=[], warnings=[], source_processing_label=lambda: "OpenCode",
        vocal_suppressor=SimpleNamespace(name="DSP", suppression_level_db=-20), suppression_stats={},
        start=AsyncMock(), start_from_url=AsyncMock(), start_automatic_review=AsyncMock(return_value={}),
        report_progress=AsyncMock(), emit=AsyncMock(),
    )

    def persist():
        persisted.append({
            "automatic_output": dict(getattr(item, "automatic_output", {}) or {}),
            "output_filename": item.output_filename,
            "output_video_url": item.output_video_url,
            "status": main.active_export_tasks.get("export_publication-state", {}).get("status"),
        })

    item.persist = persist
    monkeypatch.setattr(main, "active_streaming_sessions", {item.task_id: item})
    monkeypatch.setattr(main, "get_streaming_session", lambda task_id: item if task_id == item.task_id else None)
    monkeypatch.setattr(main, "active_export_tasks", {})
    monkeypatch.setattr(main, "task_history", [])
    monkeypatch.setattr(main, "stream_sockets", {})
    exporter = Mock()
    monkeypatch.setattr(main, "HQExporter", Mock(return_value=exporter))
    rendered = tmp_path / "rendered.mp4"
    final = tmp_path / "published.mp4"

    def render(**kwargs):
        rendered.write_bytes(b"validated MP4")
        kwargs["publish_callback"](rendered, final)
        return {"output_filename": final.name, "elapsed_seconds": 1}

    exporter.export.side_effect = render
    yield item, exporter, persisted, final


def _assert_published(item, exporter, persisted, final):
    assert final.read_bytes() == b"validated MP4"
    assert exporter.export.call_count == 1
    task = main.active_export_tasks["export_publication-state"]
    assert task["status"] == "COMPLETED"
    assert task["published"] is True
    assert item.automatic_output == {
        "status": "completed", "policy": "strict_media", "reason": "review_required"
    }
    assert item.output_filename == "published.mp4"
    assert item.output_video_url == "/api/outputs/published.mp4"
    assert persisted[-1]["automatic_output"] == item.automatic_output
    assert persisted[-1]["output_filename"] == "published.mp4"
    assert persisted[-1]["output_video_url"] == "/api/outputs/published.mp4"
    assert persisted[-1]["status"] == "COMPLETED"


@pytest.mark.parametrize("failure", [RuntimeError("late progress"), asyncio.CancelledError()])
def test_late_report_progress_failure_keeps_published_output(publication_session, failure):
    item, exporter, persisted, final = publication_session
    item.report_progress = AsyncMock(side_effect=failure)

    result = asyncio.run(main.export_hq(main.ExportHQRequest(task_id=item.task_id)))

    assert result["status"] == "ok"
    _assert_published(item, exporter, persisted, final)


@pytest.mark.parametrize("failure", [RuntimeError("late ready"), asyncio.CancelledError()])
def test_late_result_ready_failure_keeps_published_output(publication_session, monkeypatch, failure):
    item, exporter, persisted, final = publication_session
    events = []

    async def broadcast(task_id, event_type, payload):
        events.append(event_type)
        if event_type == "result_ready":
            raise failure

    monkeypatch.setattr(main, "broadcast_session_event", broadcast)
    result = asyncio.run(main.export_hq(main.ExportHQRequest(task_id=item.task_id)))

    assert result["status"] == "ok"
    assert events[-1] == "result_ready"
    _assert_published(item, exporter, persisted, final)
    assert not any(event == "result_error" for event in events)
