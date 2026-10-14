"""Regression contracts for bounded preparation, promotion and edit ownership.

Providers are isolated here. Production/provider acceptance is recorded
separately by the real UI harness, never inferred from these tests.
"""
import asyncio
import threading
import time
import subprocess
import json
import wave
from copy import deepcopy
from pathlib import Path

import pytest

from config import settings
from core.streaming.chunked_source import owned_boundary, prepare_interval, publish_background_prefix as real_background_prefix
from core.streaming.pipeline import SegmentEditConflict, SegmentItem, StreamingPipelineSession


@pytest.mark.parametrize("stage,label", [
    ("ASR", "chưa nhận diện được lời nguồn"), ("TRANSLATING", "chưa dịch được"),
    ("TTS", "chưa tạo được giọng"), ("ALIGNING", "chưa căn được thời lượng giọng"),
])
def test_worker_final_error_preserves_failed_stage(preview, stage, label):
    session, *_ = preview
    row = SegmentItem(0, 0, 1, 1)
    row.status, row.failed_stage = "FAILED", stage
    session.segments = {0: row}
    session._chunked_source_started = True
    session._visual_scanned_seconds = session._source_prepared_seconds = 1.
    session.is_running, session.is_stopped, session.error = True, False, None
    asyncio.run(session._worker_loop(recover_pacing=False))
    assert f"1 câu {label}" in session.error
    assert "Tiếp tục" in session.error
    if stage != "TTS":
        assert "chưa tạo được giọng" not in session.error


def test_successful_speech_batch_survives_unrelated_ocr_failure_without_coverage(preview, monkeypatch):
    from core.engines.translation.opencode_client import OpenCodeRequestError
    session, _, _, speech, events = preview
    def partial_then_fail(path, rows, **options):
        row = rows[0]
        result = {"segments": {row.id: {"id": row.id, "start": row.start, "end": row.end,
            "text_zh": row.text_zh, "literal_vi": "Lời đã dịch.", "natural_vi": "Lời đã dịch.",
            "final_vi": "Lời đã dịch.", "needs_review": True, "review_reason": "Cần đối chiếu.",
            "translation_provider": "opencode", "translation_model": "isolated-provider"}},
            "screen_texts": [], "translation_sources": [], "summary": ""}
        options["partial_callback"](result, options["start_time"], options["end_time"])
        assert session.segments[row.id].final_vi == "Lời đã dịch."
        assert session._visual_completed_seconds == 0
        raise OpenCodeRequestError("OCR sibling failed", code="provider_failed")
    monkeypatch.setattr(session.video_intelligence, "prepass", partial_then_fail)
    async def run():
        await session.start()
        await session.worker_task
    asyncio.run(run())
    assert session.segments[0].final_vi == "Lời đã dịch."
    assert session.segments[0].status == "READY" and speech == [0]
    assert session._visual_completed_seconds == 0 and not session._preview_ready
    assert session._chunk_jobs[0]["state"] == "FAILED"
    assert any(e == "segment_update" and p.get("final_vi") == "Lời đã dịch." for e, p in events)


def test_partial_batch_never_replaces_manual_text_or_claims_screen_coverage(preview):
    session, *_ = preview
    session.initialized = True
    row = SegmentItem(0, 0, 1, 1)
    row.final_vi, row.verification = "Lời của người dùng.", {"status": "manual"}
    session.segments = {0: row}
    session.screen_texts = [{"id": "keep", "start": 0, "end": 1}]
    result = {"segments": {0: {"final_vi": "Lời mới."}}, "screen_texts": [], "summary": "new"}
    asyncio.run(session._publish_visual_chunk(result, 0, 1, partial_only=True))
    assert row.final_vi == "Lời của người dùng."
    assert session.screen_texts == [{"id": "keep", "start": 0, "end": 1}]
    assert session._visual_completed_seconds == 0


def advance_retry_clock(session, monkeypatch, before_due=None):
    """Advance only retry wall clocks, retaining the real 300s schedule check."""
    from core.streaming import pipeline, retry_schedule
    class Clock:
        now = time.time()
        def time(self): return self.now
        def __getattr__(self, name): return getattr(time, name)
    clock = Clock()
    monkeypatch.setattr(pipeline, 'time', clock)
    monkeypatch.setattr(retry_schedule, 'time', clock)
    waits = []
    async def due(record):
        assert record['next_attempt_at'] - record['updated_at'] >= 300
        waits.append(deepcopy(record))
        assert len(waits) <= 8, 'Unexpected unbounded retry in a bounded fixture'
        if before_due: before_due(record)
        clock.now = max(clock.now, record['next_attempt_at'] + .001)
        await asyncio.sleep(0)
    monkeypatch.setattr(session, '_retry_delay', due)
    return waits


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
    # Existing sequential contracts; parallel ownership is exercised separately.
    monkeypatch.setattr(settings, "TRANSLATION_CHUNK_WORKERS", 1)
    model = tmp_path / "workspace/models" / f"faster-whisper-{settings.WHISPER_MODEL_SIZE}"
    model.mkdir(parents=True)
    for name in ("config.json", "model.bin", "tokenizer.json", "vocabulary.json"):
        (model / name).write_bytes(b"isolated asset-identity fixture; never used for inference")
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

    async def synthesize(item, **options):
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


def test_source_preparation_failure_with_no_rows_never_publishes_ready_preview(preview, monkeypatch, caplog):
    session, prepared, analyzed, speech, events = preview

    async def unavailable_source(current, start, end):
        prepared.append((start, end))
        raise MemoryError("controlled recognizer failure before source publication")

    monkeypatch.setattr("core.streaming.chunked_source.prepare_interval", unavailable_source)

    async def run():
        await session.start()
        await session.worker_task
        snapshot = session.get_progress()
        assert snapshot["status"] == "FAILED"
        assert snapshot["can_retry"] and not snapshot["can_translate_full"]
        assert session._startup_failed and not session._preview_ready
        assert session._source_prepared_seconds == session._visual_completed_seconds == 0
        assert not session.segments and snapshot["total_segments"] == 0
        assert session.video_path.is_file()

    asyncio.run(run())
    assert prepared == [(0, 24)] and analyzed == speech == []
    assert "SOURCE_RETRY_PENDING source_start=0.0 error_type=MemoryError" in caplog.text
    assert "controlled recognizer failure before source publication" in caplog.text
    assert "unavailable_source" in caplog.text
    assert not any(event == "progress" and payload["status"] in {"PREVIEW_READY", "COMPLETED"}
                   for event, payload in events)


def test_unresolved_source_with_no_vi_or_wav_is_prepared_for_review_without_false_preview(preview, monkeypatch):
    session, prepared, analyzed, speech, events = preview

    async def unresolved_review(**options):
        for sid in options["segment_ids"]:
            row = session.segments[sid]
            row.final_vi = row.literal_vi = row.natural_vi = ""
            row.needs_review = True
            row.verification = {"status": "unresolved", "semantic_verified": False}
        session.review_summary = {"status": "completed"}

    async def empty_playback(row, **options):
        assert row.needs_review and not row.final_vi
        row.status = "READY"
        await session.emit("segment_update", row.to_dict())

    monkeypatch.setattr(session, "_review_translations", unresolved_review)
    monkeypatch.setattr(session, "_synthesize_segment", empty_playback)

    async def run():
        await session.start()
        await session.worker_task
        progress = session.get_progress()
        assert progress["status"] == "PREPARED" and progress["content_review_state"] == "REVIEW_REQUIRED"
        assert not progress["preview_output_ready"] and not session._preview_ready
        assert not session.can_translate_full and not session.error
        assert progress["preview_untranslated_ids"] == [0, 1, 2]
        assert all(session.segments[sid].needs_review and not session.segments[sid].confirmed_silence
                   for sid in range(3))

    asyncio.run(run())
    assert prepared == [(0, 24)] and speech == []
    assert not any(event == "progress" and payload["status"] in {"PREVIEW_READY", "COMPLETED"}
                   for event, payload in events)


def test_actually_prepared_speechless_prefix_can_publish_ready_preview(preview, monkeypatch):
    session, prepared, analyzed, speech, events = preview

    async def silent_source(current, start, end):
        prepared.append((start, end))
        raw = current.cache_dir / "recognized_silence.wav"
        wav(raw, end - start)
        return {"start": start, "end": end, "rows": [],
                "raw_audio": {"path": str(raw)}, "suppression_stats": {}}

    monkeypatch.setattr("core.streaming.chunked_source.prepare_interval", silent_source)

    async def run():
        await session.start()
        await session.worker_task
        assert session.get_progress()["status"] == "PREVIEW_READY"
        assert session.can_translate_full and not session.error
        assert session._source_prepared_seconds == session._visual_completed_seconds == 24
        assert not session.segments and session.playable_until == 24

    asyncio.run(run())
    assert prepared == [(0, 24)] and speech == []
    assert analyzed[0][0:3] == (0, 24, [])


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


def test_explicit_review_only_rechecks_published_preview_and_preserves_waiting_lookahead(preview, monkeypatch):
    session, prepared, analyzed, speech, events = preview
    reviewed = []
    original_review = session._review_translations

    async def review(**options):
        reviewed.append(options)
        await original_review(**options)

    async def run():
        await session.start()
        await session.worker_task
        future = session.segments[3]
        before = deepcopy(vars(future))
        wavs = {sid: Path(session.segments[sid].audio_path).read_bytes() for sid in range(3)}
        assert future.status == "WAITING" and session.get_progress()["can_review"]
        monkeypatch.setattr(session, "_review_translations", review)
        await session.start_automatic_review()
        await session.review_task
        assert session.get_progress()["status"] == "PREVIEW_READY"
        assert session.can_translate_full and session.get_progress()["can_review"]
        assert vars(future) == before
        assert wavs == {sid: Path(session.segments[sid].audio_path).read_bytes() for sid in range(3)}
        assert not any(event == "progress" and payload["status"] == "COMPLETED" for event, payload in events)

    asyncio.run(run())
    assert reviewed == [{"regenerate_audio": True, "force_review": True, "segment_ids": {0, 1, 2}}]
    assert prepared == [(0, 24)] and speech == [0, 1, 2]


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
    healthy = {}
    def recover(record):
        nonlocal broken
        assert requests == [0, 24, 48]
        assert session._visual_scanned_seconds == 64 and session._visual_completed_seconds == 0
        assert session.get_progress()["retry_chunks"] == 1
        assert all(session.segments[sid].status == "READY" for sid in range(3, 8))
        healthy.update({sid: Path(session.segments[sid].audio_path).read_bytes() for sid in range(3, 8)})
        broken = False
    waits = advance_retry_clock(session, monkeypatch, recover)
    async def run():
        await asyncio.wait_for(session.start(), 10)
        await session.worker_task
        assert requests == [0, 24, 48, 0], "Completed intervals must not call the provider again"
        assert session._visual_completed_seconds == 64
        assert all(row.status == "READY" for row in session.segments.values())
        assert all(Path(session.segments[sid].audio_path).read_bytes() == data for sid, data in healthy.items())
    asyncio.run(run())
    assert len(waits) == 1 and not session._retry_schedule


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


def test_full_parallel_workers_own_disjoint_minute_ranges_and_publish_in_order(preview, monkeypatch):
    session, prepared, analyzed, speech, events = preview
    session.translation_mode = "full"
    session.total_duration = 300
    monkeypatch.setattr(settings, "TRANSLATION_CHUNK_WORKERS", 5)
    monkeypatch.setattr(settings, "OPENCODE_CONCURRENCY", 5)
    monkeypatch.setattr(session, "_parallel_chunk_capacity", lambda: 5)
    original = session.video_intelligence.prepass
    lock, barrier = threading.Lock(), threading.Barrier(5)
    started, completed, owners, contexts = [], [], {}, {}

    class PrivateEngine:
        provider = "opencode"
        class OCR:
            def close(self):
                pass
        screen_ocr = OCR()
        def prepass(self, path, rows, **options):
            start = options["start_time"]
            with lock:
                started.append(start)
                owners[start] = [row.id for row in rows]
                contexts[start] = deepcopy(options["context_segments"])
            barrier.wait(5)
            # Later intervals finish sooner, but they may not publish first.
            time.sleep((300 - start) / 10000)
            result = original(path, rows, **options)
            with lock:
                completed.append(start)
            return result

    monkeypatch.setattr(session, "_new_chunk_intelligence", PrivateEngine)
    async def run():
        await session.start()
        await session.worker_task
    asyncio.run(run())
    assert len(started) == 5 and completed != sorted(completed)
    jobs = session._chunk_jobs
    assert jobs[0]["start"] == 0 and jobs[-1]["end"] == 300
    assert all(a["end"] == b["start"] for a, b in zip(jobs, jobs[1:]))
    flattened = [sid for start in sorted(owners) for sid in owners[start]]
    assert len(flattened) == len(set(flattened)) == len(session.segments)
    assert speech == sorted(session.segments), "Review/TTS publication follows the timeline"
    assert all(row["state"] == "COMPLETED" and row["attempts"] == 1 for row in jobs)
    assert all(row.status == "READY" for row in session.segments.values())
    assert all(contexts[start][0].id == 0 for start in contexts), "Distant raw relationship cues survive"
    assert session.get_progress()["chunk_workers"] == 5
    assert all(row.end <= start + 60 + 12 for start, rows in contexts.items() for row in rows)


def test_parallel_interval_retry_is_independent_and_resume_reuses_completed_owners(preview, monkeypatch):
    session, prepared, analyzed, speech, events = preview
    session.translation_mode = "full"
    session.total_duration = 120
    monkeypatch.setattr(session, "_parallel_chunk_capacity", lambda: 2)
    original = session.video_intelligence.prepass
    attempts = {}
    broken = True
    class PrivateEngine:
        provider = "opencode"
        class OCR:
            def close(self):
                pass
        screen_ocr = OCR()
        def prepass(self, path, rows, **options):
            start = options["start_time"]
            attempts[start] = attempts.get(start, 0) + 1
            if broken and start == 0:
                # Keep complete partial batches through a later provider error.
                if attempts[start] == 1:
                    partial = {**options, "end_time": rows[0].end}
                    original(path, rows[:1], **partial)
                raise TimeoutError("injected interval failure")
            return original(path, rows, **options)
    monkeypatch.setattr(session, "_new_chunk_intelligence", PrivateEngine)
    healthy = {}
    prior_bounds = []
    later_calls = 0
    def recover(record):
        nonlocal broken, later_calls
        successful = next(row for row in session._chunk_jobs if row["start"] != 0)
        assert successful["state"] == "COMPLETED" and successful["attempts"] == 1
        assert session._visual_completed_seconds == 0
        assert session.segments[0].status == "READY"
        healthy.update({sid: Path(row.audio_path).read_bytes() for sid, row in session.segments.items() if row.status == "READY"})
        prior_bounds.extend((row["start"], row["end"]) for row in session._chunk_jobs)
        later_calls = attempts[successful["start"]]
        monkeypatch.setattr(settings, "TRANSLATION_CHUNK_SECONDS", 30)
        broken = False
    advance_retry_clock(session, monkeypatch, recover)
    async def run():
        await asyncio.wait_for(session.start(), 10)
        await session.worker_task
        assert attempts[next(row["start"] for row in session._chunk_jobs if row["start"] != 0)] == later_calls
        assert [(row["start"], row["end"]) for row in session._chunk_jobs] == prior_bounds
        assert all(Path(session.segments[sid].audio_path).read_bytes() == content for sid, content in healthy.items())
        assert all(row.status == "READY" for row in session.segments.values())
    asyncio.run(run())


def test_parallel_stop_drains_private_workers_and_checkpoints_each_owner(preview, monkeypatch):
    session, *_ = preview
    session.translation_mode = "full"
    session.total_duration = 120
    monkeypatch.setattr(session, "_parallel_chunk_capacity", lambda: 2)
    entered, left = threading.Barrier(3), []
    class PrivateEngine:
        provider = "opencode"
        class OCR:
            def close(self):
                pass
        screen_ocr = OCR()
        def prepass(self, path, rows, **options):
            entered.wait(5)
            try:
                while not options["cancel_check"]():
                    time.sleep(.005)
                raise asyncio.CancelledError
            finally:
                left.append(options["start_time"])
    monkeypatch.setattr(session, "_new_chunk_intelligence", PrivateEngine)
    async def run():
        start = asyncio.create_task(session.start())
        await asyncio.to_thread(entered.wait, 5)
        session.stop()
        start.cancel()
        await asyncio.gather(start, return_exceptions=True)
        assert len(left) == 2
        assert all(row["state"] == "RETRY_PENDING" for row in session._chunk_jobs)
        assert session._chunk_followup_task is None
        assert not any(task is not asyncio.current_task() and not task.done()
                       for task in asyncio.all_tasks())
    asyncio.run(run())


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
        assert session._chunk_jobs[0]["state"] == "FAILED"
        assert session._chunk_jobs[0]["attempts"] == 1
        assert not session._retry_schedule, "Invalid content must require repair, not transport retry"
    asyncio.run(run())


def test_partial_source_retry_waits_for_owned_speech_and_preserves_ready_rows(preview, monkeypatch):
    session, prepared, analyzed, speech, _ = preview
    advance_retry_clock(session, monkeypatch)
    session.total_duration = 24
    original_prepass = session.video_intelligence.prepass
    original_synthesize = session._synthesize_segment
    attempts = []
    entered, release = asyncio.Event(), asyncio.Event()

    def prepass(path, rows, **options):
        attempts.append(options["start_time"])
        if len(attempts) > 1:
            publish = options["chunk_callback"]
            def changed_draft(result, start, end):
                for data in result["segments"].values():
                    data["final_vi"] = "Bản nháp thử lại khác."
                return publish(result, start, end)
            options["chunk_callback"] = changed_draft
        result = original_prepass(path, rows, **options)
        if len(attempts) == 1:
            raise TimeoutError("injected failure after partial publication")
        return result

    async def synthesize(item, **options):
        if item.id == 0:
            entered.set()
            await release.wait()
        await original_synthesize(item, **options)

    monkeypatch.setattr(session.video_intelligence, "prepass", prepass)
    monkeypatch.setattr(session, "_synthesize_segment", synthesize)

    async def run():
        start = asyncio.create_task(session.start())
        try:
            await asyncio.wait_for(entered.wait(), 3)
            await asyncio.sleep(.05)
            assert attempts == [0], "Retry must not republish a row still owned by speech"
        finally:
            release.set()
            await asyncio.wait_for(start, 5)
        await session.worker_task
        assert attempts == [0, 0]
        assert speech == [0, 1, 2], "Healthy speech must not be generated twice"
        assert all(row.status == "READY" for row in session.segments.values())
        assert [row.final_vi for row in session.segments.values()] == [f"Lời {sid}." for sid in range(3)]

    asyncio.run(run())


def test_retry_drain_reports_crashed_consumer_without_hanging(preview):
    session, *_ = preview

    async def run():
        session._chunk_followups = asyncio.Queue(maxsize=2)
        session._chunk_followups.put_nowait("unfinished owner")
        async def crashed():
            raise RuntimeError("injected drain worker crash")
        session._chunk_followup_task = asyncio.create_task(crashed())
        with pytest.raises(RuntimeError, match="injected drain worker crash"):
            await asyncio.wait_for(session._drain_chunk_followups(), 1)
        assert not [task for task in asyncio.all_tasks()
                    if task is not asyncio.current_task() and not task.done()]

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
    async def fail_one(row, **options):
        if row.id == 1:
            row.status = "ALIGNING"
            raise ValueError("isolated unfit speech")
        await original(row, **options)
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
    async def synthesize(row, **options):
        if row.id != 7:
            assert any(row.id in ids for ids in reviewed)
        assert row.verification["status"] != "pending"
        await original_speech(row, **options)
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


@pytest.mark.parametrize("change", ["model_asset", "loaded_grouping", "runtime", "missing_model", "chunk_policy"])
def test_source_cache_rejects_changed_runtime_but_preserves_valid_background(preview, monkeypatch, change):
    from core.streaming import chunked_source as source
    from core.streaming import source_revision
    session, *_ = preview
    root = session.cache_dir / "source_preparation"
    root.mkdir(parents=True)
    raw, bgm = root / "raw.wav", root / "bgm.wav"
    wav(raw)
    wav(bgm)
    record = {"identity": source._source_identity(session), "start": 0., "end": 5.,
        "rows": [{"start": 0., "end": 5., "duration": 5., "text_zh": "开头"}],
        "raw_audio": source._asset(raw), "bgm": source._asset(bgm)}
    source._save(session, record)
    assert source._load(session, 0.) == record
    model = settings.BASE_DIR / "workspace/models" / f"faster-whisper-{session.faster_whisper.model_size}"
    if change == "model_asset":
        (model / "model.bin").write_bytes(b"different asset bytes under the same model name")
    elif change == "missing_model":
        (model / "model.bin").unlink()
    elif change == "loaded_grouping":
        monkeypatch.setattr(session, "_grounded_visual_segments", lambda rows: rows)
    elif change == "chunk_policy":
        monkeypatch.setattr(source, "LOOKAHEAD_SECONDS", source.LOOKAHEAD_SECONDS + 1.)
    else:
        version = source_revision.metadata.version
        monkeypatch.setattr(source_revision.metadata, "version",
            lambda name: "different-runtime-version" if name == "faster-whisper" else version(name))
    assert source._load(session, 0.) is None
    assert source._load(session, 0., background_only=True) == record
    assert raw.is_file() and bgm.is_file()


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
    def saved_packet(current, start, *, background_only=False):
        assert background_only
        return by_start[round(start, 6)]
    monkeypatch.setattr("core.streaming.chunked_source._load", saved_packet)
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
    assert calls == [({"regenerate_audio": True, "segment_ids": {0, 1}, "defer_audio": True}, 0, 0)]
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


def test_cross_script_source_recovery_only_revisits_affected_nonmanual_rows(preview, monkeypatch):
    from core.translation_review import AutomaticTranslationReviewer
    session, *_ = preview
    session._chunked_source_started = True
    session._visual_completed_seconds = 24
    session.source_language = "auto"
    session.segments = {}
    for sid in range(3):
        row = SegmentItem(sid, sid * 2, sid * 2 + 1, 1)
        row.status, row.asr_text, row.text_zh, row.final_vi = "READY", "안녕하세요", "你好", "Xin chào."
        row.source_method, row.translation_provider = "text-ai", "opencode"
        row.verification = {"status": "manual" if sid == 1 else "verified", "source_supported": True}
        if sid == 2:
            row.text_zh = row.asr_text
        session.segments[sid] = row
    original = deepcopy(session.segments[1].to_dict())
    calls = []
    async def review(**options):
        calls.append(options)
        for sid in options["segment_ids"]:
            row = session.segments[sid]
            row.text_zh = row.asr_text
            row.verification = {"status": "unresolved", "source_supported": False,
                "review_gate_revision": AutomaticTranslationReviewer.REVIEW_GATE_REVISION}
    monkeypatch.setattr(session, "_review_translations", review)
    async def run():
        await session._resume_pending_chunk_reviews()
        await session._resume_pending_chunk_reviews()
    asyncio.run(run())
    assert calls == [{"regenerate_audio": True, "segment_ids": {0}}]
    assert session.segments[0].verification["source_supported"] is False
    assert session.segments[1].to_dict() == original


def test_orthography_recheck_targets_only_unexamined_ascii_draft(preview, monkeypatch):
    from core.translation_review import AutomaticTranslationReviewer
    session, *_ = preview
    session._chunked_source_started = True
    session._visual_completed_seconds = 24
    session.segments = {}
    for sid, text in enumerate(["Lời đã lưu.", "Hom nay troi dep qua.", "Hom nay troi dep qua."]):
        row = SegmentItem(sid, sid * 2, sid * 2 + 1, 1)
        row.status, row.text_zh, row.final_vi = "READY", "今天", text
        row.source_method, row.translation_provider = "text-ai", "opencode"
        row.verification = {"status": "unresolved", "audio_evidence": [],
            "review_gate_revision": AutomaticTranslationReviewer.REVIEW_GATE_REVISION}
        if sid == 2:
            row.verification["target_orthography"] = {"original": text, "status": "review_required"}
        session.segments[sid] = row
    calls = []
    async def review(**options):
        calls.append(options)
        for sid in options["segment_ids"]:
            session.segments[sid].verification["target_orthography"] = {
                "original": session.segments[sid].final_vi, "status": "review_required"}
    monkeypatch.setattr(session, "_review_translations", review)
    async def run():
        await session._resume_pending_chunk_reviews()
        await session._resume_pending_chunk_reviews()
    asyncio.run(run())
    assert calls == [{"regenerate_audio": True, "segment_ids": {1}}]
    assert all(row.verification["status"] == "unresolved" for row in session.segments.values())


def test_resume_reaudits_exact_numeric_audio_false_negative_without_approving_content(preview, monkeypatch):
    from core.translation_review import AutomaticTranslationReviewer
    session, *_ = preview
    session._chunked_source_started = True
    session._visual_completed_seconds = 24
    session.segments = {}
    for sid, first, second in [(0, "90%。", "百分之九十"), (1, "90%", "百分之十九")]:
        row = SegmentItem(sid, sid * 2, sid * 2 + 1, 1)
        row.status, row.text_zh, row.final_vi = "READY", second, "Bản nháp giữ nguyên."
        row.source_method, row.translation_provider = "text-ai", "opencode"
        row.verification = {"status": "unresolved", "audio_consensus": False,
            "review_gate_revision": AutomaticTranslationReviewer.REVIEW_GATE_REVISION,
            "audio_evidence": [{"engine": "sensevoice", "text_zh": first},
                               {"engine": "faster-whisper-small", "text_zh": second}]}
        session.segments[sid] = row
    calls = []
    async def review(**options):
        calls.append(options)
        assert session.segments[0].verification["status"] == "unresolved"
        session.segments[0].verification.update(audio_consensus=True, semantic_verified=False)
    monkeypatch.setattr(session, "_review_translations", review)
    async def run():
        await session._resume_pending_chunk_reviews()
        await session._resume_pending_chunk_reviews()
    asyncio.run(run())
    assert calls == [{"regenerate_audio": True, "segment_ids": {0}}]
    assert session.segments[0].verification["semantic_verified"] is False
    assert session.segments[1].verification["audio_consensus"] is False


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


@pytest.mark.parametrize("newer_state", ["manual", "revision"])
def test_review_dispatch_freezes_context_before_worker_and_preserves_newer_edit(preview, monkeypatch, newer_state):
    session, *_ = preview
    focus = SegmentItem(0, 0, 1, 1)
    focus.text_zh = focus.asr_text = "我回来了"
    focus.final_vi = "Con về rồi."
    focus.speaker_id = "voice-0"
    focus.speaker_evidence = {"scope_id": "first", "references": [{"source": "original"}]}
    focus.verification = {"status": "pending", "semantic_verified": False}
    neighbor = SegmentItem(1, 2, 3, 1)
    neighbor.text_zh = neighbor.asr_text = "妈妈"
    neighbor.speaker_id = "voice-1"
    neighbor.speaker_evidence = {"scope_id": "first", "references": [{"source": "neighbor-original"}]}
    session.segments = {0: focus, 1: neighbor}
    session.screen_texts = [{"id": "title-0", "kind": "title", "start": 0, "end": 3,
        "text_zh": "家", "text_vi": "Nhà", "bbox": [.1, .1, .5, .1]}]
    expected_targets = deepcopy([focus.to_dict()])
    expected_context = deepcopy([focus.to_dict(), neighbor.to_dict()])
    expected_screens = deepcopy(session.screen_texts)
    original_blocking = session._run_blocking
    calls = []

    def review(_reviewer, video_path, rows, screens, **options):
        calls.append(1)
        assert video_path == session.video_path
        assert [row.to_dict() for row in rows] == expected_targets
        context = options["context_segments"]
        assert [row.to_dict() for row in context] == expected_context
        assert context[0] is not focus and context[1] is not neighbor
        assert screens == expected_screens
        return {"segments": {0: {**rows[0].to_dict(), "final_vi": "Bản rà đã cũ.",
            "needs_review": False, "verification": {"status": "verified", "semantic_verified": True}}},
            "summary": {"checked": 1, "verified": 1}, "translation_sources": []}

    monkeypatch.setattr("core.translation_review.AutomaticTranslationReviewer.review", review)
    monkeypatch.setattr(session, "_review_translations", StreamingPipelineSession._review_translations.__get__(session))

    async def run():
        dispatched, release = asyncio.Event(), asyncio.Event()

        async def delayed_worker(function, *args, **options):
            # Hold the executor boundary, rather than freezing the context in
            # the fake reviewer: production must snapshot before dispatch.
            dispatched.set()
            await release.wait()
            return await original_blocking(function, *args, **options)

        monkeypatch.setattr(session, "_run_blocking", delayed_worker)
        owner = asyncio.create_task(session._review_translations(segment_ids={0}))
        await asyncio.wait_for(dispatched.wait(), 5)
        focus.revision += 1
        focus.text_zh, focus.final_vi = "我刚回家", "Lời vừa sửa được giữ."
        focus.speaker_id = "voice-new"
        focus.speaker_evidence["references"][0]["source"] = "changed"
        focus.verification = ({"status": "manual"} if newer_state == "manual"
                              else {"status": "pending", "semantic_verified": False})
        neighbor.text_zh = "爸爸"
        neighbor.speaker_id = "voice-other"
        neighbor.speaker_evidence["references"][0]["source"] = "neighbor-changed"
        session.screen_texts[0]["text_vi"] = "Tiêu đề mới"
        session.screen_texts[0]["bbox"][0] = .2
        release.set()
        await asyncio.wait_for(owner, 5)

    asyncio.run(run())
    assert calls == [1]
    assert focus.text_zh == "我刚回家" and focus.final_vi == "Lời vừa sửa được giữ."
    assert focus.speaker_id == "voice-new" and focus.speaker_evidence["references"][0]["source"] == "changed"
    assert focus.verification == ({"status": "manual"} if newer_state == "manual"
                                  else {"status": "pending", "semantic_verified": False})
    assert neighbor.text_zh == "爸爸" and neighbor.speaker_id == "voice-other"
    assert session.screen_texts[0]["text_vi"] == "Tiêu đề mới" and session.screen_texts[0]["bbox"][0] == .2


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
    # Exercise real publication without undoing the fixture's storage isolation.
    # Review commits are durable and must never save this test in user history.
    monkeypatch.setattr(session, "_review_translations",
                        StreamingPipelineSession._review_translations.__get__(session))
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
    assert settings.BASE_DIR == session.video_path.parent, "Durable review tests must retain isolated storage"
    asyncio.run(session._review_translations(regenerate_audio=True, segment_ids={0}))
    assert row.final_vi == "Lời đã sửa." and row.status == "WAITING"
    assert row.verification["semantic_verified"] is True
    assert (settings.BASE_DIR / "workspace/projects/preview-owned.json").is_file()


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


def test_retry_resets_real_elapsed_clock_and_measured_throughput_baseline(preview):
    session, *_ = preview
    saved_full_queue(session)
    session._run_started_at, session._run_ready_baseline = 1.0, 999
    async def run():
        before = time.monotonic()
        progress = await session.retry_failed_synthesis()
        after = time.monotonic()
        assert before <= session._run_started_at <= after
        assert session._run_ready_baseline == 1
        assert progress['elapsed_seconds'] is not None and 0 <= progress['elapsed_seconds'] <= after - before
        assert progress['eta_seconds'] is None, 'No fresh throughput was measured yet'
        await session.worker_task
    asyncio.run(run())


def test_saved_full_retry_continues_later_rows_after_real_queue_failure(preview, monkeypatch):
    session, *_, events = preview
    saved_full_queue(session)
    original = session._synthesize_segment
    statuses = []
    async def synthesize(row, **options):
        statuses.append((row.id, session.get_progress()["status"]))
        if row.id == 1:
            row.status = "TTS"
            raise RuntimeError("Empty speech provider response")
        await original(row, **options)
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
    assert calls == [{"regenerate_audio": True, "segment_ids": {2}, "defer_audio": True}]
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
    async def synthesize(row, **options):
        attempts.append(row.id)
        if row.id == 1:
            row.status = "TTS"
            raise RuntimeError("Temporary real-provider class of failure")
        await original(row, **options)
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
    async def synthesize(row, **options):
        await original(row, **options)
        session.is_stopped = True
    monkeypatch.setattr(session, "_synthesize_segment", synthesize)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(session._resume_speech_rows(session.segments.values()))
    assert speech == [2] and session.segments[3].status == "WAITING"


def test_resume_generates_independent_wavs_before_slow_pacing_and_preserves_failure(preview, monkeypatch):
    from core.engines.alignment.timing_aligner import SpeechBudgetError
    session, *_ = preview
    saved_full_queue(session)
    session.error, session.is_running = None, True
    session.segments[1].status = "WAITING"
    calls = []
    original = session._synthesize_segment
    async def synthesize(row, *, allow_pacing=True):
        calls.append((row.id, allow_pacing))
        if row.id == 1:
            if allow_pacing:
                assert session.segments[2].status == session.segments[3].status == "READY"
                raise RuntimeError("Bounded pacing request failed")
            row.status = "ALIGNING"
            raise SpeechBudgetError("Measured complete WAV exceeds safe capacity")
        await original(row)
    monkeypatch.setattr(session, "_synthesize_segment", synthesize)
    asyncio.run(session._resume_speech_rows(session.segments.values()))
    assert calls == [(1, False), (2, False), (3, False), (1, True)]
    assert session.segments[1].status == "FAILED"
    assert session.segments[2].status == session.segments[3].status == "READY"


def test_resume_does_not_overwrite_manual_revision_while_pacing_deferred(preview, monkeypatch):
    from core.engines.alignment.timing_aligner import SpeechBudgetError
    session, *_ = preview
    saved_full_queue(session)
    session.error, session.is_running = None, True
    session.segments[1].status = "WAITING"
    calls = []
    original = session._synthesize_segment
    async def synthesize(row, *, allow_pacing=True):
        calls.append((row.id, allow_pacing))
        if row.id == 1:
            row.status = "ALIGNING"
            raise SpeechBudgetError("Measured complete WAV exceeds safe capacity")
        if row.id == 2:
            changed = session.segments[1]
            changed.revision += 1
            changed.final_vi, changed.status = "Lời người dùng vừa lưu.", "READY"
            changed.verification = {"status": "manual"}
        await original(row)
    monkeypatch.setattr(session, "_synthesize_segment", synthesize)
    asyncio.run(session._resume_speech_rows(session.segments.values()))
    assert calls == [(1, False), (2, False), (3, False)]
    assert session.segments[1].final_vi == "Lời người dùng vừa lưu."
    assert session.segments[1].verification["status"] == "manual"


def test_slow_pacing_waits_for_all_review_groups_independent_speech(preview, monkeypatch):
    from core.engines.alignment.timing_aligner import SpeechBudgetError
    session, _, _, speech, _ = preview
    saved_full_queue(session)
    session.segments = {}
    for sid in range(7):
        row = SegmentItem(sid, sid * 4, sid * 4 + 2, 2)
        row.source_method, row.translation_provider = "text-ai", "opencode"
        row.status, row.final_vi, row.failed_stage = "WAITING", f"Lời {sid}.", "TTS"
        row._retry_synthesis = True
        row.verification = {"status": "verified" if sid == 0 else "incomplete",
                            "semantic_verified": sid == 0}
        session.segments[sid] = row
    session._visual_completed_seconds = session.total_duration = 28
    session.error, session.is_running = None, True
    original = session._synthesize_segment
    calls, groups = [], []

    async def synthesize(row, *, allow_pacing=True):
        calls.append((row.id, allow_pacing))
        if row.id in {0, 1}:
            row.status = "ALIGNING"
            if not allow_pacing:
                raise SpeechBudgetError("Measured complete waveform is overlong")
            assert groups == [{1, 2, 3, 4}, {5, 6}]
            assert speech == [2, 3, 4, 5, 6], "Later easy speech was delayed by early pacing"
            raise RuntimeError("Bounded provider failure")
        await original(row)

    async def review(**options):
        ids = options["segment_ids"]
        if groups:
            assert speech == [2, 3, 4]
        groups.append(ids)
        for sid in ids:
            session.segments[sid].verification = {"status": "verified", "semantic_verified": True}

    monkeypatch.setattr(session, "_synthesize_segment", synthesize)
    monkeypatch.setattr(session, "_review_translations", review)
    asyncio.run(session._resume_pending_chunk_reviews(synthesize_pending=True))
    assert calls == [(0, False), (1, False), (2, False), (3, False), (4, False),
                     (5, False), (6, False), (0, True), (1, True)]
    assert session.segments[0].status == session.segments[1].status == "FAILED"
    assert all(session.segments[sid].status == "READY" for sid in range(2, 7))


def test_collected_pacing_skips_duplicate_and_newer_manual_revision(preview, monkeypatch):
    from core.engines.alignment.timing_aligner import SpeechBudgetError
    session, *_ = preview
    saved_full_queue(session)
    session.error, session.is_running = None, True
    session.segments[1].status = "WAITING"
    original = session._synthesize_segment
    calls = []

    async def synthesize(row, *, allow_pacing=True):
        calls.append((row.id, allow_pacing))
        if not allow_pacing and row.id in {1, 2}:
            row.status = "ALIGNING"
            raise SpeechBudgetError("Measured overlong candidate")
        await original(row)

    monkeypatch.setattr(session, "_synthesize_segment", synthesize)
    async def run():
        deferred = await session._resume_speech_rows(session.segments.values(), defer_pacing=True)
        manual = session.segments[1]
        manual.revision += 1
        manual.final_vi, manual.status = "Lời vừa sửa tay.", "READY"
        manual.verification = {"status": "manual"}
        await session._resume_deferred_speech_rows(deferred + deferred)
    asyncio.run(run())
    assert calls == [(1, False), (2, False), (3, False), (2, True)]
    assert session.segments[1].final_vi == "Lời vừa sửa tay."
    assert session.segments[1].verification["status"] == "manual"


def test_transient_tts_exhaustion_retries_late_after_independent_siblings(preview, monkeypatch):
    from core.engines.tts.edge_fallback import EdgeTTSRequestError
    session, _, _, speech, events = preview
    saved_full_queue(session)
    session.error, session.is_running = None, True
    session.segments[1].status = "WAITING"
    original = session._synthesize_segment
    calls = []
    def before_due(record):
        assert calls == [(1, False), (2, False), (3, False)]
        assert speech == [2, 3] and session.queue.empty()
        assert record["state"] == "RETRY_PENDING" and record["rounds"] == 1
    waits = advance_retry_clock(session, monkeypatch, before_due)
    async def synthesize(row, *, allow_pacing=True):
        calls.append((row.id, allow_pacing))
        if row.id == 1 and not allow_pacing:
            row.status = "TTS"
            raise EdgeTTSRequestError("Safe empty audio after 3 attempts", code="tts_empty_audio", retryable=True)
        if row.id == 1:
            assert speech == [2, 3], "Transient row retried before independent audio was published"
        await original(row)
    monkeypatch.setattr(session, "_synthesize_segment", synthesize)
    async def run():
        await session._resume_speech_rows(session.segments.values())
        assert calls == [(1, False), (2, False), (3, False)]
        assert len(session._retry_schedule) == 1
        await asyncio.wait_for(session._worker_loop(), 5)
    asyncio.run(run())
    assert calls == [(1, False), (2, False), (3, False), (1, True)]
    pending = [payload for event, payload in events if event == "segment_update" and payload.get("id") == 1
               and payload.get("status") == "WAITING"]
    assert pending and pending[0]["processing_state"] == "RETRY_PENDING"
    assert session.segments[1].status == "READY" and session.segments[1].error is None
    assert all("Câu 2 chưa tạo" not in warning for warning in session.warnings)
    assert session.get_progress()["segment_states"]["RETRY_PENDING"] == 0
    assert len(waits) == 1 and not session._retry_schedule


@pytest.mark.parametrize("permanent", ["auth", "tls", "configuration", "untyped"])
def test_permanent_tts_errors_never_enter_late_retry_but_siblings_continue(preview, monkeypatch, permanent):
    from core.engines.tts.edge_fallback import EdgeTTSRequestError
    session, _, _, speech, _ = preview
    saved_full_queue(session)
    session.error, session.is_running = None, True
    session.segments[1].status = "WAITING"
    original = session._synthesize_segment
    calls = []
    errors = {"auth": EdgeTTSRequestError("Unauthorized", code="tts_http_error", status=401),
        "tls": EdgeTTSRequestError("TLS unavailable", code="tts_tls_certificate"),
        "configuration": ValueError("Unsupported configuration"), "untyped": RuntimeError("Unknown provider failure")}
    async def synthesize(row, *, allow_pacing=True):
        calls.append((row.id, allow_pacing))
        if row.id == 1:
            row.status = "TTS"
            raise errors[permanent]
        await original(row)
    monkeypatch.setattr(session, "_synthesize_segment", synthesize)
    asyncio.run(session._resume_speech_rows(session.segments.values()))
    assert calls == [(1, False), (2, False), (3, False)] and speech == [2, 3]
    assert session.segments[1].status == "FAILED" and session.segments[1].failed_stage == "TTS"


def test_repeated_transient_tts_failure_schedules_unlimited_rounds_until_stopped(preview, monkeypatch):
    from core.engines.tts.edge_fallback import EdgeTTSRequestError
    session, _, _, speech, _ = preview
    saved_full_queue(session)
    session.error, session.is_running = None, True
    session.segments[1].status = "WAITING"
    original = session._synthesize_segment
    calls = []
    def before_due(record):
        assert speech == [2, 3] and session.queue.empty()
        assert all(session.segments[sid].status == "READY" for sid in (2, 3))
        if record["rounds"] == 3:
            session.stop()
            raise asyncio.CancelledError
    waits = advance_retry_clock(session, monkeypatch, before_due)
    async def synthesize(row, *, allow_pacing=True):
        calls.append((row.id, allow_pacing))
        if row.id == 1:
            row.status = "TTS"
            raise EdgeTTSRequestError("Safe rate limit", code="tts_rate_limited", status=429, retryable=True)
        await original(row)
    monkeypatch.setattr(session, "_synthesize_segment", synthesize)
    async def run():
        deferred = await session._resume_speech_rows(session.segments.values(), defer_pacing=True)
        assert session.segment_snapshot(session.segments[1])["processing_state"] == "RETRY_PENDING"
        await session._resume_deferred_speech_rows(deferred + deferred)
        assert calls == [(1, False), (2, False), (3, False)], "Cooling failures are not pacing work"
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(session._worker_loop(), 5)
    asyncio.run(run())
    assert calls == [(1, False), (2, False), (3, False), (1, True), (1, True)] and speech == [2, 3]
    assert [record["rounds"] for record in waits] == [1, 2, 3]
    assert all(later["next_attempt_at"] >= earlier["next_attempt_at"] + 300
               for earlier, later in zip(waits, waits[1:]))
    assert session.is_stopped and len(session._retry_schedule) == 1
    assert session._retry_schedule[0]["state"] == "RETRY_PENDING"
    assert session.get_progress()["segment_states"]["RETRY_PENDING"] == 1, session.segments[1].to_dict()
    assert any("Câu 2 chưa tạo" in warning for warning in session.warnings)


@pytest.mark.parametrize("change", ["stop", "manual"])
def test_transient_late_retry_preserves_stop_and_newer_manual_revision(preview, monkeypatch, change):
    from core.engines.tts.edge_fallback import EdgeTTSRequestError
    session, *_ = preview
    saved_full_queue(session)
    session.error, session.is_running = None, True
    session.segments[1].status = "WAITING"
    original = session._synthesize_segment
    calls = []
    async def synthesize(row, *, allow_pacing=True):
        calls.append((row.id, allow_pacing))
        if row.id == 1:
            row.status = "TTS"
            raise EdgeTTSRequestError("Safe temporary failure", code="tts_timeout", retryable=True)
        if row.id == 2:
            if change == "stop":
                session.is_stopped = True
            else:
                focus = session.segments[1]
                focus.revision += 1
                focus.status, focus.final_vi = "READY", "Lời vừa sửa tay."
                focus.verification = {"status": "manual"}
        await original(row)
    monkeypatch.setattr(session, "_synthesize_segment", synthesize)
    if change == "stop":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(session._resume_speech_rows(session.segments.values()))
        assert calls == [(1, False), (2, False)]
        assert session.segments[1].status == "WAITING" and session.segments[3].status == "WAITING"
    else:
        async def run():
            await session._resume_speech_rows(session.segments.values())
            assert not await session._queue_due_speech_retries()
            assert not session._retry_schedule and session.queue.empty()
        asyncio.run(run())
        assert calls == [(1, False), (2, False), (3, False)]
        assert session.segments[1].final_vi == "Lời vừa sửa tay."
        assert session.segments[1].verification["status"] == "manual"


def test_parallel_timing_only_neighbor_revision_keeps_undispatched_translation(preview, monkeypatch):
    session, _, _, speech, _ = preview
    session.translation_mode, session.total_duration = "full", 120
    monkeypatch.setattr(session, "_parallel_chunk_capacity", lambda: 2)
    original_prepass = session.video_intelligence.prepass
    original_synthesize = session._synthesize_segment
    timing_published = threading.Event()
    moved = []

    class PrivateEngine:
        provider = "opencode"
        class OCR:
            def close(self):
                pass
        screen_ocr = OCR()

        def prepass(self, path, rows, **options):
            if options["start_time"] >= 60:
                assert timing_published.wait(5), "The earlier chunk did not publish neighbor timing"
            return original_prepass(path, rows, **options)

    async def synthesize(row, **options):
        if not moved:
            later = next(item for item in session.segments.values() if item.start >= 60)
            assert later.status == "WAITING" and not later.final_vi and not later.translation_provider
            owner_before = session._visual_review_state(later)
            revision_before = later.revision
            session._publish_dub_plan({later.id: {
                "dub_start": later.start + .1, "dub_end": later.end + .1}}, row.id)
            assert later.revision == revision_before + 1
            assert session._visual_review_state(later) == owner_before
            moved.append(later.id)
            timing_published.set()
        await original_synthesize(row, **options)

    monkeypatch.setattr(session, "_new_chunk_intelligence", PrivateEngine)
    monkeypatch.setattr(session, "_synthesize_segment", synthesize)

    async def run():
        await session.start()
        await session.worker_task

    asyncio.run(run())
    assert moved and moved[0] in speech
    later = session.segments[moved[0]]
    assert later.final_vi and later.translation_provider == "opencode" and later.status == "READY"
    assert later.revision == 1 and later.dub_start == later.start + .1
    assert all(job["state"] == "COMPLETED" and job["attempts"] == 1 for job in session._chunk_jobs)
    assert speech == sorted(session.segments)


def test_parallel_pending_owners_resume_with_one_worker_without_replanning(preview, monkeypatch):
    from core.streaming.recovery import reserve, validate_records
    from core.streaming.session_store import save_session

    session, _, analyzed, speech, _ = preview
    session.translation_mode, session.total_duration = "full", 120
    session._chunked_source_started = session._parallel_chunk_planning = True
    reserve(session._chunk_jobs, 0., 60., "1" * 64)
    reserve(session._chunk_jobs, 60., 120., "2" * 64)
    original_bounds = [(job["id"], job["start"], job["end"]) for job in session._chunk_jobs]
    # The planner marker and PENDING owners must survive durable storage before
    # memory pressure changes the available worker count from two to one.
    manifest = json.loads(save_session(session).read_text(encoding="utf-8"))
    assert manifest["session"]["_parallel_chunk_planning"] is True
    assert all(job["state"] == "PENDING" for job in manifest["session"]["_chunk_jobs"])
    assert session._visual_scanned_seconds == 0
    monkeypatch.setattr(session, "_parallel_chunk_capacity", lambda: 1)
    monkeypatch.setattr(settings, "TRANSLATION_CHUNK_SECONDS", 30.)
    original_prepass = session.video_intelligence.prepass

    class PrivateEngine:
        provider = "opencode"
        class OCR:
            def close(self):
                pass
        screen_ocr = OCR()
        def prepass(self, path, rows, **options):
            return original_prepass(path, rows, **options)

    monkeypatch.setattr(session, "_new_chunk_intelligence", PrivateEngine)

    async def run():
        await session.start()
        await session.worker_task

    asyncio.run(run())
    assert [(job["id"], job["start"], job["end"]) for job in session._chunk_jobs] == original_bounds
    validate_records(session._chunk_jobs, session.total_duration)
    assert [(lo, hi) for lo, hi, *_ in analyzed] == [(0., 60.), (60., 120.)]
    assert all(job["state"] == "COMPLETED" and job["attempts"] == 1 for job in session._chunk_jobs)
    assert len(speech) == len(set(speech)) == len(session.segments)
    assert session.get_progress()["chunk_workers"] == 1


def test_parallel_extended_boundary_prepares_stable_lookahead_before_retry(preview, monkeypatch):
    session, *_ = preview
    advance_retry_clock(session, monkeypatch)
    session.translation_mode, session.total_duration = "full", 130
    session._parallel_chunk_planning = True
    monkeypatch.setattr(session, "_parallel_chunk_capacity", lambda: 1)
    original_prepass = session.video_intelligence.prepass
    prepared, contexts, attempts = [], [], {}

    async def prepare_until(end):
        prepared.append(end)
        candidates = [(0, 55., 65.), (1, 67., 67.5), (2, 70., 72.)]
        for sid, start, stop in candidates:
            if stop <= end and sid not in session.segments:
                row = SegmentItem(sid, start, stop, stop - start)
                row.text_zh = row.asr_text = f"源{sid}"
                row.asr_pretranscribed = True
                session.segments[sid] = row
        session._source_prepared_seconds = max(session._source_prepared_seconds, end)
        session._prepared = True
        return True

    class PrivateEngine:
        provider = "opencode"
        class OCR:
            def close(self):
                pass
        screen_ocr = OCR()
        def prepass(self, path, rows, **options):
            start = options["start_time"]
            attempts[start] = attempts.get(start, 0) + 1
            if start == 0:
                contexts.append([(row.id, row.start, row.end, row.asr_text)
                                 for row in options["context_segments"]])
                assert options["end_time"] == 65.
                assert {row.id for row in options["context_segments"]} == {0, 1, 2}
            result = original_prepass(path, rows, **options)
            if start == 0 and attempts[start] == 1:
                raise TimeoutError("Injected failure after a checkpointed batch")
            return result

    monkeypatch.setattr(session, "_prepare_source_until", prepare_until)
    monkeypatch.setattr(session, "_new_chunk_intelligence", PrivateEngine)

    async def run():
        await session.start()
        await session.worker_task

    asyncio.run(run())
    assert prepared[:2] == [68., 73.], "Lookahead must follow the measured, extended boundary"
    assert len(contexts) == 2 and contexts[0] == contexts[1]
    first = session._chunk_jobs[0]
    assert (first["start"], first["end"], first["attempts"], first["state"]) == (0., 65., 2, "COMPLETED")


@pytest.mark.parametrize("fault", ["missing", "duplicate", "gap", "extra_id"])
def test_parallel_invalid_callback_coverage_keeps_owner_pending_and_healthy_peer(preview, monkeypatch, fault):
    session, _, _, speech, _ = preview
    session.translation_mode, session.total_duration = "full", 120
    monkeypatch.setattr(session, "_parallel_chunk_capacity", lambda: 2)
    original_prepass = session.video_intelligence.prepass
    attempts = {}

    class PrivateEngine:
        provider = "opencode"
        class OCR:
            def close(self):
                pass
        screen_ocr = OCR()
        def prepass(self, path, rows, **options):
            start = options["start_time"]
            attempts[start] = attempts.get(start, 0) + 1
            if start != 0:
                return original_prepass(path, rows, **options)
            publish = options["chunk_callback"]
            def broken_callback(result, lo, hi):
                if fault == "missing":
                    return
                if fault == "duplicate":
                    publish(result, lo, hi)
                    publish(result, lo, hi)
                elif fault == "gap":
                    publish(result, lo + 1., hi)
                elif fault == "extra_id":
                    invalid = deepcopy(result)
                    invalid["segments"][9999] = {**next(iter(result["segments"].values())), "id": 9999}
                    publish(invalid, lo, hi)
            return original_prepass(path, rows, **{**options, "chunk_callback": broken_callback})

    monkeypatch.setattr(session, "_new_chunk_intelligence", PrivateEngine)

    async def run():
        await session.start()
        await session.worker_task

    asyncio.run(run())
    failed, healthy = session._chunk_jobs
    assert failed["state"] == "FAILED" and failed["attempts"] == 1
    assert healthy["state"] == "COMPLETED" and healthy["attempts"] == 1
    assert attempts == {0.: 1, 60.: 1}
    assert all(row.status == "READY" for row in session.segments.values() if row.start >= 60)
    assert len(speech) == len(set(speech)), "Partial successful callback batches must not synthesize twice"
