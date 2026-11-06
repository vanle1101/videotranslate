"""Explicit partial publication keeps failed rows repairable and audible rows intact."""
import asyncio
import json
import threading
import wave
from unittest.mock import AsyncMock
from copy import deepcopy

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import main
from core.streaming.pipeline import SegmentItem
from test_auto_review_export import session


@pytest.mark.parametrize("stopped", [False, True])
@pytest.mark.parametrize("review", ["completed", "failed", "incomplete"])
def test_partial_export_preserves_subtitles_and_pending_work(session, tmp_path, stopped, review):
    item, exporter = session
    item.is_stopped = stopped
    item.error = "One sentence failed"
    item.review_summary["status"] = review
    valid_audio = tmp_path / "voice.wav"
    with wave.open(str(valid_audio), "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x01\x00" * 24000)
    item.segments[0].audio_path = str(valid_audio)
    failed = SegmentItem(1, 2, 3, 1)
    failed.status, failed.final_vi = "FAILED", "Giữ phụ đề câu lỗi."
    failed.audio_path = str(valid_audio)  # Stale speech must not leak into partial output.
    empty = SegmentItem(2, 3, 4, 1)
    empty.status, empty.text_zh = "PLAYED", "叶哥"
    item.segments.update({1: failed, 2: empty})
    item.total_duration = 4
    before = deepcopy([row.to_dict() for row in item.segments.values()])
    result = asyncio.run(main.export_hq(main.ExportHQRequest(task_id=item.task_id, allow_incomplete=True)))
    assert result["output_incomplete"] is True
    assert result["output_missing_segment_ids"] == [1, 2]
    assert result["output_video_url"].endswith("reviewed.mp4")
    assert "2 câu" in result["output_warning"]
    rows = exporter.export.call_args.kwargs["segments"]
    assert len(rows) == 3 and rows[0]["audio_path"] == str(valid_audio)
    assert rows[1]["final_vi"] == failed.final_vi and rows[1]["omit_voice"]
    assert rows[1]["audio_path"] is None and rows[2]["omit_voice"]
    assert [row.to_dict() for row in item.segments.values()] == before
    report = json.loads((tmp_path / "reviewed.review.json").read_text(encoding="utf-8"))
    assert report["output_missing_segment_ids"] == [1, 2]
    assert item.review_summary["status"] == review


def test_missing_ready_audio_is_explicitly_reported(session):
    item, exporter = session
    item.segments[0].audio_path = "not-present.wav"
    result = asyncio.run(main.export_hq(main.ExportHQRequest(task_id=item.task_id, allow_incomplete=True)))
    assert result["output_missing_segment_ids"] == [0]
    assert exporter.export.call_args.kwargs["segments"][0]["omit_voice"]


def test_source_gap_draft_restores_original_audio_for_failed_asr_timing(session, tmp_path, monkeypatch):
    import core.streaming.chunked_source as chunked_source
    monkeypatch.setattr(chunked_source, "ensure_source_identity", lambda _session: None)
    item, exporter = session
    ready = item.segments[0]
    ready.end, ready.duration = 1., 1.
    failed = SegmentItem(1, 1.2, 1.3, .1)
    failed.status, failed.failed_stage = "FAILED", "TTS"
    failed.text_zh, failed.final_vi = "source evidence", "Lời dịch cần kiểm tra."
    failed.source_method = "audio"
    failed.source_audio_evidence = "original_audio"
    failed.source_asr_start, failed.source_asr_end = 1.2, 1.3
    failed.needs_review = True
    failed.verification = {"status": "unresolved"}
    item.segments[1] = failed
    item.total_duration = 2.
    item._chunked_source_started = True
    item._source_prepared_seconds = 2.
    item._source_scanned_seconds = 2.
    item._source_missing_speech_spans = [[1.52, 1.58]]
    item._source_failure_code = "source_speech_unrecognized"
    item._source_coverage_intervals = [
        {"start": 0., "end": 1.5, "state": "prepared", "missing_speech_spans": []},
        {"start": 1.5, "end": 1.6, "state": "unresolved", "missing_speech_spans": [[1.52, 1.58]],
         "error_code": "source_speech_unrecognized"},
        {"start": 1.6, "end": 2., "state": "prepared", "missing_speech_spans": []},
    ]
    result = asyncio.run(main.export_hq(
        main.ExportHQRequest(task_id=item.task_id, allow_incomplete=True)))

    call = exporter.export.call_args.kwargs
    assert call["source_audio_fallback_spans"] == [(1.2, 1.3)]
    assert call["segments"][1]["omit_voice"] is True
    assert call["segments"][1]["audio_path"] is None
    assert result["output_incomplete"] is True
    assert result["output_missing_segment_ids"] == [1]


@pytest.mark.parametrize("active", ["is_running", "is_editing", "_stop_draining"])
def test_partial_does_not_export_while_mutation_is_active(session, active):
    item, exporter = session
    setattr(item, active, True)
    with pytest.raises(HTTPException) as failure:
        asyncio.run(main.export_hq(main.ExportHQRequest(task_id=item.task_id, allow_incomplete=True)))
    assert failure.value.status_code == 409
    exporter.export.assert_not_called()


def test_partial_does_not_export_unprocessed_preview(session):
    item, exporter = session
    item.translation_mode = "preview"
    item._visual_prepass_complete = False
    with pytest.raises(HTTPException) as failure:
        asyncio.run(main.export_hq(main.ExportHQRequest(task_id=item.task_id, allow_incomplete=True)))
    assert failure.value.status_code == 409
    exporter.export.assert_not_called()


@pytest.mark.parametrize("cancel", [False, True])
def test_stopped_partial_publication_still_honors_new_cancel(session, tmp_path, cancel):
    item, exporter = session
    item.is_stopped = True
    item.segments[0].audio_path = "not-present.wav"
    rendered, final = tmp_path / "new.mp4", tmp_path / "reviewed.mp4"
    rendered.write_bytes(b"validated replacement")
    final.write_bytes(b"previous output")
    ready, release = threading.Event(), threading.Event()
    def render(**kwargs):
        ready.set()
        assert release.wait(5)
        kwargs["publish_callback"](rendered, final)
        return {"output_filename": final.name, "elapsed_seconds": 1}
    exporter.export.side_effect = render
    async def run():
        task = asyncio.create_task(main.export_hq(main.ExportHQRequest(task_id=item.task_id, allow_incomplete=True)))
        try:
            assert await asyncio.to_thread(ready.wait, 5)
            if cancel:
                await main.cancel_export_hq(item.task_id)
            release.set()
            if cancel:
                with pytest.raises(HTTPException):
                    await task
                assert final.read_bytes() == b"previous output"
            else:
                assert (await task)["output_incomplete"]
                assert final.read_bytes() == b"validated replacement"
        finally:
            release.set()
    asyncio.run(run())


@pytest.mark.parametrize("status", ["RUNNING", "CANCELLING"])
def test_reopen_source_cannot_restart_translation_during_export(session, monkeypatch, status):
    item, _ = session
    item.can_retry = True
    item.retry_failed_synthesis = AsyncMock()
    monkeypatch.setattr(main, "_saved_project_candidates", lambda **kwargs: [(item.task_id, {}, "full")])
    main.active_export_tasks[f"export_{item.task_id}"] = {"status": status}
    with pytest.raises(HTTPException) as failure:
        asyncio.run(main._restore_matching_saved_project(video_path=item.video_path,
            voice="voice", engine="edge-tts", asr_engine="asr", visual_translation=True, translation_mode="full"))
    assert failure.value.status_code == 409
    item.retry_failed_synthesis.assert_not_awaited()


def test_websocket_reconnect_keeps_partial_download(session):
    item, _ = session
    item.initialized = False
    item.output_incomplete = True
    item.output_missing_segment_ids = [0]
    item.output_filename = "partial.mp4"
    item.output_video_url = "/api/outputs/partial.mp4"
    with TestClient(main.app).websocket_connect(f"/ws/stream/{item.task_id}") as socket:
        progress = socket.receive_json()
        assert progress["output_incomplete"] is True
        assert progress["output_video_url"] == item.output_video_url
        assert progress["output_missing_segment_ids"] == [0]
