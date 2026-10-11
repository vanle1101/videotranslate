"""Caption changes are display-only transactions, never new translation/TTS."""
import asyncio
import wave
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

import main
from config import settings
from core.streaming.pipeline import StreamingPipelineSession, SegmentItem


@pytest.fixture
def caption_session(tmp_path, monkeypatch):
    for key in ("BASE_DIR", "TEMP_DIR", "WORKSPACE_DIR"):
        monkeypatch.setattr(settings, key, tmp_path)
    session = StreamingPipelineSession("caption-test", tmp_path / "source.mp4")
    session.initialized = True
    session.total_duration = 3
    session.video_size = (1920, 1080)
    segment = SegmentItem(0, 0, 3, 3)
    segment.status = "READY"
    segment.final_vi = "Về nhất, phá kỷ lục của trường."
    segment.subtitle_timing_source = "edge-word-boundary"
    segment.subtitle_cues = [{"start": .1, "end": 2.5, "text": segment.final_vi}]
    segment.audio_path = str(session.segments_dir / "seg_0.wav")
    Path(segment.audio_path).write_bytes(b"existing untouched audio")
    session.segments[0] = segment
    session.screen_texts = [{"start": 0, "end": 3, "bbox": [.3, .8, .4, .05],
        "kind": "subtitle", "source_method": "local-ocr", "confidence": .98,
        "needs_review": False}]
    session.output_video_url = "/api/outputs/old.mp4"
    session.output_filename = "old.mp4"
    session.translator = Mock()
    session.tts_engine = Mock()
    monkeypatch.setattr(main, "get_streaming_session", lambda key: session if key == session.task_id else None)
    monkeypatch.setattr(main, "active_export_tasks", {})
    return session


def request(session, data):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as api:
            return await api.post(f"/api/streaming/{session.task_id}/caption-style", json=data)
    return asyncio.run(run())


def test_style_updates_shared_preview_without_touching_audio_or_translation(caption_session):
    session = caption_session
    before_audio = Path(session.segments[0].audio_path).read_bytes()
    signature = main.export_revision_signature(session)
    response = request(session, {"background_color": "#123abc", "text_color": "#FEDCBA",
                                 "position": "top", "blur_original": True})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["caption_style"]["background_color"] == "#123ABC"
    assert result["caption_style_revision"] == 1 and result["has_subtitle_regions"]
    assert result["output_video_url"] == "" and result["output_outdated"] is True
    cue = result["segments"][0]["caption_layout"]["cues"][0]
    assert cue["color"] == "#FEDCBA" and cue["bbox"][1] < .15
    assert result["segments"][0]["caption_layout"]["source_masks"]
    assert result["source_subtitle_masks"]
    assert min(mask["start"] for mask in result["source_subtitle_masks"]) == 0
    assert max(mask["end"] for mask in result["source_subtitle_masks"]) >= 3
    assert Path(session.segments[0].audio_path).read_bytes() == before_audio
    assert session.segments[0].final_vi == "Về nhất, phá kỷ lục của trường."
    assert main.export_revision_signature(session) != signature
    session.translator.assert_not_called()
    assert session.tts_engine.mock_calls == []
    assert main.review_sidecar_payload(session)["caption_style"] == session.caption_style


def test_same_style_is_idempotent_and_preserves_current_result(caption_session):
    result = request(caption_session, {}).json()
    assert result["caption_style_revision"] == 0 and result["output_outdated"] is False
    assert result["output_video_url"] == "/api/outputs/old.mp4"


def test_valid_translated_preview_is_editable_even_with_other_unfinished_rows(caption_session, monkeypatch):
    session = caption_session
    session.segments[0].needs_review = True
    session.segments[0].preview_is_draft = True
    later = SegmentItem(1, 3, 6, 3)
    later.status = "WAITING"
    session.segments[1] = later
    session.total_duration = 6
    monkeypatch.setattr(session, "get_progress", lambda: {"status": "PREVIEW_READY",
        "preview_output_ready": True, "preview_untranslated_ids": [], "preview_missing_audio_ids": []})
    response = request(session, {"position": "top", "background_color": "#123456", "blur_original": True})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["has_subtitle_regions"] is True
    assert payload["segments"][0]["caption_layout"]["source_masks"]
    assert payload["segments"][0]["needs_review"] is True
    assert later.status == "WAITING"
    assert session.caption_style["background_color"] == "#123456"
    assert (settings.BASE_DIR / "workspace" / "projects" / f"{session.task_id}.json").exists()
    session.translator.assert_not_called()
    assert session.tts_engine.mock_calls == []


@pytest.mark.parametrize("invalid", [{"preview_output_ready": False},
    {"preview_untranslated_ids": [0]}, {"preview_missing_audio_ids": [0]}])
def test_invalid_preview_cannot_unlock_style_changes(caption_session, monkeypatch, invalid):
    later = SegmentItem(1, 3, 6, 3)
    later.status = "WAITING"
    caption_session.segments[1] = later
    progress = {"status": "PREVIEW_READY", "preview_output_ready": True,
        "preview_untranslated_ids": [], "preview_missing_audio_ids": [], **invalid}
    monkeypatch.setattr(caption_session, "get_progress", lambda: progress)
    response = request(caption_session, {"position": "top"})
    assert response.status_code == 409
    assert caption_session.caption_style == {}


def make_real_preview(session):
    session.translation_mode = "preview"
    session.preview_seconds = 3
    session.total_duration = 6
    session._chunked_source_started = True
    session._source_prepared_seconds = 6
    session._visual_completed_seconds = 3
    session._preview_ready = True
    row = session.segments[0]
    row.audio_url = f"/api/streaming/audio/{session.task_id}/0?rev=0"
    with wave.open(row.audio_path, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x01\x00" * 16000)
    future = SegmentItem(1, 3, 6, 3)
    future.text_zh = future.asr_text = "后面的句子"
    session.segments[1] = future


def test_genuine_preview_can_restyle_without_processing_future_source(caption_session):
    make_real_preview(caption_session)
    before_audio = Path(caption_session.segments[0].audio_path).read_bytes()
    response = request(caption_session, {"text_color": "#ffffff", "background_color": "#113355",
                                         "position": "bottom", "blur_original": True})
    assert response.status_code == 200, response.text
    assert response.json()["caption_style"]["blur_original"] is True
    assert response.json()["segments"][0]["caption_layout"]["source_masks"]
    assert caption_session.segments[1].status == "WAITING"
    assert caption_session.segments[1].final_vi == ""
    assert Path(caption_session.segments[0].audio_path).read_bytes() == before_audio
    caption_session.translator.assert_not_called()
    assert caption_session.tts_engine.mock_calls == []


@pytest.mark.parametrize("failure", ["empty_audio", "missing_audio", "untranslated", "active"])
def test_incomplete_preview_cannot_unlock_style_by_a_stale_ready_flag(caption_session, failure):
    make_real_preview(caption_session)
    row = caption_session.segments[0]
    if failure == "empty_audio":
        Path(row.audio_path).write_bytes(b"")
    elif failure == "missing_audio":
        Path(row.audio_path).unlink()
    elif failure == "untranslated":
        row.final_vi = ""
        row.text_zh = "原文"
    else:
        caption_session.is_running = True
    response = request(caption_session, {"position": "bottom"})
    assert response.status_code == 409, response.text
    assert caption_session.caption_style == {}


def test_style_change_invalidates_completed_export_polling(caption_session):
    export_id = f"export_{caption_session.task_id}"
    main.active_export_tasks[export_id] = {"status": "COMPLETED", "video_url": "/api/outputs/old.mp4"}
    assert request(caption_session, {"position": "top"}).status_code == 200
    assert export_id not in main.active_export_tasks


def test_style_disk_failure_is_not_acknowledged_and_retains_previous_result(caption_session, monkeypatch):
    session = caption_session
    session.total_duration = 3
    manifest = session.persist()
    durable = manifest.read_bytes()
    export_id = f"export_{session.task_id}"
    completed = {"status": "COMPLETED", "video_url": "/api/outputs/old.mp4"}
    main.active_export_tasks[export_id] = completed
    session.auto_export_signature = "previous-result-signature"
    monkeypatch.setattr(session, "persist", Mock(side_effect=OSError("disk full")))

    response = request(session, {"position": "top"})

    assert response.status_code == 507, response.text
    assert "dung lượng" in response.json()["detail"]
    assert session.caption_style == {} and session.caption_style_revision == 0
    assert session.caption_output_outdated is False
    assert session.output_filename == "old.mp4" and session.output_video_url == "/api/outputs/old.mp4"
    assert session.auto_export_signature == "previous-result-signature"
    assert main.active_export_tasks[export_id] is completed
    assert manifest.read_bytes() == durable


@pytest.mark.parametrize("position", ["auto", "top", "middle", "bottom"])
def test_export_keeps_blur_evidence_independent_of_placement_toggle(caption_session, monkeypatch, position):
    session = caption_session
    session.caption_style = {"position": position, "blur_original": True}
    exporter = Mock()
    exporter.export.return_value = {"output_filename": "styled.mp4", "elapsed_seconds": 1}
    monkeypatch.setattr(main, "HQExporter", Mock(return_value=exporter))
    asyncio.run(main.export_hq(main.ExportHQRequest(task_id=session.task_id, translate_screen_text=False)))
    kwargs = exporter.export.call_args.kwargs
    assert kwargs["screen_texts"] is None or kwargs["screen_texts"] == []
    assert kwargs["source_screen_texts"] == session.screen_texts
    assert kwargs["caption_style"] == session.caption_style


@pytest.mark.parametrize("data", [{"text_color": "red"}, {"background_color": "#000000;blur"},
    {"text_color": 123}, {"position": "outside"}, {"blur_original": "true"}, {"unknown": True},
    {"background_opacity": -1}, {"background_opacity": 101}, {"background_opacity": True},
    {"font_scale": 49}, {"font_scale": 201}, {"font_scale": "120"},
    {"font_family": "Arial;bad"}, {"bold": 1}, {"outline": "true"},
    {"font_size_pt": True}, {"font_size_pt": "12"}, {"font_size_pt": []},
    {"font_size_pt": 5.5}, {"font_size_pt": 96.5}, {"font_size_pt": 12.25}])
def test_invalid_style_never_mutates_session(caption_session, data):
    response = request(caption_session, data)
    assert response.status_code == 422
    assert caption_session.caption_style == {} and caption_session.output_filename == "old.mp4"


def test_transparent_background_and_typography_update_shared_layout(caption_session):
    before = Path(caption_session.segments[0].audio_path).read_bytes()
    result = request(caption_session, {"background_opacity": 0, "font_scale": 150,
        "font_family": "Tahoma", "text_color": "#FFFFFF", "bold": False, "outline": True,
        "position": "bottom", "blur_original": True})
    assert result.status_code == 200, result.text
    payload = result.json()
    cue = payload["segments"][0]["caption_layout"]["cues"][0]
    assert payload["caption_style"]["background_opacity"] == 0
    assert payload["caption_style"]["font_scale"] == 150
    assert cue["font_family"] == "Tahoma" and cue["bold"] is False
    assert cue["background_opacity"] == 0 and cue["outline_width"] > 0
    assert cue["outline_color"] == "#000000"
    assert payload["output_outdated"] and payload["source_subtitle_masks"]
    assert Path(caption_session.segments[0].audio_path).read_bytes() == before
    assert caption_session.tts_engine.mock_calls == []


@pytest.mark.parametrize("points", [None, 6, 12.5, 24, 96])
def test_word_point_size_api_contract_and_absolute_precedence(caption_session, points):
    before = Path(caption_session.segments[0].audio_path).read_bytes()
    response = request(caption_session, {"font_size_pt": points, "font_scale": 200,
        "font_family": "Segoe UI", "position": "top"})
    assert response.status_code == 200, response.text
    payload = response.json()
    cue = payload["segments"][0]["caption_layout"]["cues"][0]
    assert payload["caption_style"]["font_size_pt"] == points and cue["font_size_pt"] == points
    assert cue["font_family"] == "Segoe UI"
    if points in (6, 12.5, 24):
        assert cue["font_size"] == pytest.approx(points * 4 / 3, abs=.001)
    assert Path(caption_session.segments[0].audio_path).read_bytes() == before
    assert caption_session.tts_engine.mock_calls == [] and caption_session.translator.mock_calls == []


@pytest.mark.parametrize("state", ["processing", "exporting", "failed", "not_ready"])
def test_style_cannot_race_active_work(caption_session, state):
    if state == "processing":
        caption_session.is_running = True
    elif state == "exporting":
        main.active_export_tasks[f"export_{caption_session.task_id}"] = {"status": "RUNNING"}
    elif state == "failed":
        caption_session.error = "failed"
    else:
        caption_session.segments[0].status = "TTS"
    assert request(caption_session, {"position": "bottom"}).status_code == 409
    assert caption_session.caption_style == {}


def test_blur_requires_verified_source_region_and_can_be_disabled(caption_session):
    caption_session.screen_texts = []
    assert request(caption_session, {"blur_original": True}).status_code == 422
    result = request(caption_session, {"position": "bottom"}).json()
    assert result["has_subtitle_regions"] is False
    assert not result["segments"][0]["caption_layout"].get("source_masks")


def test_invalid_session_returns_clear_404(caption_session, monkeypatch):
    monkeypatch.setattr(main, "get_streaming_session", lambda key: None)
    assert request(caption_session, {}).status_code == 404
