"""Transcript text/audio transactions, lifecycle and export consistency; offline."""
import asyncio
import threading
import wave
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
    # This transaction fixture uses text bytes as audio. Actual PCM activity and
    # service word boundaries are exercised in test_speech_timing.py.
    monkeypatch.setattr("core.engines.alignment.speech_timing.audio_activity_span", lambda path, **kwargs: (0, 3))
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


@pytest.mark.parametrize("text", ["Lời thoại cũ", "Bản dịch đã kiểm tra"])
def test_reviewed_visual_sentence_can_generate_audio_even_when_text_is_unchanged(session, text):
    segment = session.segments[0]
    segment.status = "NEEDS_REVIEW"
    segment.source_method = "video-ai"
    segment.needs_review = True
    segment.review_reason = "Chữ nguồn chưa rõ"
    Path(segment.audio_path).unlink()
    segment.audio_path = segment.audio_url = None

    async def run():
        async with client() as api:
            response = await api.patch(route(session), json={"final_vi": text})
            assert response.status_code == 200, response.text
            saved = response.json()["segment"]
            assert saved["status"] == "READY"
            assert saved["needs_review"] is False
            assert not saved["review_reason"]
            assert saved["final_vi"] == text
            assert saved["revision"] == 1
            assert saved["audio_url"].endswith("?rev=1")
            audio = await api.get(saved["audio_url"])
            assert audio.content == (text + " aligned").encode()

    asyncio.run(run())
    session.tts_engine.synthesize.assert_called_once()
    assert not session.edit_tasks
    assert not list(session.cache_dir.glob("edit_*"))


@pytest.mark.parametrize("source_method", ["video-ai", "text-ai", "audio"])
def test_uncertain_draft_is_audible_before_edit_and_edit_replaces_audio_without_double_counting(session, source_method):
    segment = session.segments[0]
    Path(segment.audio_path).unlink()
    segment.audio_path = segment.audio_url = None
    segment.status = "WAITING"
    segment.source_method = source_method
    segment.translation_provider = "opencode"
    segment.translation_model = "muse-spark-1.3-contributor-free"
    segment.evidence_mode = "asr-ocr-text"
    segment.needs_review = True
    segment.review_reason = "Một từ nguồn chưa rõ"
    session.visual_translation = source_method != "audio"
    session.asr_engine = Mock()
    segment.asr_pretranscribed = True
    session.translator = Mock()
    session.translator.translate_single_segment.return_value = {
        "final_vi": segment.final_vi, "needs_review": True, "review_reason": segment.review_reason,
    }

    async def run():
        session.is_running = True
        await session.queue.put((0, 0))
        await session._worker_loop()
        draft = segment.to_dict()
        assert draft["status"] == "READY" and draft["needs_review"] and draft["preview_is_draft"]
        assert draft["review_reason"] == "Một từ nguồn chưa rõ"
        assert draft["subtitle_cues"] and draft["speech_start"] is not None
        assert session.error is None and session.first_play_emitted
        assert session.playable_until == session.total_duration
        assert session.get_progress()["status"] == "COMPLETED"
        assert session.get_telemetry()["status"] == "finished"
        assert session.get_telemetry()["review_count"] == 1
        assert session.total_processed_duration == segment.duration
        async with client() as api:
            audio = await api.get(draft["audio_url"])
            assert audio.status_code == 200 and audio.content == "Lời thoại cũ aligned".encode()
            exported = await api.post("/api/streaming/export-hq", json={"task_id": session.task_id})
            assert exported.status_code == 409
            response = await api.patch(route(session), json={"final_vi": "Câu đã nghe và sửa"})
            assert response.status_code == 200, response.text
            saved = response.json()["segment"]
            assert not saved["needs_review"] and not saved["preview_is_draft"]
            assert saved["review_reason"] is None and saved["revision"] == 1
            assert (saved["start"], saved["end"]) == (draft["start"], draft["end"])
            assert saved["translation_provider"] == draft["translation_provider"]
            assert saved["translation_model"] == draft["translation_model"]
            assert saved["evidence_mode"] == draft["evidence_mode"]
            edited_audio = await api.get(saved["audio_url"])
            assert edited_audio.content == "Câu đã nghe và sửa aligned".encode()
            assert session.total_processed_duration == segment.duration
            assert session.get_telemetry()["review_count"] == 0
            assert session.get_telemetry()["review_message"] is None

    asyncio.run(run())
    assert session.tts_engine.synthesize.call_count == 2
    session.asr_engine.transcribe.assert_not_called()
    if session.visual_translation:
        session.translator.translate_single_segment.assert_not_called()
    assert not list(session.cache_dir.glob("tts_*_raw.wav"))
    assert not list(session.cache_dir.glob("edit_*"))


def test_empty_uncertain_draft_keeps_review_without_freezing_playback_or_inventing_voice(session):
    segment = session.segments[0]
    Path(segment.audio_path).unlink()
    segment.audio_path = segment.audio_url = None
    segment.status = "WAITING"
    segment.source_method = "text-ai"
    segment.final_vi = ""
    segment.needs_review = True
    segment.review_reason = "Câu này không nghe rõ"
    session.visual_translation = True

    async def run():
        session.is_running = True
        await session.queue.put((0, 0))
        await session._worker_loop()
        assert segment.status == "READY" and segment.needs_review
        assert segment.audio_url is None and not segment.confirmed_silence
        assert session.error is None and session.first_play_emitted
        assert session.playable_until == session.total_duration
        assert session.get_progress()["review_count"] == 1
        assert session.get_progress()["status"] == "COMPLETED"
        await session.edit_segment(0, "Bổ sung câu nghe được")
        assert not segment.needs_review and segment.audio_url
        assert session.total_processed_duration == segment.duration

    asyncio.run(run())
    session.tts_engine.synthesize.assert_called_once()
    assert session.tts_engine.synthesize.call_args.kwargs["text"] == "Bổ sung câu nghe được"


def test_editing_one_draft_keeps_other_drafts_playable_and_does_not_fail_the_session(session):
    first = session.segments[0]
    first.needs_review = True
    first.review_reason = "Câu đầu cần nghe lại"
    second = SegmentItem(1, 5, 8, 3)
    second.status = "READY"
    second.needs_review = True
    second.review_reason = "Câu sau cần nghe lại"
    second.final_vi = "Bản nháp câu sau"
    session.segments[1] = second
    session.total_processed_duration = first.duration + second.duration

    asyncio.run(session.edit_segment(0, "Câu đầu đã sửa"))
    assert first.needs_review is False and second.needs_review is True
    assert second.review_reason == "Câu sau cần nghe lại"
    assert session.total_processed_duration == 6
    assert session.error is None
    assert session.get_progress()["status"] == "COMPLETED"
    assert session.get_progress()["review_count"] == 1
    assert session.get_telemetry()["status"] == "finished"
    assert session.playable_until == session.total_duration


@pytest.mark.parametrize("draft", ["", "Lời nhận dạng nhầm"])
def test_explicit_silence_approves_review_without_tts_and_exports_silent_timeline(session, monkeypatch, draft):
    from core.streaming.export import HQExporter

    segment = session.segments[0]
    segment.status = "NEEDS_REVIEW"
    segment.needs_review = True
    segment.review_reason = "ASR có chữ nhưng không nghe rõ lời nói"
    segment.final_vi = segment.literal_vi = segment.natural_vi = draft
    segment.translation_provider = "openrouter-free"
    segment.translation_model = "provider/model:free"
    segment.evidence_mode = "asr-ocr-text"
    session.error = session._review_message()
    session.visual_translation = True
    session.screen_texts = [{"id": "o0", "start": 2, "end": 5, "kind": "title",
                             "text_vi": "Tiêu đề vẫn còn", "bbox": [.1, .1, .8, .1]}]
    audio_path = Path(segment.audio_path)
    old_url = segment.audio_url
    captured = {}

    def export(**kwargs):
        captured.update(kwargs)
        output = session.cache_dir / "silent-timeline.wav"
        HQExporter._assemble_voice_timeline(kwargs["segments"], kwargs["total_duration"], output)
        with wave.open(str(output), "rb") as sound:
            assert sound.getnframes() == round(session.total_duration * 44100)
            assert not any(sound.readframes(sound.getnframes()))
        output.unlink()
        return {"output_filename": "silent.mp4", "elapsed_seconds": 0.1}

    monkeypatch.setattr(main, "HQExporter", lambda: SimpleNamespace(export=export))

    async def run():
        async with client() as api:
            payload = {"final_vi": "", "confirm_silence": True}
            response = await api.patch(route(session), json=payload)
            assert response.status_code == 200, response.text
            saved = response.json()["segment"]
            assert saved["status"] == "READY" and saved["confirmed_silence"] is True
            assert saved["needs_review"] is False and saved["review_reason"] is None
            assert saved["final_vi"] == saved["natural_vi"] == saved["literal_vi"] == saved["text_vi"] == ""
            assert saved["audio_url"] is None and segment.audio_path is None
            assert saved["subtitle_cues"] == [] and saved["tts_duration"] == 0
            assert saved["revision"] == 1 and (saved["start"], saved["end"]) == (2, 5)
            assert saved["text_zh"] == "原文"  # Retain the recognition evidence.
            assert saved["translation_provider"] == "openrouter-free"
            assert saved["translation_model"] == "provider/model:free"
            assert saved["evidence_mode"] == "asr-ocr-text"
            assert response.json()["screen_texts"] == session.screen_texts
            assert not audio_path.exists() and (await api.get(old_url)).status_code == 404
            assert session.error is None and session.total_processed_duration == 3
            repeated = await api.patch(route(session), json=payload)
            assert repeated.status_code == 200 and repeated.json()["segment"] == saved
            assert session.total_processed_duration == 3
            exported = await api.post("/api/streaming/export-hq", json={"task_id": session.task_id})
            assert exported.status_code == 200, exported.text
    asyncio.run(run())
    assert captured["segments"][0]["audio_path"] is None
    assert captured["screen_texts"] == session.screen_texts
    session.tts_engine.synthesize.assert_not_called()
    session.aligner.apply_atempo.assert_not_called()
    assert not session.edit_tasks


@pytest.mark.parametrize("payload", [
    {"final_vi": "", "confirm_silence": False},
    {"final_vi": "   "},
    {"final_vi": "Câu vẫn có chữ", "confirm_silence": True},
    {"final_vi": "", "confirm_silence": "true"},
    {"final_vi": "", "confirm_silence": 1},
])
def test_silence_requires_explicit_boolean_and_empty_text_without_changing_draft(session, payload):
    segment = session.segments[0]
    segment.status = "NEEDS_REVIEW"
    segment.needs_review = True
    before = segment.to_dict()

    async def run():
        async with client() as api:
            response = await api.patch(route(session), json=payload)
            assert response.status_code == 422
    asyncio.run(run())
    assert segment.to_dict() == before
    assert Path(segment.audio_path).read_bytes() == b"old audio"
    session.tts_engine.synthesize.assert_not_called()


def test_silence_cannot_clear_an_already_approved_spoken_sentence(session):
    before = session.segments[0].to_dict()

    async def run():
        async with client() as api:
            response = await api.patch(route(session), json={"final_vi": "", "confirm_silence": True})
            assert response.status_code == 409
    asyncio.run(run())
    assert session.segments[0].to_dict() == before
    session.tts_engine.synthesize.assert_not_called()


def test_locked_old_audio_does_not_partially_approve_silence(session, monkeypatch):
    segment = session.segments[0]
    segment.status = "NEEDS_REVIEW"
    segment.needs_review = True
    before = segment.to_dict()
    old_path = Path(segment.audio_path)
    real_unlink = Path.unlink

    def unlink(path, *args, **kwargs):
        if path == old_path:
            raise PermissionError("audio still open")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)

    async def run():
        async with client() as api:
            response = await api.patch(route(session), json={"final_vi": "", "confirm_silence": True})
            assert response.status_code == 503
    asyncio.run(run())
    assert segment.to_dict() == before and old_path.read_bytes() == b"old audio"
    assert not session.edit_tasks


def test_confirmed_silence_can_be_replaced_with_spoken_text(session):
    segment = session.segments[0]
    segment.status = "NEEDS_REVIEW"
    segment.needs_review = True

    async def run():
        async with client() as api:
            silent = await api.patch(route(session), json={"final_vi": "", "confirm_silence": True})
            assert silent.status_code == 200
            spoken = await api.patch(route(session), json={"final_vi": "Thật ra vẫn có lời thoại"})
            assert spoken.status_code == 200
            saved = spoken.json()["segment"]
            assert saved["confirmed_silence"] is False and saved["revision"] == 2
            assert saved["audio_url"].endswith("?rev=2") and saved["subtitle_cues"]
            assert session.total_processed_duration == 3
    asyncio.run(run())
    session.tts_engine.synthesize.assert_called_once()


def test_visual_transcript_edit_masks_source_regions_without_repeating_text_and_preserves_titles(session):
    session.visual_translation = True
    session.segments[0].source_method = "video-ai"
    session.screen_texts = [
        {"start": 1, "end": 4, "kind": "subtitle", "text_vi": "Lời cũ trên hình", "bbox": [.1, .8, .8, .1]},
        {"start": 4, "end": 6, "kind": "subtitle", "text_vi": "Lời cũ tiếp theo", "bbox": [.1, .8, .8, .1]},
        {"start": 0, "end": 6, "kind": "title", "text_vi": "Tên bộ phim", "bbox": [.1, .1, .8, .1]},
        {"start": 7, "end": 9, "kind": "subtitle", "text_vi": "Câu khác", "bbox": [.1, .8, .8, .1]},
    ]
    edited_text = ("Sau khi kiểm tra lại toàn bộ nội dung, đây là câu đã sửa để lời đọc và phụ đề "
                   "đồng bộ liên tục dù chữ gốc đổi nhiều lần trong cùng một câu thoại.")

    async def run():
        async with client() as api:
            response = await api.patch(route(session), json={"final_vi": edited_text})
            assert response.status_code == 200, response.text
            screens = response.json()["screen_texts"]
            for screen in screens:
                if screen["kind"] == "subtitle" and screen["start"] < 5 and screen["end"] > 2:
                    assert screen["text_vi"] == ""
                    assert screen["mask_only"] is True
                    assert screen["bbox"] == [.1, .8, .8, .1]
                    assert 2 <= screen["start"] < screen["end"] <= 5
                    assert not screen.get("needs_review")
            assert any(screen["start"] == 1 and screen["end"] == 2
                       and screen["text_vi"] == "Lời cũ trên hình" for screen in screens)
            assert any(screen["start"] == 5 and screen["end"] == 6
                       and screen["text_vi"] == "Lời cũ tiếp theo" for screen in screens)
            assert any(screen["kind"] == "title" and screen["text_vi"] == "Tên bộ phim" for screen in screens)
            assert any(screen["start"] == 7 and screen["text_vi"] == "Câu khác" for screen in screens)
            assert screens == session.screen_texts
            saved = response.json()["segment"]
            cues = saved["subtitle_cues"]
            assert len(cues) > 1
            assert " ".join(" ".join(cue["text"].split()) for cue in cues) == edited_text
            assert (cues[0]["start"], cues[-1]["end"]) == (2, 5)
            assert all(left["end"] == right["start"] for left, right in zip(cues, cues[1:]))
            # Mask boundaries must not restart or hide the sentence's own cues.
            from core.subtitle_cues import normalize_screen_texts, uncovered_intervals
            normalized = normalize_screen_texts(screens)
            assert sum(item.get("mask_only", False) for item in normalized) == 2
            for cue in cues:
                assert uncovered_intervals(cue["start"], cue["end"], normalized) == [(cue["start"], cue["end"])]
            assert session.events[-1][1]["screen_texts"] == screens

    asyncio.run(run())


def test_editing_speech_does_not_approve_an_unreliable_ocr_mask(session):
    session.visual_translation = True
    session.segments[0].source_method = "video-ai"
    session.screen_texts = [{"start": 2, "end": 5, "kind": "subtitle", "text_vi": "Bản nháp",
                             "bbox": [0, 0, 1, .8], "needs_review": True,
                             "review_reason": "Vùng chữ quá lớn"}]

    async def run():
        async with client() as api:
            response = await api.patch(route(session), json={"final_vi": "Lời thoại đã sửa"})
            assert response.status_code == 200
            assert response.json()["segment"]["final_vi"] == "Lời thoại đã sửa"
            for screen in response.json()["screen_texts"]:
                assert screen["needs_review"] is True
                assert screen["review_reason"]

    asyncio.run(run())


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


@pytest.mark.parametrize("show_screen_text", [False, True])
@pytest.mark.parametrize("visual_translation", [False, True])
def test_export_screen_text_choice_keeps_spoken_captions_and_original_evidence(
        session, monkeypatch, show_screen_text, visual_translation):
    session.visual_translation = visual_translation
    session.screen_texts = [{"id": "o0", "start": 2, "end": 5, "kind": "title",
                             "text_vi": "Tiêu đề", "bbox": [.1, .1, .8, .1], "needs_review": False}]
    captured = {}

    def export(**kwargs):
        captured.update(kwargs)
        return {"output_filename": "final.mp4", "elapsed_seconds": .1}

    monkeypatch.setattr(main, "HQExporter", lambda: SimpleNamespace(export=export))

    async def run():
        async with client() as api:
            response = await api.post("/api/streaming/export-hq", json={
                "task_id": session.task_id, "mask_chinese": True,
                "translate_screen_text": show_screen_text})
            assert response.status_code == 200, response.text

    asyncio.run(run())
    expected_screens = (session.screen_texts if show_screen_text else []) if visual_translation else None
    assert captured["screen_texts"] == expected_screens
    # OCR may guide Vietnamese placement, but even a legacy client asking to
    # mask Chinese must preserve the source pixels under the current contract.
    assert captured["mask_chinese"] is False
    assert captured["segments"][0]["final_vi"] == session.segments[0].final_vi
    assert len(session.screen_texts) == 1


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
