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
        assert analyzed[-1][0:2] == (23, 24)
        assert session.segments[3].status == "WAITING"
    asyncio.run(run())


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
