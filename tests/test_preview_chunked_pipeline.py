"""Regression contracts for bounded preparation, promotion and edit ownership.

Providers are isolated here. Production/provider acceptance is recorded
separately by the real UI harness, never inferred from these tests.
"""
import asyncio
import threading
import subprocess
import json
import wave
from copy import deepcopy
from pathlib import Path

import pytest

from config import settings
from core.streaming.chunked_source import owned_boundary, prepare_interval, publish_background_prefix as real_background_prefix
from core.streaming.pipeline import SegmentEditConflict, SegmentItem, StreamingPipelineSession


def wav(path, seconds=.5):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\0\0" * round(seconds * 16000))


@pytest.fixture
def preview(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    monkeypatch.setattr(settings, "DIARIZATION_ENABLED", False)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"isolated source identity")
    session = StreamingPipelineSession("preview-owned", source, visual_translation=True,
                                       translation_mode="preview", tts_engine_name="edge-tts")
    session.video_size = (640, 360)
    session.total_duration = 64
    prepared, analyzed, speech, events = [], [], [], []

    async def prepare(current, start, end):
        assert current is session
        prepared.append((start, end))
        stop = min(current.total_duration, end + 8)
        raw = current.cache_dir / f"source_{start:.0f}.wav"
        wav(raw)
        return {"start": start, "end": stop,
                "rows": [{"start": value, "end": value + 3, "duration": 3,
                          "text_zh": f"源{value}"} for value in range(round(start), round(stop), 8)],
                "raw_audio": {"path": str(raw)}, "suppression_stats": {}}

    async def background(current, record):
        path = current.cache_dir / f"bgm_{record['end']}.ogg"
        path.write_bytes(b"isolated ready background")
        return path

    def prepass(path, rows, **options):
        assert options["total_duration"] == session.total_duration
        analyzed.append((options["start_time"], options["end_time"], [row.id for row in rows],
                         deepcopy(options["context_segments"])))
        result = {"segments": {row.id: {"id": row.id, "start": row.start, "end": row.end,
                  "text_zh": row.text_zh, "literal_vi": f"Lời {row.id}.", "natural_vi": f"Lời {row.id}.",
                  "final_vi": f"Lời {row.id}.", "needs_review": False, "review_reason": "",
                  "translation_provider": "opencode", "translation_model": "isolated-provider"} for row in rows},
                  "screen_texts": [], "translation_sources": [], "summary": ""}
        options["chunk_callback"](result, options["start_time"], options["end_time"])
        return result

    async def review(**options):
        for sid in options["segment_ids"]:
            session.segments[sid].verification = {"status": "verified", "semantic_verified": True}
            session.segments[sid].needs_review = False
        session.review_summary = {"status": "completed"}

    async def synthesize(item):
        speech.append(item.id)
        path = session.segments_dir / f"seg_{item.id}.wav"
        wav(path)
        item.audio_path = str(path)
        item.audio_url = f"/audio/{item.id}?rev={item.revision}"
        item.status = "READY"
        await session.emit("segment_update", item.to_dict())
        await session._update_ready()

    monkeypatch.setattr("core.streaming.chunked_source.prepare_interval", prepare)
    monkeypatch.setattr("core.streaming.chunked_source.publish_background_prefix", background)
    monkeypatch.setattr(session.video_intelligence, "prepass", prepass)
    monkeypatch.setattr(session, "_review_translations", review)
    monkeypatch.setattr(session, "_synthesize_segment", synthesize)
    session.event_callback = lambda event, payload: events.append((event, deepcopy(payload)))
    return session, prepared, analyzed, speech, events


def test_hours_source_preview_only_prepares_one_bounded_window(preview):
    session, prepared, analyzed, speech, events = preview
    session.total_duration = 7200
    async def run():
        await session.start()
        await session.worker_task
        state = session.get_progress()
        assert state["status"] == "PREVIEW_READY"
        assert state["can_translate_full"] is True
        assert state["total_seconds"] == 7200
        assert state["processed_seconds"] == 24
        assert session.playable_until == 24
        assert session._visual_prepass_complete is False
    asyncio.run(run())
    assert prepared == [(0, 24)]
    assert [(start, end) for start, end, ids, context in analyzed] == [(0, 24)]
    assert speech == [0, 1, 2]
    assert session.segments[3].status == "WAITING"
    assert not any(event == "progress" and payload["status"] == "COMPLETED" for event, payload in events)


def test_provider_hole_does_not_block_later_intervals_and_resume_reuses_success(preview, monkeypatch):
    session, prepared, analyzed, speech, _ = preview
    session.translation_mode = "full"
    session._chunked_source_started = True
    original = session.video_intelligence.prepass
    requests = []
    broken = True
    def prepass(path, rows, **options):
        requests.append(options["start_time"])
        if broken and options["start_time"] == 0:
            raise TimeoutError("injected provider timeout")
        return original(path, rows, **options)
    monkeypatch.setattr(session.video_intelligence, "prepass", prepass)
    async def run():
        nonlocal broken
        await session.start()
        await session.worker_task
        assert requests == [0, 24, 48, 0]
        assert session._visual_scanned_seconds == 64 and session._visual_completed_seconds == 0
        assert session.get_progress()["retry_chunks"] == 1
        assert session.get_progress()["status"] == "FAILED" and session.can_retry
        assert all(session.segments[sid].status == "READY" for sid in range(3, 8))
        healthy = {sid: Path(session.segments[sid].audio_path).read_bytes() for sid in range(3, 8)}
        broken = False
        await session.retry_failed_synthesis()
        await session.start_task
        await session.worker_task
        assert requests == [0, 24, 48, 0, 0], "Completed intervals must not call the provider again"
        assert session._visual_completed_seconds == 64
        assert all(row.status == "READY" for row in session.segments.values())
        assert all(Path(session.segments[sid].audio_path).read_bytes() == data for sid, data in healthy.items())
    asyncio.run(run())


def test_new_full_visual_request_uses_bounded_source_pipeline(preview):
    session, prepared, analyzed, speech, _ = preview
    session.translation_mode = "full"
    assert not session._chunked_source_started
    async def run():
        await session.start()
        await session.worker_task
    asyncio.run(run())
    assert session._chunked_source_started and session._visual_prepass_complete
    assert prepared == [(0, 24), (32, 56)]
    assert [(start, end) for start, end, *_ in analyzed] == [(0, 24), (24, 48), (48, 64)]
    assert all(row.status == "READY" for row in session.segments.values())


def test_stop_keeps_one_checkpoint_owner_until_followup_drains(preview):
    from core.streaming.pipeline import active_streaming_sessions
    session, *_ = preview
    entered, release = threading.Event(), threading.Event()
    def native():
        entered.set()
        assert release.wait(5)
    async def run():
        active_streaming_sessions[session.task_id] = session
        async def consumer():
            try:
                await session._run_blocking(native)
            finally:
                session._release_runtime()
        session._chunk_followup_task = asyncio.create_task(consumer())
        while not entered.is_set():
            await asyncio.sleep(.01)
        try:
            session.stop()
            await asyncio.sleep(.03)
            assert active_streaming_sessions[session.task_id] is session
            assert not session._chunk_followup_task.done()
        finally:
            release.set()
            await asyncio.gather(session._chunk_followup_task, return_exceptions=True)
        assert session.task_id not in active_streaming_sessions
    asyncio.run(run())


def test_provider_result_without_published_output_is_not_completed(preview, monkeypatch):
    session, *_ = preview
    def invisible(path, rows, **options):
        return {"segments": {row.id: {} for row in rows}}
    monkeypatch.setattr(session.video_intelligence, "prepass", invisible)
    async def run():
        await session.start()
        await session.worker_task
        assert session._visual_completed_seconds == 0
        assert session.get_progress()["status"] == "FAILED"
        assert session._chunk_jobs[0]["state"] == "RETRY_PENDING"
        assert session._chunk_jobs[0]["attempts"] == 2
    asyncio.run(run())


def test_followup_worker_failure_does_not_leave_producer_hung(preview, monkeypatch):
    session, *_ = preview
    async def failed(*args):
        raise RuntimeError("injected worker crash")
    monkeypatch.setattr(session, "_finish_visual_chunk", failed)
    async def run():
        with pytest.raises(RuntimeError, match="injected worker crash"):
            await asyncio.wait_for(session.start(), 5)
        assert session._chunk_followup_task is None
        assert not session.is_running
    asyncio.run(run())


def test_source_memory_failure_drains_admitted_work_and_resumes_exact_tail(preview, monkeypatch):
    import core.streaming.chunked_source as source
    session, prepared, analyzed, speech, events = preview
    session.translation_mode = "full"
    original_prepare = source.prepare_interval
    original_review = session._review_translations
    rejected = []
    blocked = True

    async def prepare(current, start, end):
        if blocked and start == 32:
            rejected.append((start, end))
            raise MemoryError("injected native allocation failure")
        return await original_prepare(current, start, end)

    async def review(**options):
        # The source worker fails while this earlier stage is still admitted.
        # Its healthy output must be drained rather than cancelled.
        await asyncio.sleep(.02)
        await original_review(**options)

    monkeypatch.setattr(source, "prepare_interval", prepare)
    monkeypatch.setattr(session, "_review_translations", review)

    async def run():
        nonlocal blocked
        await session.start()
        await session.worker_task
        assert rejected == [(32, 56)]
        assert prepared == [(0, 24)]
        assert session._source_prepared_seconds == 32
        assert session._visual_completed_seconds == 24
        assert session.get_progress()["status"] == "FAILED" and session.can_retry
        healthy = {row.id: (row.final_vi, Path(row.audio_path).read_bytes())
                   for row in session.segments.values() if row.status == "READY"}
        assert set(healthy) == {0, 1, 2}
        assert any("32.00" in warning for warning in session.warnings)
        assert not any(event == "progress" and payload["status"] == "COMPLETED" for event, payload in events)
        blocked = False
        await session.retry_failed_synthesis()
        await session.start_task
        await session.worker_task
        assert prepared == [(0, 24), (32, 56)]
        assert [lo for lo, *_ in analyzed] == [0, 24, 48], "Do not rerun the successful provider interval"
        assert session._visual_prepass_complete
        assert all(row.status == "READY" for row in session.segments.values())
        assert all((session.segments[sid].final_vi, Path(session.segments[sid].audio_path).read_bytes()) == value
                   for sid, value in healthy.items())
    asyncio.run(run())


def test_full_promotion_keeps_prefix_global_ids_and_manual_audio(preview):
    session, prepared, analyzed, speech, events = preview
    async def run():
        await session.start()
        await session.worker_task
        first = session.segments[0]
        # A committed manual row has the same contract as edit_segment's
        # atomic publication; promotion must not replace it with prepass drafts.
        first.final_vi = "Câu đã sửa bằng tay."
        first.verification = {"status": "manual"}
        first.revision = 1
        first.audio_url += "&manual=1"
        first_audio, first_bytes = first.audio_url, Path(first.audio_path).read_bytes()
        progress = await session.translate_full()
        assert progress["translation_mode"] == "full"
        assert not progress["can_translate_full"]
        with pytest.raises(SegmentEditConflict):
            await session.translate_full()
        await session.start_task
        await session.worker_task
        assert session.get_progress()["status"] == "COMPLETED"
        assert session.playable_until == 64
        assert first.final_vi == "Câu đã sửa bằng tay."
        assert first.verification == {"status": "manual"}
        assert first.audio_url == first_audio
        assert Path(first.audio_path).read_bytes() == first_bytes
    asyncio.run(run())
    assert prepared == [(0, 24), (32, 56)]
    assert [(start, end) for start, end, ids, context in analyzed] == [(0, 24), (24, 48), (48, 64)]
    assert speech == list(range(8))
    assert list(session.segments) == list(range(8))
    assert analyzed[0][3][-1]["id"] == 3, "Recognition lookahead is source context, not a translated future hour"


def test_preview_promotion_is_rejected_while_edit_owns_audio(preview):
    session, *_ = preview
    async def run():
        await session.start()
        await session.worker_task
        session.edit_tasks.add(asyncio.current_task())
        with pytest.raises(SegmentEditConflict):
            await session.translate_full()
        session.edit_tasks.clear()
        assert session.can_translate_full
    asyncio.run(run())


def test_failed_preview_sentence_does_not_block_explicit_remaining_video(preview, monkeypatch):
    session, prepared, analyzed, speech, events = preview
    original = session._synthesize_segment
    async def fail_one(row):
        if row.id == 1:
            row.status = "ALIGNING"
            raise ValueError("isolated unfit speech")
        await original(row)
    monkeypatch.setattr(session, "_synthesize_segment", fail_one)
    async def run():
        await session.start()
        await session.worker_task
        assert session.get_progress()["status"] == "FAILED"
        assert session.can_translate_full and session.can_retry
        assert session.segments[0].status == session.segments[2].status == "READY"
        await session.translate_full()
        await session.start_task
        await session.worker_task
        assert session.get_progress()["status"] == "FAILED", "A gap cannot become full success"
        assert session.segments[1].status == "FAILED"
        assert session._visual_prepass_complete
        assert all(row.status == "READY" for sid, row in session.segments.items() if sid != 1)
    asyncio.run(run())
    assert prepared == [(0, 24), (32, 56)]
    assert speech == [0, 2, 3, 4, 5, 6, 7]


def test_interrupted_preview_does_not_prepare_translate_rest_before_full_click(preview):
    session, prepared, analyzed, speech, events = preview
    async def run():
        await session.start()
        await session.worker_task
        session._preview_ready = False
        session._visual_completed_seconds = 23
        await session.start()
        await session.worker_task
        assert session.get_progress()["status"] == "PREVIEW_READY"
        assert prepared == [(0, 24)], "Resume cannot silently prepare the next full interval"
        # The validated interval ledger supersedes an obsolete prefix cursor.
        # It must reuse the successful checkpoint rather than call AI again.
        assert analyzed[-1][0:2] == (0, 24)
        assert len(analyzed) == 1 and session._visual_completed_seconds == 24
        assert session.segments[3].status == "WAITING"
    asyncio.run(run())


def test_startup_resume_retries_earlier_known_speech_without_discarding_ready_prefix(preview):
    session, prepared, analyzed, speech, events = preview
    async def run():
        await session.start()
        await session.worker_task
        first = session.segments[0]
        first_bytes = Path(first.audio_path).read_bytes()
        missing = session.segments[1]
        Path(missing.audio_path).unlink()
        missing.audio_path = missing.audio_url = None
        missing.status, missing.failed_stage = "FAILED", "TTS"
        session._startup_failed = True
        session.error = "source work interrupted"
        session._preview_ready = False
        await session.retry_failed_synthesis()
        await session.start_task
        await session.worker_task
        assert missing.status == "READY" and missing.final_vi
        assert Path(first.audio_path).read_bytes() == first_bytes
        assert session.get_progress()["status"] == "PREVIEW_READY"
    asyncio.run(run())
    assert prepared == [(0, 24)]
    assert speech == [0, 1, 2, 1]


@pytest.mark.parametrize("startup", [False, True])
def test_retry_reviews_pending_owned_drafts_before_speech_without_replacing_ready_edits(preview, monkeypatch, startup):
    session, prepared, analyzed, speech, events = preview
    session.initialized = session._chunked_source_started = session._prepared = True
    session._source_prepared_seconds = 32
    session._visual_completed_seconds = 24
    session._startup_failed = startup
    session.error = "interrupted independent review"
    saved = SegmentItem(0, 0, 1, 1)
    saved.status, saved.source_method, saved.translation_provider = "READY", "text-ai", "opencode"
    saved.final_vi, saved.revision, saved.verification = "Lời người dùng đã sửa.", 3, {"status": "manual"}
    audio = session.segments_dir / "seg_0.wav"
    wav(audio)
    saved.audio_path, saved.audio_url = str(audio), "/audio/0?rev=3"
    session.segments = {0: saved}
    for sid in range(1, 8):
        row = SegmentItem(sid, sid * 2, sid * 2 + 1, 1)
        row.text_zh = row.asr_text = f"源{sid}"
        row.final_vi = f"Lời {sid}."
        row.status, row.failed_stage = "FAILED", "TTS"
        row.source_method, row.translation_provider = "text-ai", "opencode"
        row.verification = {"status": "verified" if sid == 7 else "incomplete" if sid == 6 else "pending",
                            "semantic_verified": sid == 7}
        row.needs_review = sid != 7
        session.segments[sid] = row
    future = SegmentItem(8, 25, 27, 2)
    future.text_zh = future.asr_text = "还未翻译"
    session.segments[8] = future
    reviewed = []
    original_speech = session._synthesize_segment
    async def review(**options):
        ids = sorted(options["segment_ids"])
        reviewed.append(ids)
        assert len(ids) <= session.VISUAL_REVIEW_GROUP_SIZE
        assert not set(ids).intersection(speech), "A row's own review must finish before its saved speech resumes"
        for sid in ids:
            row = session.segments[sid]
            row.verification = {"status": "unresolved" if sid == 6 else "verified",
                                "semantic_verified": sid != 6}
            row.needs_review = sid == 6
        session.review_summary = {"status": "completed"}
    async def synthesize(row):
        if row.id != 7:
            assert any(row.id in ids for ids in reviewed)
        assert row.verification["status"] != "pending"
        await original_speech(row)
    monkeypatch.setattr(session, "_review_translations", review)
    monkeypatch.setattr(session, "_synthesize_segment", synthesize)
    before = deepcopy(saved.to_dict()), audio.read_bytes()
    async def run():
        await session.retry_failed_synthesis()
        if startup:
            await session.start_task
        await session.worker_task
        assert session.get_progress()["status"] == "PREVIEW_READY"
        assert session.segments[6].needs_review
        assert session.segments[6].verification == {"status": "unresolved", "semantic_verified": False}
    asyncio.run(run())
    assert saved.to_dict() == before[0] and audio.read_bytes() == before[1]
    assert session.segments[8].status == "WAITING" and not session.segments[8].final_vi
    assert prepared == analyzed == []
    assert speech == [7, 1, 2, 3, 4, 5, 6]


def test_retry_review_failure_keeps_incomplete_evidence_instead_of_forcing_verification(preview, monkeypatch):
    session, prepared, analyzed, speech, events = preview
    async def run():
        await session.start()
        await session.worker_task
        row = session.segments[1]
        row.status, row.failed_stage, row.verification = "FAILED", "TTS", {"status": "pending"}
        row.needs_review = True
        session.error = "review interrupted"
        async def failure(**options):
            assert options["segment_ids"] == {1}
            raise TimeoutError("isolated review failure")
        monkeypatch.setattr(session, "_review_translations", failure)
        await session.retry_failed_synthesis()
        await session.worker_task
        assert row.status == "READY", "The saved draft remains playable"
        assert row.needs_review and row.verification["status"] == "incomplete"
        assert row.verification["semantic_verified"] is False
        assert session.review_summary["status"] == "incomplete"
    asyncio.run(run())


def test_semantic_review_stage_uses_indeterminate_progress_with_real_source_coverage(preview, monkeypatch):
    session, *_ , events = preview
    session._visual_completed_seconds = 12
    monkeypatch.setattr(session, "_review_translations", StreamingPipelineSession._review_translations.__get__(session))
    row = SegmentItem(0, 0, 1, 1)
    row.text_zh = "源"
    session.segments = {0: row}
    step_seen = threading.Event()
    def event(kind, payload):
        events.append((kind, deepcopy(payload)))
        if kind == "progress" and payload.get("review_stage") == "semantic":
            step_seen.set()
    session.event_callback = event
    def review(reviewer, path, rows, screens, **options):
        options["progress_callback"](30)
        assert step_seen.wait(2)
        step_seen.clear()
        options["progress_callback"](75)
        assert step_seen.wait(2)
        return {"segments": {0: {"id": 0, "verification": {"status": "verified"}}},
                "summary": {"checked": 1, "verified": 1}}
    monkeypatch.setattr("core.translation_review.AutomaticTranslationReviewer.review", review)
    async def run():
        await session._review_translations(segment_ids={0})
        await asyncio.sleep(0)
    asyncio.run(run())
    snapshots = [payload for event, payload in events if event == "progress" and payload["phase"] == "review"]
    assert sum(snapshot.get("review_stage") == "semantic" for snapshot in snapshots) == 2
    assert all(snapshot["progress_pct"] is None and snapshot["processed_seconds"] == 12 for snapshot in snapshots)


def test_turning_ocr_review_off_still_uses_bounded_preview_then_full(preview, monkeypatch):
    session, prepared, analyzed, speech, events = preview
    session.visual_translation = False
    async def process(row):
        assert row.asr_pretranscribed
        row.final_vi = f"Lời {row.id}."
        await session._synthesize_segment(row)
    monkeypatch.setattr(session, "_process_segment", process)
    async def run():
        await session.start()
        await session.worker_task
        assert session.get_progress()["status"] == "PREVIEW_READY"
        assert prepared == [(0, 24)]
        await session.translate_full()
        await session.start_task
        await session.worker_task
        assert session.get_progress()["status"] == "COMPLETED"
        assert session.playable_until == 64
    asyncio.run(run())
    assert prepared == [(0, 24), (32, 56)]
    assert speech == list(range(8))
    assert analyzed == []


def test_source_boundary_defers_clipped_tail_without_losing_words():
    rows = [{"start": 0, "end": 5}, {"start": 30, "end": 32}]
    assert owned_boundary(rows, 0, 32, 7200) == 30
    assert owned_boundary(rows, 0, 32, 32) == 32
    assert owned_boundary([{ "start": 0, "end": 30}], 0, 32, 7200) == 32
    assert owned_boundary([], 0, 32, 7200) == 32


def test_bounded_preparation_reuses_exact_checkpoint_and_rejects_changed_asset(preview, monkeypatch):
    session, *_ = preview
    calls, extracted = [], []
    async def ffmpeg(command):
        extracted.append(command)
        wav(Path(command[-1]))
    def recognize(path, **options):
        calls.append(path)
        return [{"start": 0, "end": 5, "text_zh": "开头"},
                {"start": 30, "end": 32, "text_zh": "句子还没结束"}]
    def suppress(**options):
        options["output_audio_path"].write_bytes(b"isolated valid checkpoint packets")
        return {"duration": 30}
    monkeypatch.setattr(session, "_run_ffmpeg", ffmpeg)
    monkeypatch.setattr(session.faster_whisper, "transcribe", recognize)
    monkeypatch.setattr(session.vocal_suppressor, "process_file", suppress)
    async def run():
        first = await prepare_interval(session, 0, 24)
        assert first["end"] == 30
        assert [row["text_zh"] for row in first["rows"]] == ["开头"]
        assert all(float(command[command.index("-t") + 1]) <= 32 for command in extracted)
        retained = await prepare_interval(session, 0, 24)
        assert retained == first
        assert len(calls) == 1
        assert len(extracted) == 2
        Path(first["raw_audio"]["path"]).write_bytes(b"corrupt checkpoint asset")
        repaired = await prepare_interval(session, 0, 24)
        assert repaired["end"] == 30
        assert len(calls) == 2
    asyncio.run(run())


def test_global_source_offsets_survive_following_chunk_preparation(preview, monkeypatch):
    session, *_ = preview
    async def ffmpeg(command):
        wav(Path(command[-1]))
    monkeypatch.setattr(session, "_run_ffmpeg", ffmpeg)
    monkeypatch.setattr(session.faster_whisper, "transcribe", lambda *args, **kwargs: [
        {"start": 1, "end": 3, "text_zh": "下一句", "words": [{"word": "下一句", "start": 1, "end": 3}]}])
    def suppress(**options):
        options["output_audio_path"].write_bytes(b"isolated bounded background")
        return {}
    monkeypatch.setattr(session.vocal_suppressor, "process_file", suppress)
    async def run():
        result = await prepare_interval(session, 32, 56)
        assert result["start"] == 32 and result["end"] == 64
        assert result["rows"][0]["start"] == 33
        assert result["rows"][0]["end"] == 35
        assert result["rows"][0]["text_zh"] == "下一句"
    asyncio.run(run())


@pytest.mark.parametrize("new_start,conflict", [(25, False), (.1, True)])
def test_later_source_append_does_not_invalidate_an_earlier_fit(preview, monkeypatch, new_start, conflict):
    session, *_ = preview
    session.total_duration = session._source_prepared_seconds = 40
    session._chunked_source_started = True
    item = SegmentItem(0, 0, 2, 2)
    item.final_vi = "Xin chào."
    session.segments = {0: item}
    path = session.segments_dir / "fit.wav"
    async def run():
        loop = asyncio.get_running_loop()
        done = threading.Event()
        def append():
            session.segments[1] = SegmentItem(1, new_start, new_start + 1, 1)
            done.set()
        def synthesize(**options):
            loop.call_soon_threadsafe(append)
            assert done.wait(2)
            wav(options["output_path"])
            return {"text": options["text"], "tts_duration": .5, "speed_ratio": 1, "boundaries": []}
        monkeypatch.setattr("core.streaming.pipeline.synthesize_natural_speech", synthesize)
        monkeypatch.setattr("core.streaming.pipeline.build_speech_timing", lambda *args: {})
        session._ensure_tts_engine()
        if conflict:
            with pytest.raises(SegmentEditConflict):
                await session._fit_dub(item, text=item.final_vi, source="你好", output_path=path)
        else:
            spoken, timing, plan = await session._fit_dub(item, text=item.final_vi, source="你好", output_path=path)
            assert spoken["text"] == item.final_vi
    asyncio.run(run())


def test_short_sentence_uses_both_bounded_reflow_sides_before_semantic_shortening(preview, monkeypatch):
    session, *_ = preview
    session.total_duration = session._source_prepared_seconds = 8
    session._chunked_source_started = True
    focus = SegmentItem(0, 1, 2.3, 1.3)
    focus.final_vi = "Hộp đồ nghề xanh của bố."
    following = SegmentItem(1, 2.3, 4, 1.7)
    session.segments = {0: focus, 1: following}
    path = session.segments_dir / "fit.wav"
    budgets = []
    def synthesize(**options):
        budgets.append(options["max_duration"])
        wav(options["output_path"], 1.916)
        return {"text": options["text"], "tts_duration": 1.916, "speed_ratio": 1, "boundaries": []}
    monkeypatch.setattr("core.streaming.pipeline.synthesize_natural_speech", synthesize)
    monkeypatch.setattr("core.streaming.pipeline.build_speech_timing", lambda *args: {})
    async def run():
        session._ensure_tts_engine()
        spoken, timing, plan = await session._fit_dub(focus, text=focus.final_vi, source="你那个绿色工具箱", output_path=path)
        assert spoken["text"] == focus.final_vi
        assert plan[focus.id]["dub_end"] - plan[focus.id]["dub_start"] >= 1.916
        for sid, bounds in plan.items():
            row = session.segments[sid]
            assert abs(bounds["dub_start"] - row.start) <= .35 + .000001
            assert abs(bounds["dub_end"] - row.end) <= .35 + .000001
        assert plan[0]["dub_end"] <= plan[1]["dub_start"]
    asyncio.run(run())
    assert budgets == pytest.approx([2.0])


def test_opus_prefix_uses_source_offsets_without_per_chunk_encoder_delay(preview, monkeypatch):
    session, *_ = preview
    root = session.cache_dir / "source_preparation"
    root.mkdir()
    records = []
    duration = .317
    # Real FFmpeg/Opus packets expose the ~6.5 ms container pre-skip that a
    # mocked subprocess cannot detect. Twenty intervals must not add 130 ms.
    for index in range(20):
        audio = root / f"packet_{index}.ogg"
        subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                        "-t", str(duration), "-c:a", "libopus", str(audio)], check=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        records.append({"start": round(index * duration, 6), "end": round((index + 1) * duration, 6),
                        "bgm": {"path": str(audio)}})
    by_start = {record["start"]: record for record in records}
    monkeypatch.setattr("core.streaming.chunked_source._load", lambda current, start: by_start[round(start, 6)])
    path = asyncio.run(real_background_prefix(session, records[-1]))
    data = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)))
    assert float(data["format"]["duration"]) == pytest.approx(20 * duration, abs=.02)
    subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-i", str(path), "-f", "null", "-"], check=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def test_resume_rechecks_interrupted_review_before_advancing_source(preview, monkeypatch):
    session, prepared, analyzed, *_ = preview
    session._chunked_source_started = True
    session.is_running = True
    session._visual_completed_seconds = 24
    session._source_prepared_seconds = 32
    calls = []
    # Include an early draft already synthesized by the older retry path,
    # an interrupted sentence without audio, and a future untouched row.
    for sid, status, review in [(0, "READY", "pending"), (1, "WAITING", "incomplete"),
                                 (2, "READY", "manual"), (3, "READY", "verified")]:
        row = SegmentItem(sid, sid * 4, sid * 4 + 2, 2)
        row.status, row.final_vi = status, f"Lời {sid}."
        row.source_method, row.translation_provider = "text-ai", "opencode"
        row.verification = {"status": review}
        row.needs_review = review in {"pending", "incomplete"}
        session.segments[sid] = row
    future = SegmentItem(4, 26, 28, 2)
    future.final_vi, future.needs_review = "Bản nháp tương lai.", True
    future.verification = {"status": "pending"}
    session.segments[4] = future
    async def review(**options):
        calls.append((options, len(prepared), len(analyzed)))
        for sid in options["segment_ids"]:
            session.segments[sid].verification = {"status": "verified"}
            session.segments[sid].needs_review = False
    monkeypatch.setattr(session, "_review_translations", review)
    async def run():
        session._ensure_tts_engine()
        await session._start_chunked_visual()
        await session.worker_task
    asyncio.run(run())
    assert calls == [({"regenerate_audio": True, "segment_ids": {0, 1}}, 0, 0)]
    assert session.segments[2].verification["status"] == "manual"
    assert session.segments[3].verification["status"] == "verified"
    assert session.segments[4].verification["status"] == "pending"


@pytest.mark.parametrize('reserved', [False, True])
@pytest.mark.parametrize('legacy_metadata', [False, True])
def test_resume_revalidates_failed_spanning_ocr_correction_before_resynthesizing(preview, monkeypatch, reserved, legacy_metadata):
    session, *_ = preview
    session._chunked_source_started = True
    session._visual_completed_seconds = 24
    for sid, status in ((5, "FAILED"), (6, "READY")):
        row = SegmentItem(sid, 8.68 if sid == 5 else 10.17, 9.4 if sid == 5 else 11.03, .72)
        row.source_method, row.translation_provider = "text-ai", "opencode"
        row.asr_text, row.text_zh = "小满", "小满今年19"
        row.final_vi, row.status, row.failed_stage = "Tiểu Mãn, năm nay mười chín.", status, "TTS"
        proof = {'text_zh': '小满今年19'}
        if not legacy_metadata:
            proof.update(full_text_zh='我是你女儿小满今年19', source_scope_ids=[4, 5])
        row.verification = {'status': 'corrected', 'source_supported': True, 'evidence': [proof]}
        session.segments[sid] = row
    if reserved:
        session.segments[5].status = 'WAITING'
        session.segments[5]._retry_synthesis = True
    calls = []
    async def review(**options):
        calls.append(options)
        assert session.segments[5].status == ('WAITING' if reserved else 'FAILED')
        session.segments[5].text_zh, session.segments[5].final_vi = '小满', 'Tiểu Mãn.'
        session.segments[5].verification = {'status': 'corrected', 'semantic_verified': True}
    monkeypatch.setattr(session, '_review_translations', review)
    asyncio.run(session._resume_pending_chunk_reviews())
    assert calls == [{'regenerate_audio': True, 'segment_ids': {5}}]
    assert session.segments[5].status == 'WAITING' and session.segments[5]._retry_synthesis
    assert session.segments[5].final_vi == 'Tiểu Mãn.'
    assert session.segments[6].status == 'READY'


def test_resume_fresh_reviews_stale_address_rows_in_bounded_groups_only(preview, monkeypatch):
    session, *_ = preview
    session._chunked_source_started = True
    session._visual_completed_seconds = 24
    session.segments = {}
    for sid in range(9):
        row = SegmentItem(sid, sid * 2, sid * 2 + 1, 1)
        row.source_method, row.translation_provider = "text-ai", "opencode"
        row.status, row.final_vi = "READY", "Con về rồi."
        row.verification = {"status": "unresolved", "address_stale_source_ids": [8]}
        session.segments[sid] = row
    # Unresolved source/meaning alone is not a stale address verdict. Manual
    # edits, already verified rows, other providers and future rows stay intact.
    excluded = [(10, "unresolved", []), (11, "manual", [8]),
                (12, "verified", [8]), (13, "unresolved", [8]),
                (14, "unresolved", [8])]
    for sid, status, stale_ids in excluded:
        row = SegmentItem(sid, 20 if sid != 14 else 26, 21 if sid != 14 else 27, 1)
        row.source_method, row.translation_provider = "text-ai", "other" if sid == 13 else "opencode"
        row.status, row.final_vi = "READY", "Lời giữ nguyên."
        row.verification = {"status": status, "address_stale_source_ids": stale_ids}
        session.segments[sid] = row
    calls = []
    async def review(**options):
        calls.append(options)
        for sid in options["segment_ids"]:
            session.segments[sid].verification = {"status": "verified"}
    monkeypatch.setattr(session, "_review_translations", review)
    asyncio.run(session._resume_pending_chunk_reviews())
    assert calls == [
        {"regenerate_audio": True, "segment_ids": {0, 1, 2, 3}, "force_review": True},
        {"regenerate_audio": True, "segment_ids": {4, 5, 6, 7}, "force_review": True},
        {"regenerate_audio": True, "segment_ids": {8}, "force_review": True}]
    for sid, status, stale_ids in excluded:
        assert session.segments[sid].verification == {"status": status, "address_stale_source_ids": stale_ids}
        assert session.segments[sid].final_vi == "Lời giữ nguyên."


def test_obsolete_negative_gates_are_reviewed_once_without_force_approval(preview, monkeypatch):
    from core.translation_review import AutomaticTranslationReviewer
    session, *_ = preview
    session._chunked_source_started = True
    session._visual_completed_seconds = 24
    session.segments = {}
    revision = AutomaticTranslationReviewer.REVIEW_GATE_REVISION
    audits = [
        {"status": "unresolved", "address_context": {"uncertain": True}},
        {"status": "unresolved", "audio_evidence": [], "audio_consensus": False},
        {"status": "unresolved", "address_context": {}, "review_gate_revision": revision},
        {"status": "manual", "address_context": {}},
        {"status": "verified", "address_context": {}},
        {"status": "unresolved"},
    ]
    for sid, audit in enumerate(audits):
        row = SegmentItem(sid, sid * 2, sid * 2 + 1, 1)
        row.status, row.text_zh, row.final_vi = "READY", "我", "Lời đã lưu."
        row.source_method, row.translation_provider = "text-ai", "opencode"
        row.verification = deepcopy(audit)
        session.segments[sid] = row
    calls = []
    async def review(**options):
        calls.append(options)
        for sid in options["segment_ids"]:
            audit = session.segments[sid].verification
            audit.update(status="unresolved", semantic_verified=False, review_gate_revision=revision)
    monkeypatch.setattr(session, "_review_translations", review)
    async def run():
        await session._resume_pending_chunk_reviews()
        await session._resume_pending_chunk_reviews()
    asyncio.run(run())
    assert calls == [{"regenerate_audio": True, "segment_ids": {0, 1}}]
    assert session.segments[0].verification["semantic_verified"] is False
    assert session.segments[1].verification["status"] == "unresolved"
    assert all(session.segments[sid].verification == audits[sid] for sid in range(2, 6))
    assert all(row.final_vi == "Lời đã lưu." for row in session.segments.values())


def test_source_ocr_owner_is_released_before_next_native_interval(preview, monkeypatch):
    import core.streaming.chunked_source as source
    session, *_ = preview
    session.translation_mode = "full"
    original_prepare, original_prepass = source.prepare_interval, session.video_intelligence.prepass
    live_ocr = False
    closed_intervals = []
    def prepass(*args, **options):
        nonlocal live_ocr
        live_ocr = True
        return original_prepass(*args, **options)
    def close():
        nonlocal live_ocr
        if live_ocr:
            closed_intervals.append(session._visual_scanned_seconds)
        live_ocr = False
    async def prepare(*args):
        assert not live_ocr, "Previous source OCR must not retain model RAM into ASR"
        return await original_prepare(*args)
    monkeypatch.setattr(source, "prepare_interval", prepare)
    monkeypatch.setattr(session.video_intelligence, "prepass", prepass)
    monkeypatch.setattr(session, "_release_visual_runtime", close)
    async def run():
        await session.start()
        await session.worker_task
    asyncio.run(run())
    assert len(closed_intervals) == 3
    assert not live_ocr


def test_stale_address_resume_uses_current_context_and_keeps_unchanged_ready_audio(preview, monkeypatch):
    session, *_ = preview
    session._chunked_source_started = True
    session._visual_completed_seconds = 24
    row = SegmentItem(0, 0, 1, 1)
    row.source_method, row.translation_provider = "text-ai", "opencode"
    row.text_zh, row.final_vi, row.status = "我回来了", "Con về rồi.", "READY"
    row.verification = {"status": "unresolved", "address_stale_source_ids": [1],
                        "address_context_sources": {"0": row.text_zh, "1": "爸爸"}}
    row.audio_path = str(session.segments_dir / "seg_0.wav")
    row.audio_url = "/api/streaming/audio/preview-owned/0?rev=0"
    wav(Path(row.audio_path))
    old_audio = Path(row.audio_path).read_bytes()
    source = SegmentItem(1, 2, 3, 1)
    source.text_zh, source.final_vi, source.status = "妈妈", "Mẹ.", "READY"
    source.verification = {"status": "manual"}
    session.segments = {0: row, 1: source}
    calls = []
    def review(_reviewer, video_path, rows, screens, **options):
        calls.append(options)
        assert [item.id for item in rows] == [0]
        assert [(item.id, item.text_zh) for item in options["context_segments"]] == [(0, "我回来了"), (1, "妈妈")]
        assert options["force_review"] is True
        return {"segments": {0: {**row.to_dict(), "needs_review": False,
            "verification": {"status": "verified", "semantic_verified": True,
                             "address_context_sources": {"0": row.text_zh, "1": source.text_zh}}}},
            "summary": {"checked": 1, "verified": 1}, "translation_sources": []}
    async def no_audio_rebuild(*args, **options):
        raise AssertionError("Unchanged reviewed speech must preserve its existing WAV")
    monkeypatch.setattr("core.translation_review.AutomaticTranslationReviewer.review", review)
    monkeypatch.setattr(session, "_review_translations", StreamingPipelineSession._review_translations.__get__(session))
    monkeypatch.setattr(session, "edit_segment", no_audio_rebuild)
    asyncio.run(session._resume_pending_chunk_reviews())
    assert len(calls) == 1
    assert row.verification["status"] == "verified" and not row.needs_review
    assert row.status == "READY" and row.revision == 0
    assert row.audio_url == "/api/streaming/audio/preview-owned/0?rev=0"
    assert Path(row.audio_path).read_bytes() == old_audio
    assert source.verification == {"status": "manual"} and source.final_vi == "Mẹ."


def test_resume_does_not_review_a_later_stale_address_row_after_manual_commit(preview, monkeypatch):
    session, *_ = preview
    session._chunked_source_started = True
    session._visual_completed_seconds = 24
    session.segments = {}
    for sid in range(5):
        row = SegmentItem(sid, sid * 2, sid * 2 + 1, 1)
        row.source_method, row.translation_provider = "text-ai", "opencode"
        row.status, row.final_vi = "READY", "Con về rồi."
        row.verification = {"status": "unresolved", "address_stale_source_ids": [3]}
        session.segments[sid] = row
    calls = []
    async def review(**options):
        calls.append(options)
        # The edit arrives while the first provider request is in progress.
        edited = session.segments[4]
        edited.final_vi = "Lời người dùng đã sửa."
        edited.revision += 1
        edited.verification = {"status": "manual"}
    monkeypatch.setattr(session, "_review_translations", review)
    asyncio.run(session._resume_pending_chunk_reviews())
    assert calls == [{"regenerate_audio": True, "segment_ids": {0, 1, 2, 3}, "force_review": True}]
    assert session.segments[4].verification == {"status": "manual"}
    assert session.segments[4].final_vi == "Lời người dùng đã sửa."


def test_final_source_pass_reviews_newly_stale_ready_address_once_and_keeps_uncertainty(preview, monkeypatch):
    session, *_ = preview
    session.total_duration = session._visual_completed_seconds = 24
    session.translation_mode = "full"
    session.initialized = True
    row = SegmentItem(0, 0, 1, 1)
    row.source_method, row.translation_provider = "text-ai", "opencode"
    row.status, row.final_vi = "READY", "Con về rồi."
    row.verification = {"status": "verified"}
    unrelated = SegmentItem(1, 2, 3, 1)
    unrelated.source_method, unrelated.translation_provider = "text-ai", "opencode"
    unrelated.status, unrelated.final_vi = "READY", "Bản nháp chưa rõ."
    unrelated.verification = {"status": "unresolved"}
    session.segments = {0: row, 1: unrelated}
    resume = StreamingPipelineSession._resume_pending_chunk_reviews.__get__(session)
    calls, stages = [], []
    async def resume_then_simulate_last_source_correction(**options):
        stages.append(("resume", options))
        await resume(**options)
        if not options.get("stale_address_only"):
            # A later accepted source correction invalidates the earlier row
            # after startup's recovery selection, before the final speech queue.
            row.verification = {"status": "unresolved", "address_stale_source_ids": [1]}
    async def still_uncertain(**options):
        calls.append(options)
        row.needs_review = True
        row.review_reason = "Chưa xác định chắc người nghe."
        # Keep stale evidence deliberately to prove no automatic review loop.
    async def worker():
        stages.append(("worker", row.verification["status"]))
    monkeypatch.setattr(session, "_resume_pending_chunk_reviews", resume_then_simulate_last_source_correction)
    monkeypatch.setattr(session, "_review_translations", still_uncertain)
    monkeypatch.setattr(session, "_worker_loop", worker)
    async def run():
        await session._start_chunked_visual()
        await session.worker_task
    asyncio.run(run())
    assert calls == [{"regenerate_audio": True, "segment_ids": {0}, "force_review": True}]
    assert stages == [("resume", {"synthesize_pending": True}), ("resume", {"stale_address_only": True}),
                      ("worker", "unresolved")]
    assert row.needs_review and row.verification["status"] == "unresolved"
    assert row.status == "READY" and row.final_vi == "Con về rồi."
    assert unrelated.verification == {"status": "unresolved"}


def test_pending_review_cannot_be_hidden_by_a_later_completed_group(preview):
    session, *_ = preview
    early = SegmentItem(0, 0, 1, 1)
    early.verification = {"status": "pending"}
    later = SegmentItem(1, 2, 3, 1)
    later.verification = {"status": "verified"}
    session.segments = {0: early, 1: later}
    session.review_summary = {"status": "completed"}
    session._refresh_review_counts()
    assert session.review_summary["status"] == "incomplete"
    assert session.review_summary["incomplete"] == 1
    assert session.review_summary["checked"] == 1


def test_recovered_review_updates_waiting_text_without_editing_missing_audio(preview, monkeypatch):
    session, *_ = preview
    # Exercise the actual review publication path, with provider isolation.
    monkeypatch.undo()
    row = SegmentItem(0, 0, 2, 2)
    row.final_vi, row.status = "Bản nháp.", "WAITING"
    row.verification = {"status": "pending"}
    session.segments = {0: row}
    result = {"segments": {0: {**row.to_dict(), "final_vi": "Lời đã sửa.", "needs_review": False,
                                   "verification": {"status": "corrected", "semantic_verified": True}}},
              "summary": {"checked": 1, "corrected": 1}, "translation_sources": []}
    monkeypatch.setattr("core.translation_review.AutomaticTranslationReviewer.review", lambda *args, **kwargs: result)
    async def no_missing_audio_edit(*args, **kwargs):
        raise AssertionError("Missing audio must be synthesized by the resumed worker, not the transcript editor")
    monkeypatch.setattr(session, "edit_segment", no_missing_audio_edit)
    asyncio.run(session._review_translations(regenerate_audio=True, segment_ids={0}))
    assert row.final_vi == "Lời đã sửa." and row.status == "WAITING"
    assert row.verification["semantic_verified"] is True


def test_failed_recovery_response_cannot_overwrite_newer_manual_edit(preview, monkeypatch):
    session, *_ = preview
    session._chunked_source_started = True
    session._visual_completed_seconds = 24
    row = SegmentItem(0, 0, 1, 1)
    row.source_method, row.translation_provider = "text-ai", "opencode"
    row.final_vi, row.status = "Bản nháp.", "READY"
    row.verification = {"status": "pending"}
    session.segments = {0: row}
    async def concurrent_edit(**options):
        row.revision += 1
        row.final_vi = "Lời người dùng sửa."
        row.verification, row.needs_review = {"status": "manual"}, False
        raise TimeoutError("Old review failed after user saved")
    monkeypatch.setattr(session, "_review_translations", concurrent_edit)
    asyncio.run(session._resume_pending_chunk_reviews())
    assert row.final_vi == "Lời người dùng sửa." and row.revision == 1
    assert row.verification == {"status": "manual"} and not row.needs_review


def test_dub_duration_cache_rechecks_replacement_and_rejects_truncated_pcm(preview, monkeypatch):
    session, *_ = preview
    path = session.segments_dir / "saved.wav"
    wav(path, .5)
    reads = []
    real = session._dub_audio_duration
    def counted(path):
        reads.append(str(path))
        return real(path)
    monkeypatch.setattr(session, "_dub_audio_duration", counted)
    assert session._cached_dub_audio_duration(path) == .5
    assert session._cached_dub_audio_duration(path) == .5
    assert len(reads) == 1
    replacement = session.segments_dir / "replacement.wav"
    wav(replacement, .8)
    replacement.replace(path)
    assert session._cached_dub_audio_duration(path) == .8
    assert len(reads) == 2
    data = path.read_bytes()
    path.write_bytes(data[:-10])
    with pytest.raises(ValueError, match="thiếu dữ liệu"):
        session._cached_dub_audio_duration(path)


def test_source_replacement_between_intervals_is_rejected_even_with_original_size_and_mtime(preview):
    import os
    from core.streaming.chunked_source import ensure_source_identity
    session, *_ = preview
    original = session.video_path.stat()
    signature = ensure_source_identity(session)
    assert ensure_source_identity(session) == signature
    replacement = session.video_path.parent / "replacement.mp4"
    replacement.write_bytes(b"X" * original.st_size)
    os.utime(replacement, ns=(original.st_atime_ns, original.st_mtime_ns))
    replacement.replace(session.video_path)
    with pytest.raises(ValueError, match="nguồn đã thay đổi"):
        ensure_source_identity(session)


def test_legacy_bounded_checkpoint_cannot_adopt_replaced_source(preview):
    from core.streaming.chunked_source import ensure_source_identity, _record_path, _source_identity
    session, *_ = preview
    original = _source_identity(session)
    checkpoint = _record_path(session, 0.0)
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text(json.dumps({"identity": original}), encoding="utf-8")
    session.video_path.write_bytes(b"replacement-source-video")
    with pytest.raises(ValueError, match="nguồn đã thay đổi"):
        ensure_source_identity(session)


def test_published_summary_reaches_the_next_bounded_prepass_as_untrusted_context(preview, monkeypatch):
    session, prepared, analyzed, *_ = preview
    original = session.video_intelligence.prepass
    seen = []
    def prepass(path, rows, **options):
        seen.append(options["previous_summary"])
        result = original(path, rows, **options)
        session._visual_context_summary = "Tóm tắt nháp đoạn trước."
        return result
    monkeypatch.setattr(session.video_intelligence, "prepass", prepass)
    async def run():
        await session.start()
        await session.worker_task
        await session.translate_full()
        await session.start_task
        await session.worker_task
    asyncio.run(run())
    assert seen[0] == "" and all(value == "Tóm tắt nháp đoạn trước." for value in seen[1:])


def saved_full_queue(session):
    session.translation_mode = "full"
    session.initialized = True
    session._chunked_source_started = session._visual_prepass_complete = True
    session._visual_completed_seconds = session.total_duration = 16
    session.error = "Earlier speech request failed"
    session.segments = {}
    for sid, status in enumerate(("READY", "FAILED", "WAITING", "WAITING")):
        row = SegmentItem(sid, sid * 4, sid * 4 + 2, 2)
        row.final_vi, row.status = f"Lời {sid}.", status
        row._retry_synthesis = True
        if status == "FAILED":
            row.failed_stage = "TTS"
        session.segments[sid] = row


def test_saved_full_retry_continues_later_rows_after_real_queue_failure(preview, monkeypatch):
    session, *_, events = preview
    saved_full_queue(session)
    original = session._synthesize_segment
    statuses = []
    async def synthesize(row):
        statuses.append((row.id, session.get_progress()["status"]))
        if row.id == 1:
            row.status = "TTS"
            raise RuntimeError("Empty speech provider response")
        await original(row)
    monkeypatch.setattr(session, "_synthesize_segment", synthesize)
    async def run():
        await session.retry_failed_synthesis()
        await session.worker_task
    asyncio.run(run())
    assert statuses == [(1, "RUNNING"), (2, "RUNNING"), (3, "RUNNING")]
    assert [row.status for row in session.segments.values()] == ["READY", "FAILED", "READY", "READY"]
    assert session.queue.empty() and session.can_retry
    assert session.get_progress()["status"] == "FAILED"
    assert not any(event == "progress" and payload["status"] == "COMPLETED" for event, payload in events)


def test_saved_retry_capacity_failure_cannot_start_speech_worker(preview, monkeypatch):
    from core.streaming.session_store import ProjectCapacityError
    session, *_ = preview
    saved_full_queue(session)
    workers = []
    async def review(**options):
        assert options == {"synthesize_pending": True}
        session._persistence_capacity_failed = True
        raise ProjectCapacityError("Dự án đạt giới hạn dữ liệu lưu.")
    async def worker():
        workers.append(True)
    monkeypatch.setattr(session, "_resume_pending_chunk_reviews", review)
    monkeypatch.setattr(session, "_worker_loop", worker)
    async def run():
        await session.retry_failed_synthesis()
        await session.worker_task
    asyncio.run(run())
    assert not workers and not session.is_running and not session.can_retry
    assert session.get_progress()["status"] == "FAILED"
    assert "giới hạn" in session.error


def test_queue_capacity_failure_cannot_become_completed(preview, monkeypatch):
    from core.streaming.session_store import ProjectCapacityError
    session, *_ = preview
    saved_full_queue(session)
    session.error, session.is_running = None, True
    async def update():
        session._persistence_capacity_failed = True
        raise ProjectCapacityError("Dự án đạt giới hạn dữ liệu lưu.")
    monkeypatch.setattr(session, "_update_ready", update)
    asyncio.run(session._worker_loop())
    assert not session.is_running and session.get_progress()["status"] == "FAILED"
    assert "giới hạn" in session.error


def test_accepted_review_survives_failed_speech_rebuild_and_queues_only_missing_audio(preview, monkeypatch):
    session, *_ = preview
    saved_full_queue(session)
    session.visual_translation = True
    row = session.segments[0]
    row.source_method, row.translation_provider = "text-ai", "opencode"
    row.verification = {"status": "pending"}
    row.audio_path = str(session.segments_dir / "seg_0.wav")
    wav(Path(row.audio_path))
    old_audio = Path(row.audio_path).read_bytes()
    result = {"segments": {0: {**row.to_dict(), "final_vi": "Lời đã kiểm tra.", "needs_review": False,
        "verification": {"status": "corrected", "semantic_verified": True}}},
        "summary": {"checked": 1, "corrected": 1}, "translation_sources": []}
    calls = []
    def review(*args, **kwargs):
        calls.append(True)
        return result
    monkeypatch.setattr("core.translation_review.AutomaticTranslationReviewer.review", review)
    monkeypatch.setattr(session, "_review_translations", StreamingPipelineSession._review_translations.__get__(session))
    async def fail_speech(*args, **kwargs):
        raise RuntimeError("Empty speech response")
    monkeypatch.setattr(session, "edit_segment", fail_speech)
    async def run():
        await session.retry_failed_synthesis()
        await session.worker_task
    asyncio.run(run())
    assert calls == [True]
    assert row.final_vi == "Lời đã kiểm tra." and row.status == "READY"
    assert row.verification == {"status": "corrected", "semantic_verified": True}
    assert row.revision == 1 and row.audio_url
    assert Path(row.audio_path).read_bytes() == old_audio


def test_resume_missing_reviewed_speech_is_not_blocked_by_unrelated_review(preview, monkeypatch):
    session, _, _, speech, _ = preview
    saved_full_queue(session)
    for row in session.segments.values():
        row.source_method, row.translation_provider = "text-ai", "opencode"
        row.verification = {"status": "verified", "semantic_verified": True}
    pending = session.segments[2]
    pending.verification = {"status": "incomplete", "semantic_verified": False}
    calls = []
    async def review(**options):
        assert speech == [1, 3], "Already-reviewed missing WAVs waited on unrelated Muse work"
        calls.append(options)
        pending.verification = {"status": "verified", "semantic_verified": True}
    monkeypatch.setattr(session, "_review_translations", review)
    async def run():
        await session.retry_failed_synthesis()
        await session.worker_task
    asyncio.run(run())
    assert calls == [{"regenerate_audio": True, "segment_ids": {2}}]
    assert speech == [1, 3, 2] and all(row.status == "READY" for row in session.segments.values())


def test_resume_publishes_each_review_group_before_later_request_and_does_not_repeat_failure(preview, monkeypatch):
    session, _, _, speech, _ = preview
    saved_full_queue(session)
    session.segments = {}
    for sid in range(6):
        row = SegmentItem(sid, sid * 4, sid * 4 + 2, 2)
        row.source_method, row.translation_provider = "text-ai", "opencode"
        row.status, row.final_vi, row.failed_stage = "WAITING", f"Lời {sid}.", "TTS"
        row._retry_synthesis = True
        row.verification = {"status": "incomplete", "semantic_verified": False}
        session.segments[sid] = row
    session._visual_completed_seconds = session.total_duration = 24
    original = session._synthesize_segment
    attempts, groups = [], []
    async def synthesize(row):
        attempts.append(row.id)
        if row.id == 1:
            row.status = "TTS"
            raise RuntimeError("Temporary real-provider class of failure")
        await original(row)
    async def review(**options):
        ids = options["segment_ids"]
        if groups:
            assert speech == [0, 2, 3] and session.segments[1].status == "FAILED"
        groups.append(ids)
        for sid in ids:
            session.segments[sid].verification = {"status": "verified", "semantic_verified": True}
    monkeypatch.setattr(session, "_synthesize_segment", synthesize)
    monkeypatch.setattr(session, "_review_translations", review)
    session.error, session.is_running = None, True
    async def run():
        await session._resume_pending_chunk_reviews(synthesize_pending=True)
        # The final queue must contain only still-unstarted speech; FAILED
        # stays a durable failure for a later explicit/bounded recovery pass.
        assert not [row for row in session.segments.values() if row.status == "WAITING"]
        await session._worker_loop()
    asyncio.run(run())
    assert groups == [{0, 1, 2, 3}, {4, 5}]
    assert attempts == [0, 1, 2, 3, 4, 5] and speech == [0, 2, 3, 4, 5]
    assert session.segments[1].status == "FAILED" and session.can_retry


def test_resume_speech_stop_keeps_later_rows_unstarted(preview, monkeypatch):
    session, _, _, speech, _ = preview
    saved_full_queue(session)
    session.error, session.is_running = None, True
    original = session._synthesize_segment
    async def synthesize(row):
        await original(row)
        session.is_stopped = True
    monkeypatch.setattr(session, "_synthesize_segment", synthesize)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(session._resume_speech_rows(session.segments.values()))
    assert speech == [2] and session.segments[3].status == "WAITING"
