"""Automatic output policy remains strict when media or review evidence is unavailable."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import wave

import pytest
import main
from core.streaming.pipeline import SegmentItem


def _session(tmp_path, *, ready=True, review_status="completed"):
    segment = SegmentItem(0, 0, 2, 2)
    segment.status = "READY" if ready else "FAILED"
    segment.text_zh = "测试"
    segment.final_vi = "Bản dịch."
    segment.needs_review = False
    segment.review_summary = {}
    if ready:
        audio = tmp_path / "voice.wav"
        with wave.open(str(audio), "wb") as output:
            output.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            output.writeframes(b"\x01\x00" * 24000)
        segment.audio_path = str(audio)
    return SimpleNamespace(
        task_id="automatic-output", segments={0: segment}, total_duration=2,
        is_running=False, is_stopped=False, is_paused=False, is_editing=False,
        error=None, _stop_draining=False, initialized=True,
        _chunked_source_started=False, _visual_incremental_started=False,
        _visual_completed_seconds=0, _source_prepared_seconds=0,
        translation_mode="full", auto_export_result=True,
        review_summary={"status": review_status} if review_status else {},
        warnings=[], output_filename="", caption_output_outdated=False,
        output_video_url="", output_review_url="", output_incomplete=False,
        output_missing_segment_ids=[], caption_style={}, screen_texts=[],
        automatic_output={}, persist=Mock(),
    )


def test_ready_media_auto_finalize_schedules_strict_export(tmp_path, monkeypatch):
    session = _session(tmp_path)
    schedule = Mock()
    monkeypatch.setattr(main, "get_streaming_session", lambda task_id: session)
    monkeypatch.setattr(main, "schedule_reviewed_export", schedule)
    monkeypatch.setattr(main, "broadcast_session_event", AsyncMock())

    asyncio.run(main.finalize_session_output(session.task_id))

    schedule.assert_called_once_with(session.task_id)
    assert session.automatic_output == {}
    assert not session.warnings


def test_incomplete_media_is_blocked_without_partial_final_claim(tmp_path, monkeypatch):
    session = _session(tmp_path, ready=False, review_status=None)
    broadcast = AsyncMock()
    schedule = Mock()
    monkeypatch.setattr(main, "get_streaming_session", lambda task_id: session)
    monkeypatch.setattr(main, "schedule_reviewed_export", schedule)
    monkeypatch.setattr(main, "broadcast_session_event", broadcast)

    asyncio.run(main.finalize_session_output(session.task_id))

    assert session.automatic_output == {
        "status": "blocked", "policy": "strict_media", "reason": "media_incomplete"
    }
    assert "Chưa thể xuất MP4 tự động" in session.warnings[0]
    schedule.assert_not_called()
    assert broadcast.await_args.args[1] == "result_error"
    assert not session.output_video_url and not session.output_filename


def test_known_source_gap_schedules_labeled_draft_only_when_rows_are_renderable(tmp_path, monkeypatch):
    session = _session(tmp_path, review_status="incomplete")
    session._chunked_source_started = True
    session._source_prepared_seconds = 1.8
    session._source_scanned_seconds = 2.0
    session._source_missing_speech_spans = [[0.8, 0.928]]
    session._source_failure_code = "source_speech_unrecognized"
    session._source_failure_start = 0.8
    session._source_coverage_intervals = [{
        "start": 0.8, "end": 0.928, "state": "unresolved",
        "missing_speech_spans": [[0.8, 0.928]], "error_code": "source_speech_unrecognized",
    }]
    # Saved, source-incomplete projects restore with this recovery marker even
    # when all persisted speech rows are still usable.
    session.error = "Dịch video chưa xong; các câu đã dịch và giọng đọc được giữ. Bấm Tiếp tục để xử lý phần còn lại."
    session._startup_failed = True
    schedule = Mock()
    monkeypatch.setattr(main, "get_streaming_session", lambda task_id: session)
    monkeypatch.setattr(main, "schedule_reviewed_export", schedule)
    monkeypatch.setattr(main, "broadcast_session_event", AsyncMock())

    asyncio.run(main.finalize_session_output(session.task_id))

    schedule.assert_called_once_with(session.task_id, allow_incomplete=True)
    assert session.automatic_output == {
        "status": "pending", "policy": "strict_media", "reason": "source_coverage_draft"
    }
    session.output_incomplete = True
    assert "0.800–0.928 giây" in main.session_output_details(session)["output_warning"]
    assert not session.is_running


def test_known_source_gap_can_export_draft_with_failed_tts_row_and_original_audio_fallback(tmp_path, monkeypatch):
    session = _session(tmp_path, review_status="incomplete")
    session._chunked_source_started = True
    session._source_prepared_seconds = 2.0
    session._source_scanned_seconds = 2.0
    session._source_missing_speech_spans = [[0.8, 0.928]]
    session._source_failure_code = "source_speech_unrecognized"
    session._source_failure_start = 0.8
    session._source_coverage_intervals = [{
        "start": 0.8, "end": 0.928, "state": "unresolved",
        "missing_speech_spans": [[0.8, 0.928]], "error_code": "source_speech_unrecognized",
    }]
    row = session.segments[0]
    row.start, row.end, row.duration = 1.0, 1.5, .5
    row.source_asr_start, row.source_asr_end = 1.1, 1.2
    row.status, row.failed_stage = "FAILED", "TTS"
    row.audio_path = None
    row.error = "Pacing candidate was not independently verified"
    row.source_method = "audio"
    row.source_audio_evidence = "original_audio"
    session.error = "1 câu chưa tạo được giọng."
    schedule = Mock()
    monkeypatch.setattr(main, "get_streaming_session", lambda task_id: session)
    monkeypatch.setattr(main, "schedule_reviewed_export", schedule)
    monkeypatch.setattr(main, "broadcast_session_event", AsyncMock())

    asyncio.run(main.finalize_session_output(session.task_id))

    schedule.assert_called_once_with(session.task_id, allow_incomplete=True)
    assert session.automatic_output == {
        "status": "pending", "policy": "strict_media", "reason": "source_coverage_draft"
    }


def test_known_source_gap_never_becomes_a_draft_when_audio_is_missing(tmp_path, monkeypatch):
    session = _session(tmp_path, review_status="incomplete")
    session._chunked_source_started = True
    session._source_prepared_seconds = 1.8
    session._source_scanned_seconds = 2.0
    session._source_missing_speech_spans = [[0.8, 0.928]]
    session._source_failure_code = "source_speech_unrecognized"
    session.segments[0].audio_path = None
    schedule = Mock()
    broadcast = AsyncMock()
    monkeypatch.setattr(main, "get_streaming_session", lambda task_id: session)
    monkeypatch.setattr(main, "schedule_reviewed_export", schedule)
    monkeypatch.setattr(main, "broadcast_session_event", broadcast)

    asyncio.run(main.finalize_session_output(session.task_id))

    schedule.assert_not_called()
    assert session.automatic_output["status"] == "blocked"
    assert broadcast.await_args.args[1] == "result_error"


def test_review_provider_failure_stays_unapproved_and_blocks_automatic_export(tmp_path, monkeypatch):
    session = _session(tmp_path, review_status="failed")
    schedule = Mock()
    monkeypatch.setattr(main, "get_streaming_session", lambda task_id: session)
    monkeypatch.setattr(main, "schedule_reviewed_export", schedule)
    monkeypatch.setattr(main, "broadcast_session_event", AsyncMock())

    asyncio.run(main.finalize_session_output(session.task_id))

    schedule.assert_not_called()
    assert session.review_summary["status"] == "failed"
    assert any("chưa hoàn tất" in warning for warning in session.warnings)
    assert session.automatic_output == {
        "status": "blocked", "policy": "strict_media", "reason": "review_incomplete"
    }
    assert session.output_filename == ""
    assert Path(session.segments[0].audio_path).is_file()



def test_voice_selection_is_carried_into_new_auto_export_session(monkeypatch):
    captured = {}
    session = SimpleNamespace(task_id="voice-flow", initial_buffer_seconds=10,
                              source_video_url=None, get_progress=lambda: {},
                              start_from_url=AsyncMock())
    monkeypatch.setattr(main, "validate_visual_translation", lambda value: None)
    monkeypatch.setattr(main, "validated_voice", lambda *args: ("edge-tts", "voice-selected"))
    def create(**kwargs):
        for key in ("voice", "tts_engine_name", "translation_mode"):
            setattr(session, key, kwargs[key])
        captured["session"] = session
        return session
    monkeypatch.setattr(main, "create_streaming_session", create)
    monkeypatch.setattr(main, "_restore_matching_saved_project", AsyncMock(return_value=(None, False)))
    monkeypatch.setattr(main.downloader, "normalize_url", lambda value: value)
    monkeypatch.setattr(main, "run_session", AsyncMock())

    asyncio.run(main.start_streaming_url(main.StreamUrlRequest(
        url="https://example.com/video", voice_id="voice-123", translation_mode="full")))

    assert captured["session"].voice == "voice-selected"
    assert captured["session"].tts_engine_name == "edge-tts"
    assert captured["session"].auto_export_result is True
    assert captured["session"].translation_mode == "full"


@pytest.mark.parametrize("provider", ["opencode", "ollama"])
def test_missing_review_starts_once_without_user_action(tmp_path, monkeypatch, provider):
    session = _session(tmp_path, review_status=None)
    async def review():
        session.is_running = True
        session.review_summary = {"status": "running"}
    session.start_automatic_review = AsyncMock(side_effect=review)
    monkeypatch.setattr(main.settings, "LLM_PROVIDER", provider)
    monkeypatch.setattr(main, "get_streaming_session", lambda task_id: session)
    schedule = Mock()
    monkeypatch.setattr(main, "schedule_reviewed_export", schedule)
    async def run():
        await main.finalize_session_output(session.task_id)
        await main.finalize_session_output(session.task_id)
    asyncio.run(run())
    session.start_automatic_review.assert_awaited_once()
    assert session.run_intent == "running"
    assert session.automatic_output == {"status": "pending", "policy": "strict_media", "reason": "review_pending"}
    schedule.assert_not_called()


def test_review_start_failure_is_blocked_without_repeat(tmp_path, monkeypatch):
    session = _session(tmp_path, review_status=None)
    session.start_automatic_review = AsyncMock(side_effect=RuntimeError("unavailable"))
    monkeypatch.setattr(main.settings, "LLM_PROVIDER", "opencode")
    monkeypatch.setattr(main, "get_streaming_session", lambda task_id: session)
    monkeypatch.setattr(main, "broadcast_session_event", AsyncMock())
    schedule = Mock()
    monkeypatch.setattr(main, "schedule_reviewed_export", schedule)
    async def run():
        await main.finalize_session_output(session.task_id)
        await main.finalize_session_output(session.task_id)
    asyncio.run(run())
    session.start_automatic_review.assert_awaited_once()
    assert session.run_intent == "idle"
    assert session.automatic_output["reason"] == "review_incomplete"
    schedule.assert_not_called()
