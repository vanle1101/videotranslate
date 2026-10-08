"""Transcript text/audio transactions, lifecycle and export consistency; offline."""
import asyncio
import threading
import wave
from copy import deepcopy
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
                                   get_audio_duration=Mock(return_value=3.3),
                                   apply_atempo=Mock(side_effect=align))
    # This transaction fixture uses text bytes as audio. Actual PCM activity and
    # service word boundaries are exercised in test_speech_timing.py.
    monkeypatch.setattr(sess, "_dub_audio_duration", lambda path: 3.0)
    monkeypatch.setattr("core.engines.alignment.speech_timing.audio_activity_span", lambda path, **kwargs: (0, 3))
    monkeypatch.setattr("core.engines.alignment.speech_timing._audio_activity_intervals", lambda path, **kwargs: [(0, 3)])
    registry[sess.task_id] = sess
    return sess


def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test")


def test_unknown_source_rows_never_advertise_one_hundred_percent(session):
    session.segments.clear()
    asyncio.run(session._segment_progress("asr", "Đang nhận diện lời nói"))
    progress = session.get_progress()
    assert progress["completed_segments"] == progress["total_segments"] == 0
    assert progress["progress_pct"] is None and progress["status"] == "RUNNING"


def test_manual_speech_failure_is_traceable_without_logging_secret_or_losing_old_audio(session, caplog):
    original = session.segments[0].to_dict()
    old_audio = Path(session.segments[0].audio_path)
    session.tts_engine.synthesize.side_effect = RuntimeError('Authorization: Bearer private-test-key')
    async def run():
        async with client() as api:
            response = await api.patch(route(session), json={'final_vi': 'Lời thoại mới'})
            assert response.status_code == 503
            detail = response.json()['detail']
            assert 'Mã lỗi:' in detail
            attempt_id = detail.split('Mã lỗi: ')[1].rstrip('.')
            assert 'attempt_id=' + attempt_id in caplog.text
    asyncio.run(run())
    assert 'TRANSCRIPT_EDIT_FAILED run_id=transcript-test segment_id=0' in caplog.text
    assert 'error_type=RuntimeError' in caplog.text and 'trace=' in caplog.text
    assert 'private-test-key' not in caplog.text
    assert session.segments[0].to_dict() == original
    assert old_audio.read_bytes() == b'old audio'


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
            assert saved["tts_duration"] == 3.3 and saved["speed_ratio"] == 1.1
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
            assert audio.status_code == 200 and audio.content == (draft["final_vi"] + " aligned").encode()
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


@pytest.mark.parametrize("failed_stage", ["TTS", "ALIGNING"])
def test_retry_synthesis_preserves_ready_audio_and_translation_and_resumes_waiting_rows(session, failed_stage):
    ready = session.segments[0]
    before = ready.to_dict()
    original_audio = Path(ready.audio_path).read_bytes()
    session.total_processed_duration = ready.duration
    session.visual_translation = True
    session.source_video_url = "/api/inputs/original.mp4"
    for index in (1, 2):
        segment = SegmentItem(index, 2 + 3 * index, 5 + 3 * index, 3)
        segment.source_method = "text-ai"
        segment.final_vi = f"Bản nháp câu {index}"
        segment.text_zh = f"原文{index}"
        segment.needs_review = True
        segment.review_reason = "Từ nguồn chưa rõ"
        segment.translation_provider = "opencode"
        segment.translation_model = "muse-spark-1.3-contributor-free"
        segment.evidence_mode = "asr-ocr-text"
        session.segments[index] = segment
    session.asr_engine = Mock()
    session.translator = Mock()
    target = session.tts_engine.synthesize if failed_stage == "TTS" else session.aligner.apply_atempo
    normal_call = target.side_effect
    attempts = 0

    def fail_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("Lỗi mạng tạm thời")
        return normal_call(*args, **kwargs)

    target.side_effect = fail_once

    async def run():
        session.is_running = True
        for index in (1, 2):
            session.queue.put_nowait((session.segments[index].start, index))
        session.worker_task = asyncio.create_task(session._worker_loop())
        await session.worker_task
        assert session.segments[1].failed_stage == failed_stage
        assert session.segments[1].status == "FAILED" and session.segments[2].status == "WAITING"
        assert session.can_retry
        async with client() as api:
            snapshot = (await api.get(f"/api/streaming/{session.task_id}")).json()
            assert snapshot["initialized"] and snapshot["video_url"] == session.source_video_url
            assert snapshot["progress"]["can_retry"] and snapshot["telemetry"]["can_retry"]
            assert snapshot["segments"][1]["failed_stage"] == failed_stage
            tasks = (await api.get("/api/tasks")).json()["tasks"]
            assert tasks[0]["can_retry"] and tasks[0]["status"] == "FAILED"
            results = await asyncio.gather(*[
                api.post(f"/api/streaming/{session.task_id}/retry") for _ in range(2)])
            assert sorted(response.status_code for response in results) == [200, 409]
            accepted = next(response.json() for response in results if response.status_code == 200)
            assert accepted["status"] == "retrying" and accepted["progress"]["status"] == "RUNNING"
            await session.worker_task
        assert session.error is None and not session.can_retry
        assert session.total_processed_duration == 9
        assert session.queue.empty()
        assert ready.to_dict() == before and Path(ready.audio_path).read_bytes() == original_audio
        for segment in list(session.segments.values())[1:]:
            assert segment.status == "READY" and segment.failed_stage is None
            assert segment.needs_review and segment.review_reason == "Từ nguồn chưa rõ"
            assert segment.translation_provider == "opencode"
            assert segment.translation_model == "muse-spark-1.3-contributor-free"
            assert segment.evidence_mode == "asr-ocr-text"
            assert Path(segment.audio_path).read_bytes() == f"Bản nháp câu {segment.id} aligned".encode()
        session.asr_engine.transcribe.assert_not_called()
        session.translator.translate_single_segment.assert_not_called()
        assert session.get_progress()["status"] == "COMPLETED"

    asyncio.run(run())
    assert attempts == 3


@pytest.mark.parametrize("blocked", ["ASR", "TRANSLATING", "active", "stopped", "uninitialized", "editing", "export"])
def test_retry_refuses_non_synthesis_failures_and_conflicting_lifecycle(session, blocked):
    segment = session.segments[0]
    segment.status = "FAILED"
    segment.failed_stage = blocked if blocked in {"ASR", "TRANSLATING"} else "TTS"
    segment.error = session.error = "Lỗi cần xử lý"
    session.is_running = blocked == "active"
    session.is_stopped = blocked == "stopped"
    session.initialized = blocked != "uninitialized"
    if blocked == "export":
        main.active_export_tasks[f"export_{session.task_id}"] = {"status": "RUNNING"}

    async def run():
        if blocked == "editing":
            session.edit_tasks.add(asyncio.current_task())
        before = segment.to_dict()
        async with client() as api:
            response = await api.post(f"/api/streaming/{session.task_id}/retry")
            assert response.status_code == 409
            assert (await api.post("/api/streaming/not-found/retry")).status_code == 404
            assert (await api.get("/api/streaming/not-found")).status_code == 404
        assert segment.to_dict() == before and session.error == "Lỗi cần xử lý"
        assert session.worker_task is None
        session.edit_tasks.clear()

    asyncio.run(run())
    session.tts_engine.synthesize.assert_not_called()


def test_retry_audio_translation_does_not_translate_failed_sentence_again(session):
    segment = session.segments[0]
    segment.status, segment.failed_stage = "FAILED", "TTS"
    segment.error = session.error = "Tạm lỗi tạo giọng"
    session.asr_engine = Mock()
    session.translator = Mock()

    async def run():
        await session.retry_failed_synthesis()
        await session.worker_task
        assert segment.status == "READY" and segment.final_vi == "Lời thoại cũ"
        assert session.error is None and segment.failed_stage is None

    asyncio.run(run())
    session.asr_engine.transcribe.assert_not_called()
    session.translator.translate_single_segment.assert_not_called()
    session.tts_engine.synthesize.assert_called_once()


def test_retry_keeps_raw_audio_only_while_remaining_legacy_asr_needs_it(session):
    failed = session.segments[0]
    failed.status, failed.failed_stage = "FAILED", "TTS"
    session.error = "Tạm lỗi tạo giọng"
    session.segments[1] = SegmentItem(1, 5, 8, 3)
    session.raw_audio_16k = session.cache_dir / "raw_audio_16k.wav"
    session.raw_audio_16k.write_bytes(b"retained source audio")
    session._release_runtime()
    assert session.raw_audio_16k.exists()
    session.segments[1].asr_pretranscribed = True
    session._release_runtime()
    assert not session.raw_audio_16k.exists()


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
            edited = await api.patch(route(session), json={"final_vi": "Bản xuất"})
            assert edited.status_code == 200, edited.text
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


@pytest.mark.parametrize("changed, unresolved", [(False, False), (True, False), (True, True)])
def test_automatic_review_only_regenerates_changed_audio_and_preserves_uncertainty(session, monkeypatch, changed, unresolved):
    from core.translation_review import AutomaticTranslationReviewer
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    segment = session.segments[0]
    segment.needs_review = True
    row = dict(segment.to_dict(), final_vi="Lời đã kiểm tra" if changed else segment.final_vi,
               needs_review=unresolved, review_reason="Nguồn bị cắt" if unresolved else None,
               verification={"status": "unresolved" if unresolved else "corrected" if changed else "verified"})
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", lambda *a, **kw: {
        "segments": {0: row}, "summary": {"checked": 1, "corrected": int(changed and not unresolved), "unresolved": int(unresolved)}})
    async def run():
        async with client() as api:
            response = await api.post(f"/api/streaming/{session.task_id}/review")
            assert response.status_code == 200 and response.json()["status"] == "reviewing"
            task = session.review_task
            assert task is not None
            await task
            assert session.get_progress()["can_review"]
            assert session.get_progress()["status"] == "COMPLETED"
    asyncio.run(run())
    assert segment.final_vi == row["final_vi"]
    assert segment.needs_review == unresolved
    assert segment.verification == row["verification"]
    assert session.tts_engine.synthesize.call_count == int(changed)
    assert segment.revision == int(changed)
    assert not session.is_running and not session.is_editing


@pytest.mark.parametrize("unresolved", [False, True])
def test_automatic_review_fits_revised_source_and_publishes_only_verified_spoken_text(session, monkeypatch, unresolved):
    from core.translation_review import AutomaticTranslationReviewer
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    segment = session.segments[0]
    long_text, shorter = "Lời thuyết minh rất dài cần rút gọn nhưng giữ nghĩa.", "Lời đọc gọn đủ nghĩa."
    row = dict(segment.to_dict(), text_zh="已经校正的原文", final_vi=long_text,
        needs_review=unresolved, review_reason="Nguồn chưa chắc" if unresolved else None,
        verification={"status": "unresolved" if unresolved else "verified", "semantic_verified": True})
    untouched_row = deepcopy(row)
    proof = {"status": "verified", "text": shorter, "provider": "opencode", "reason": "Giữ đủ ý nguồn."}
    session.translator = Mock()
    session.translator.rewrite_for_pacing.return_value = {"final_vi": shorter, "pacing_verification": proof}
    session.aligner.get_audio_duration.side_effect = lambda path: 4.2 if Path(path).read_text(encoding="utf-8") == long_text else 2.7
    previous = SegmentItem(1, 0, 1.5, 1.5)
    previous.status, previous.text_zh, previous.final_vi = "READY", "前句", "Câu trước."
    session.segments[1] = previous
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", lambda *args, **kwargs: {
        "segments": {0: row}, "summary": {"checked": 1, "verified": int(not unresolved),
                                             "corrected": 0, "unresolved": int(unresolved)}})

    async def run():
        await session.start_automatic_review()
        await session.review_task
    asyncio.run(run())

    args = session.translator.rewrite_for_pacing.call_args
    assert args.args[0] == row["text_zh"] and args.args[1] == long_text
    context = args.args[3]
    assert [entry["id"] for entry in context] == [1, 0]
    assert context[0]["zh"] == "前句" and context[0]["vi"] == "Câu trước."
    assert context[1]["zh"] == row["text_zh"] and context[1]["vi"] == long_text
    assert context[1]["start"] == segment.start and context[1]["end"] == segment.end
    assert args.kwargs["measured_duration"] == 4.2
    assert session.tts_engine.synthesize.call_count == 2
    assert segment.final_vi == shorter and segment.text_zh == row["text_zh"]
    assert Path(segment.audio_path).read_bytes() == (shorter + " aligned").encode()
    assert " ".join(cue["text"] for cue in segment.subtitle_cues) == shorter
    assert segment.verification["pacing"] == proof
    assert segment.verification["before_pacing"] == long_text
    assert segment.verification["translation_changed"] is True
    assert segment.needs_review is unresolved
    assert segment.verification["status"] == ("unresolved" if unresolved else "corrected")
    assert segment.revision == 1 and segment.audio_url.endswith("?rev=1")
    assert session.rolling_context == [{"zh": row["text_zh"], "vi": shorter}]
    assert session.review_summary["verified"] == 0
    assert session.review_summary["corrected"] == int(not unresolved)
    assert session.review_summary["unresolved"] == int(unresolved)
    completed = next(data for kind, data in session.events if kind == "review_complete")
    assert completed["review_summary"] == session.review_summary
    assert row == untouched_row, "Do not mutate the reviewer's draft/proof before audio is committed"
    assert not list(session.segments_dir.glob("edit_*"))


@pytest.mark.parametrize("fail_first", [False, True])
def test_review_audio_uses_complete_reviewed_exchange_before_future_rows_commit(session, monkeypatch, fail_first):
    from core.translation_review import AutomaticTranslationReviewer
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    first = session.segments[0]
    later = SegmentItem(1, 6, 9, 3)
    later.status, later.text_zh, later.final_vi = "READY", "错误识别", "Câu sau cũ."
    session.segments[1] = later
    rows = {
        0: dict(first.to_dict(), text_zh="这话应该我来问吧", final_vi="Bản rà câu đầu.",
                verification={"status": "corrected"}),
        1: dict(later.to_dict(), text_zh="姐你听我说", final_vi="Chị nghe em nói này.",
                verification={"status": "corrected"}),
    }
    untouched = deepcopy(rows)
    before = {sid: deepcopy(seg.to_dict()) for sid, seg in session.segments.items()}
    received = []

    def synthesis(*, text, source, output_path, context, **kwargs):
        received.append(deepcopy(context))
        if len(received) == 1:
            # Pacing sees the reviewed future source, but the visible future
            # row must stay unchanged until its own audio transaction succeeds.
            assert later.to_dict() == before[1]
            future = next(row for row in context if row["id"] == 1)
            assert future["text_zh"] == rows[1]["text_zh"]
            assert future["final_vi"] == rows[1]["final_vi"]
            if fail_first:
                raise RuntimeError("isolated speech failure")
        Path(output_path).write_bytes(text.encode())
        return {"text": text, "tts_duration": 3.0, "speed_ratio": 1.0, "boundaries": []}

    monkeypatch.setattr("core.streaming.pipeline.synthesize_natural_speech", synthesis)
    monkeypatch.setattr("core.streaming.pipeline.build_speech_timing", lambda *args: {
        "subtitle_cues": [], "subtitle_timing_source": "test", "speech_start": 0, "speech_end": 3})
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", lambda *args, **kwargs: {
        "segments": rows, "summary": {"checked": 2, "verified": 0, "corrected": 2, "unresolved": 0}})

    async def run():
        await session.start_automatic_review()
        await session.review_task
    asyncio.run(run())

    assert rows == untouched
    assert [row["id"] for row in received[0]] == [0, 1]
    if fail_first:
        assert session.review_summary["status"] == "failed"
        assert {sid: seg.to_dict() for sid, seg in session.segments.items()} == before
        assert len(received) == 1
    else:
        assert session.review_summary["status"] == "completed"
        # The reviewed exchange is stable, while the focused ID is allowed to
        # move with the sentence currently being synthesized.
        normalize = lambda context: [
            {key: value for key, value in row.items() if key != "is_focus"}
            for row in context
        ]
        assert normalize(received[0]) == normalize(received[1]), "Each row must use one stable reviewed dialogue snapshot"
        assert next(row.get("is_focus") for row in received[0] if row["id"] == 0) is True
        assert next(row.get("is_focus") for row in received[1] if row["id"] == 1) is True
        assert first.text_zh == rows[0]["text_zh"] and later.text_zh == rows[1]["text_zh"]
        assert first.final_vi == rows[0]["final_vi"] and later.final_vi == rows[1]["final_vi"]
    assert not list(session.segments_dir.glob("edit_*"))


@pytest.mark.parametrize("failure", ["pacing", "timing", "publication"])
def test_review_pacing_failure_rolls_back_text_source_audio_proof_context_and_output(session, monkeypatch, failure):
    long_text, shorter = "Bản kiểm tra quá dài để đọc tự nhiên.", "Bản ngắn đủ nghĩa."
    row = dict(session.segments[0].to_dict(), text_zh="修正来源", final_vi=long_text,
               verification={"status": "verified"}, needs_review=False)
    before_row = deepcopy(row)
    session.review_summary = {"status": "completed", "checked": 1, "verified": 1}
    before_summary = dict(session.review_summary)
    before_segment = deepcopy(session.segments[0].to_dict())
    before_context = deepcopy(session.rolling_context)
    audio_path = Path(session.segments[0].audio_path)
    before_audio = audio_path.read_bytes()
    session.output_video_url, session.output_filename = "/api/outputs/saved.mp4", "saved.mp4"
    session.translator = Mock()
    session.translator.rewrite_for_pacing.return_value = {"final_vi": shorter,
        "pacing_verification": {"status": "verified", "text": shorter}}
    session.aligner.get_audio_duration.side_effect = lambda path: 4.2 if Path(path).read_text(encoding="utf-8") == long_text else 2.7
    if failure == "pacing":
        session.translator.rewrite_for_pacing.side_effect = RuntimeError("pacing service unavailable")
    elif failure == "timing":
        monkeypatch.setattr("core.streaming.pipeline.build_speech_timing", Mock(side_effect=RuntimeError("timing failed")))
    else:
        replace = Path.replace
        def fail_publish(path, target):
            if Path(target) == audio_path:
                raise RuntimeError("publication failed")
            return replace(path, target)
        monkeypatch.setattr(Path, "replace", fail_publish)
    with pytest.raises(RuntimeError):
        asyncio.run(session.edit_segment(0, long_text, _review_result=row))
    assert session.segments[0].to_dict() == before_segment
    assert audio_path.read_bytes() == before_audio
    assert session.rolling_context == before_context
    assert session.review_summary == before_summary
    assert session.output_video_url == "/api/outputs/saved.mp4"
    assert session.output_filename == "saved.mp4"
    assert row == before_row and not session.edit_tasks
    assert not list(session.segments_dir.glob("edit_*"))
    assert not any(kind in ("segment_update", "result_invalidated") for kind, _ in session.events)


def test_manual_edit_never_rewrites_user_words_to_satisfy_timing(session):
    from core.engines.alignment.timing_aligner import SpeechBudgetError
    session.translator = Mock()
    session.aligner.get_audio_duration.return_value = 4.2
    original = deepcopy(session.segments[0].to_dict())
    with pytest.raises(SpeechBudgetError):
        asyncio.run(session.edit_segment(0, "Giữ nguyên chính xác lời tôi nhập."))
    session.translator.rewrite_for_pacing.assert_not_called()
    assert session.tts_engine.synthesize.call_args.kwargs["text"] == "Giữ nguyên chính xác lời tôi nhập."
    assert session.segments[0].to_dict() == original
    assert Path(session.segments[0].audio_path).read_bytes() == b"old audio"


def test_review_progress_tracks_speech_stages_until_timing_commits_and_clears_only_resolved_warning(session, monkeypatch):
    from core.translation_review import AutomaticTranslationReviewer
    from core.streaming.pipeline import REVIEW_FAILURE_WARNINGS, build_speech_timing
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    old_warnings = list(REVIEW_FAILURE_WARNINGS)
    unrelated = "Không lưu được tiến độ nhận diện xuống đĩa; giữ trong phiên hiện tại."
    session.warnings = [*old_warnings, unrelated]
    long_text, shorter = "Lời dài cần viết gọn để giọng đọc giữ nhịp tự nhiên.", "Lời gọn đủ nghĩa."
    callback = {}
    def review(*args, progress_callback, **kwargs):
        callback["semantic"] = progress_callback
        progress_callback(100)
        return {"segments": {0: dict(session.segments[0].to_dict(), final_vi=long_text,
                                     verification={"status": "verified"})},
                "summary": {"checked": 1, "verified": 1}}
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", review)
    session.aligner.get_audio_duration.side_effect = lambda path: 4.2 if Path(path).read_text(encoding="utf-8") == long_text else 2.7
    def rewrite(*args, **kwargs):
        # A delayed callback from the finished semantic stage must not replace
        # the live TTS/pacing stage or reintroduce 100%.
        callback["semantic"](100)
        return {"final_vi": shorter, "pacing_verification": {"status": "verified", "text": shorter}}
    session.translator = SimpleNamespace(rewrite_for_pacing=rewrite)
    entered, release = threading.Event(), threading.Event()
    def measured_timing(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return build_speech_timing(*args, **kwargs)
    monkeypatch.setattr("core.streaming.pipeline.build_speech_timing", measured_timing)

    async def run():
        await session.start_automatic_review()
        task = session.review_task
        await wait_until(entered.is_set)
        try:
            await asyncio.sleep(.02)
            state = session.get_progress()
            assert state["status"] == "RUNNING" and state["phase"] == "review"
            assert state["review_stage"] == "aligning" and state["progress_pct"] is None
            assert session.segments[0].final_vi == "Lời thoại cũ"
            assert Path(session.segments[0].audio_path).read_bytes() == b"old audio"
            states = [data for kind, data in session.events if kind == "progress"]
            assert {"tts", "rewriting", "aligning"}.issubset({state.get("review_stage") for state in states})
            assert not any(state.get("progress_pct") == 100 for state in states)
            assert all(warning in session.warnings for warning in old_warnings)
        finally:
            release.set()
            await task
    asyncio.run(run())
    assert session.get_progress()["status"] == "COMPLETED"
    assert session.get_progress()["progress_pct"] == 100
    assert session.segments[0].final_vi == shorter
    assert session.warnings == [unrelated]
    assert session.get_telemetry()["warnings"] == [unrelated]
    review_complete = next(data for kind, data in session.events if kind == "review_complete")
    assert review_complete["warnings"] == [unrelated]


def test_failed_review_retry_keeps_warning_without_duplicates_and_never_reports_100_percent(session, monkeypatch):
    from core.translation_review import AutomaticTranslationReviewer
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    warning = "AI kiểm tra lại chưa hoàn tất. Các câu đã lưu và âm thanh sẵn có được giữ; có thể thử lại."
    session.warnings = [warning, "Giữ cảnh báo khác."]
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", Mock(side_effect=RuntimeError("offline failure")))
    async def run():
        for _ in range(2):
            await session.start_automatic_review()
            await session.review_task
            assert session.get_progress()["status"] == "FAILED"
            assert session.get_progress()["progress_pct"] is None
    asyncio.run(run())
    assert session.warnings == [warning, "Giữ cảnh báo khác."]
    assert not any(data.get("progress_pct") == 100 for kind, data in session.events if kind == "progress")


def test_review_removes_old_placeholder_audio_without_claiming_source_silence(session, monkeypatch):
    from core.translation_review import AutomaticTranslationReviewer
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    segment = session.segments[0]
    segment.final_vi = "nghe chưa rõ"
    segment.needs_review = True
    segment.subtitle_cues = [{"start": 2, "end": 5, "text": segment.final_vi}]
    old_audio = Path(segment.audio_path)
    session.visual_translation = True
    session.screen_texts = [{"start": 2, "end": 5, "kind": "subtitle", "text_vi": "Chữ nguồn",
                             "bbox": [.1, .8, .8, .1]}]
    screens_before = [dict(row) for row in session.screen_texts]
    row = dict(segment.to_dict(), final_vi="", literal_vi="", natural_vi="", needs_review=True,
               review_reason="Nguồn chưa đủ rõ", verification={"status": "unresolved"})
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", lambda *a, **kw: {
        "segments": {0: row}, "summary": {"checked": 1, "unresolved": 1}})

    async def run():
        await session.start_automatic_review()
        await session.review_task
        async with client() as api:
            assert (await api.get("/api/streaming/audio/transcript-test/0")).status_code == 404
            assert (await api.patch(route(session), json={"final_vi": ""})).status_code == 422

    asyncio.run(run())
    assert not old_audio.exists()
    assert segment.final_vi == "" and segment.text_zh == "原文"
    assert segment.needs_review and not segment.confirmed_silence
    assert segment.verification == {"status": "unresolved"}
    assert segment.audio_path is None and segment.audio_url is None
    assert segment.subtitle_cues == [] and segment.subtitle_timing_source == "unresolved"
    assert segment.speech_start is None and segment.speech_end is None
    assert segment.revision == 1 and session.screen_texts == screens_before
    session.tts_engine.synthesize.assert_not_called()


def test_automatic_review_reserves_task_blocks_edit_export_and_keeps_old_media_on_failure(session, monkeypatch):
    from core.translation_review import AutomaticTranslationReviewer
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    entered, release = threading.Event(), threading.Event()
    def review(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        raise RuntimeError("test service failure")
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", review)
    segment = session.segments[0]
    segment.needs_review = True
    async def run():
        async with client() as api:
            assert (await api.post(f"/api/streaming/{session.task_id}/review")).status_code == 200
            task = session.review_task
            await wait_until(entered.is_set)
            try:
                assert (await api.post(f"/api/streaming/{session.task_id}/review")).status_code == 409
                assert (await api.patch(route(session), json={"final_vi": "Ghi đè"})).status_code == 409
                assert (await api.post('/api/streaming/export-hq', json={"task_id": session.task_id})).status_code == 409
            finally:
                release.set()
                await task
    asyncio.run(run())
    assert segment.final_vi == "Lời thoại cũ" and segment.needs_review
    assert Path(segment.audio_path).read_bytes() == b"old audio"
    assert session.review_summary["status"] == "failed"
    assert session.get_progress()["can_review"]


def test_saved_edit_invalidates_previous_result_without_deleting_file_or_claiming_ai_verified_text(session):
    final_file = session.cache_dir / "previous-final.mp4"
    final_file.write_bytes(b"previous final")
    session.output_video_url, session.output_filename = "/api/outputs/previous-final.mp4", final_file.name
    session.auto_export_signature = "old revision"
    session.segments[0].verification = {"status": "verified"}
    session.review_summary = {"status": "completed", "checked": 1, "verified": 1, "corrected": 0, "unresolved": 0}

    asyncio.run(session.edit_segment(0, "Lời tôi đã sửa"))

    assert session.output_video_url == session.output_filename == ""
    assert session.auto_export_signature is None
    assert final_file.read_bytes() == b"previous final"
    assert session.review_summary == {"status": "completed", "checked": 0, "verified": 0, "corrected": 0, "unresolved": 0, "manual": 1}
    invalidated = next(data for event, data in session.events if event == "result_invalidated")
    assert invalidated["reason"] == "transcript_changed"
    assert invalidated["output_video_url"] == ""
    assert session.events[-1][0] == "segment_update"


def test_failed_edit_preserves_current_result_reference(session):
    session.output_video_url, session.output_filename = "/api/outputs/current.mp4", "current.mp4"
    session.auto_export_signature = "current revision"
    session.tts_engine.synthesize.side_effect = RuntimeError("test TTS failure")
    with pytest.raises(RuntimeError):
        asyncio.run(session.edit_segment(0, "Chưa lưu được"))
    assert session.output_video_url == "/api/outputs/current.mp4"
    assert session.auto_export_signature == "current revision"
    assert not any(event == "result_invalidated" for event, _ in session.events)


def test_incomplete_second_review_is_not_completed_and_blocks_export(session, monkeypatch):
    from core.translation_review import AutomaticTranslationReviewer
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    row = dict(session.segments[0].to_dict(), needs_review=True,
               verification={"status": "incomplete"}, review_reason="Second pass unavailable")
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", lambda *a, **kw: {
        "segments": {0: row}, "summary": {"status": "incomplete", "checked": 1, "incomplete": 1}})

    async def run():
        await session.start_automatic_review()
        await session.review_task
        progress = session.get_progress()
        assert progress["review_summary"]["status"] == "incomplete"
        assert progress["status"] == "FAILED" and progress["can_review"]
        async with client() as api:
            result = await api.post('/api/streaming/export-hq', json={"task_id": session.task_id})
            assert result.status_code == 409
    asyncio.run(run())


def test_rechecking_invalidates_previous_result_even_when_service_fails(session, monkeypatch):
    from core.translation_review import AutomaticTranslationReviewer
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    session.output_video_url, session.output_filename = "/api/outputs/old.mp4", "old.mp4"
    session.auto_export_signature = "old revision"
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", Mock(side_effect=RuntimeError("test unavailable")))

    async def run():
        await session.start_automatic_review()
        assert session.output_video_url == session.output_filename == ""
        assert session.auto_export_signature is None
        await session.review_task

    asyncio.run(run())
    assert session.review_summary["status"] == "failed"
    assert session.output_video_url == ""
    assert session.events[0][0] == "result_invalidated"
    assert session.events[0][1]["reason"] == "review_started"


def test_chunked_automatic_review_speech_failure_keeps_proof_but_never_completed(session, monkeypatch):
    from core.translation_review import AutomaticTranslationReviewer
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    session._chunked_source_started = True
    session._source_prepared_seconds = session._visual_completed_seconds = session.total_duration
    session._visual_prepass_complete = True
    row = {**session.segments[0].to_dict(), "final_vi": "Lời đã kiểm tra.", "needs_review": False,
           "verification": {"status": "corrected", "semantic_verified": True}}
    monkeypatch.setattr(AutomaticTranslationReviewer, "review", lambda *args, **kwargs: {
        "segments": {0: row}, "summary": {"checked": 1, "corrected": 1}})
    session.tts_engine.synthesize.side_effect = RuntimeError("Offline speech failure after accepted review")

    async def run():
        await session.start_automatic_review()
        await session.review_task
    asyncio.run(run())

    assert session.segments[0].status == "FAILED"
    assert session.segments[0].verification["semantic_verified"] is True
    assert session.segments[0].audio_path is None
    assert session.get_progress()["status"] == "FAILED"
    assert session.get_progress()["progress_pct"] is None
    assert session.can_retry
    assert not any(data.get("status") == "COMPLETED" for kind, data in session.events if kind == "progress")
