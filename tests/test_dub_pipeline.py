"""Offline publication regressions for dub reflow, cancellation and persistence."""
import asyncio
from array import array
from copy import deepcopy
import math
from pathlib import Path
import threading
import wave

import pytest

from config import settings
from core.streaming import pipeline
from core.streaming.pipeline import SegmentItem, StreamingPipelineSession, active_streaming_sessions
from core.streaming.session_store import restore_saved_session, save_session


def write_pcm(path, duration, *, rate=24000):
    """A synthetic fixture signal; no provider or downloaded test media."""
    pcm = array("h", (round(6000 * math.sin(2 * math.pi * 440 * i / rate))
                      for i in range(round(duration * rate))))
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        audio.writeframes(pcm.tobytes())


@pytest.fixture
def session(tmp_path, monkeypatch):
    for name, value in {
        "BASE_DIR": tmp_path, "WORKSPACE_DIR": tmp_path / "workspace",
        "INPUT_DIR": tmp_path / "workspace/inputs", "OUTPUT_DIR": tmp_path / "workspace/outputs",
        "TEMP_DIR": tmp_path / "workspace/temp",
    }.items():
        value.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(settings, name, value)
    source = settings.INPUT_DIR / "clip.mp4"
    source.write_bytes(b"offline source fixture")
    result = StreamingPipelineSession("dub-pipeline", source, tts_engine_name="edge-tts")
    result.initialized, result.total_duration = True, 10.
    result.tts_engine = object()
    result.translator = None

    previous = SegmentItem(5, 6.84, 7.72, .88)
    previous.status, previous.text_zh, previous.final_vi = "READY", "正值巅峰", "Đang đỉnh cao."
    previous.audio_path = str(result.segments_dir / "seg_5.wav")
    write_pcm(previous.audio_path, .878)
    previous.subtitle_cues = [{"text": previous.final_vi, "start": 6.87, "end": 7.65,
        "words": [{"text": "Đang", "start": 6.87, "end": 7.1},
                  {"text": "đỉnh", "start": 7.1, "end": 7.4},
                  {"text": "cao.", "start": 7.4, "end": 7.65}]}]
    previous.speech_start, previous.speech_end = 6.87, 7.65
    previous.subtitle_timing_source = "edge-word-boundary"
    previous.audio_url = "/api/streaming/audio/dub-pipeline/5?rev=0"

    focus = SegmentItem(6, 7.72, 8.34, .62)
    focus.status, focus.text_zh, focus.final_vi = "READY", "十九", "Cũ."
    focus.audio_path = str(result.segments_dir / "seg_6.wav")
    write_pcm(focus.audio_path, .4)
    focus.audio_url = "/api/streaming/audio/dub-pipeline/6?rev=0"
    focus.subtitle_cues = [{"text": "Cũ.", "start": 7.72, "end": 8.1,
                            "words": [{"text": "Cũ.", "start": 7.72, "end": 8.1}]}]
    focus.speech_start, focus.speech_end = 7.72, 8.1
    focus.verification = {"status": "verified", "semantic_verified": True}
    result.segments = {5: previous, 6: focus}

    old_sessions = dict(active_streaming_sessions)
    active_streaming_sessions.clear()
    yield result
    active_streaming_sessions.clear()
    active_streaming_sessions.update(old_sessions)


def synthesize_fixture(*, text, output_path, **kwargs):
    write_pcm(output_path, .8563)
    return {"text": text, "tts_duration": .8563, "speed_ratio": 1.,
            "boundaries": [{"text": "Mười", "start": 0., "end": .4},
                           {"text": "chín.", "start": .4, "end": .8563}],
            "pacing_verification": None}


def snapshot(session):
    return {
        "segments": {identity: deepcopy(vars(row)) for identity, row in session.segments.items()},
        "audio": {row.audio_path: Path(row.audio_path).read_bytes()
                  for row in session.segments.values() if row.audio_path},
    }


@pytest.mark.parametrize("stage", ["synthesis", "speech_timing"])
@pytest.mark.parametrize("cancel_mode", ["cancel_task", "stop_session"])
def test_cancel_during_fit_preserves_previous_audio_and_metadata(
        session, monkeypatch, stage, cancel_mode):
    before = snapshot(session)
    manifest = session.persist()
    persisted = manifest.read_bytes()
    entered, release = threading.Event(), threading.Event()
    original_timing = pipeline.build_speech_timing

    def pause():
        entered.set()
        if not release.wait(5):
            raise TimeoutError("Regression worker was not released")

    def synthesis(**kwargs):
        result = synthesize_fixture(**kwargs)
        if stage == "synthesis":
            pause()
        return result

    def speech_timing(*args, **kwargs):
        result = original_timing(*args, **kwargs)
        if stage == "speech_timing":
            pause()
        return result

    monkeypatch.setattr(pipeline, "synthesize_natural_speech", synthesis)
    monkeypatch.setattr(pipeline, "build_speech_timing", speech_timing)

    async def run():
        job = asyncio.create_task(session.edit_segment(6, "Mười chín."))
        try:
            assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 3), 4)
            if cancel_mode == "cancel_task":
                job.cancel()
            else:
                session.is_stopped = True
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(job, 4)
        finally:
            release.set()
            if not job.done():
                job.cancel()
                try:
                    await job
                except asyncio.CancelledError:
                    pass

    asyncio.run(run())
    assert snapshot(session) == before
    assert manifest.read_bytes() == persisted
    assert not session.edit_tasks
    assert not list(session.segments_dir.glob("edit_*.wav"))
    assert not list(session.segments_dir.glob("pending_*.wav"))


def test_reflow_persists_outer_and_word_timestamps_with_unchanged_source(session, monkeypatch):
    monkeypatch.setattr(pipeline, "synthesize_natural_speech", synthesize_fixture)
    previous = session.segments[5]
    before = snapshot(session)
    asyncio.run(session.edit_segment(6, "Mười chín."))
    assert previous.dub_start < previous.start
    delta = previous.dub_start - previous.start
    original = before["segments"][5]["subtitle_cues"][0]
    cue = previous.subtitle_cues[0]
    assert cue["start"] == pytest.approx(original["start"] + delta)
    assert cue["end"] == pytest.approx(original["end"] + delta)
    for old, new in zip(original["words"], cue["words"]):
        assert new["start"] == pytest.approx(old["start"] + delta)
        assert new["end"] == pytest.approx(old["end"] + delta)
    assert previous.speech_start == cue["start"]
    assert previous.speech_end == cue["end"]
    assert Path(previous.audio_path).read_bytes() == before["audio"][previous.audio_path]
    save_session(session)
    for _ in range(2):
        active_streaming_sessions.clear()
        restored = restore_saved_session(session.task_id)
        for identity, row in restored.segments.items():
            expected = session.segments[identity]
            assert (row.start, row.end, row.duration) == (expected.start, expected.end, expected.duration)
            assert (row.dub_start, row.dub_end) == (expected.dub_start, expected.dub_end)
            assert row.subtitle_cues == expected.subtitle_cues
            assert row.speech_start == expected.speech_start
            assert row.speech_end == expected.speech_end
        save_session(restored)


@pytest.mark.parametrize("status", ["WAITING", "TTS", "FAILED"])
@pytest.mark.parametrize("stale_path", ["none", "missing", "existing"])
def test_unready_future_reservation_never_gets_playable_audio_url(session, status, stale_path):
    future = SegmentItem(7, 8.34, 9., .66)
    future.status = status
    if stale_path != "none":
        # A path retained from a failed/retried row does not prove new audio is ready.
        future.audio_path = str(session.segments_dir / "seg_7.wav")
        if stale_path == "existing":
            write_pcm(future.audio_path, .5)
    session.segments[7] = future
    session._publish_dub_plan({6: {"dub_start": 7.72, "dub_end": 8.5},
                              7: {"dub_start": 8.5, "dub_end": 9.16}}, 6)
    assert future.audio_url is None
    assert future.status == status
    assert (future.start, future.end, future.duration) == (8.34, 9., .66)


@pytest.mark.parametrize("damage", ["not_wav", "empty", "truncated_pcm"])
def test_invalid_synthesis_wav_cannot_replace_previous_result(session, monkeypatch, damage):
    before = snapshot(session)

    def invalid_synthesis(**kwargs):
        result = synthesize_fixture(**kwargs)
        path = Path(kwargs["output_path"])
        if damage == "not_wav":
            path.write_bytes(b"provider said success but not a WAV")
        elif damage == "empty":
            write_pcm(path, 0)
        else:
            # Keep a valid header claiming .8563 seconds; remove most actual PCM.
            # Header-only duration checks must not authorize a truncated file.
            with path.open("r+b") as stream:
                stream.truncate(100)
        return result

    monkeypatch.setattr(pipeline, "synthesize_natural_speech", invalid_synthesis)
    with pytest.raises((ValueError, RuntimeError, wave.Error, EOFError)):
        asyncio.run(session.edit_segment(6, "Mười chín."))
    assert snapshot(session) == before
    assert not list(session.segments_dir.glob("edit_*.wav"))


def test_fitted_audio_uses_export_sample_tolerance_not_four_sample_slack(session, monkeypatch):
    focus = session.segments[6]
    duration = .62 + 3 / 44100

    def synthesis(**kwargs):
        result = synthesize_fixture(**kwargs)
        write_pcm(kwargs["output_path"], duration, rate=44100)
        result["tts_duration"] = duration
        result["boundaries"] = [{"text": kwargs["text"], "start": 0., "end": duration}]
        return result

    monkeypatch.setattr(pipeline, "synthesize_natural_speech", synthesis)
    pending = session.segments_dir / "fit-tolerance.wav"
    _, _, plan = asyncio.run(session._fit_dub(focus, text="Mười chín.", source="十九", output_path=pending))
    bounds = plan.get(focus.id, {"dub_start": focus.start, "dub_end": focus.end})
    slot_frames = round(bounds["dub_end"] * 44100) - round(bounds["dub_start"] * 44100)
    with wave.open(str(pending), "rb") as audio:
        assert audio.getnframes() <= slot_frames + 1


def prepare_tail_session(session):
    """Actual failed row 26 and saved neighbouring measured WAV lengths."""
    session.total_duration = 48.
    session._chunked_source_started = True
    session._source_prepared_seconds = 48.
    specs = [
        (23, 33.45, 34.25, 33.23654166666667, 34.025083333333335, .7885416666666667),
        (24, 34.25, 35.73, 34.025083333333335, 35.728, 1.7029166666666666),
        (25, 35.73, 36.67, 35.728, 37.02, 1.292),
        (26, 36.67, 37.57, 37.02, 37.92, .4),
        (27, 38.63, 39.53, None, None, .8947916666666667),
    ]
    rows = {}
    for identity, start, end, dub_start, dub_end, audio_duration in specs:
        row = SegmentItem(identity, start, end, end - start)
        row.status = "READY"
        row.text_zh = "你还哭了一下午" if identity == 26 else "来源"
        row.final_vi = "Cũ." if identity == 26 else f"Câu {identity}."
        row.dub_start, row.dub_end = dub_start, dub_end
        row.audio_path = str(session.segments_dir / f"seg_{identity}.wav")
        row.audio_url = f"/api/streaming/audio/{session.task_id}/{identity}?rev=0"
        write_pcm(row.audio_path, audio_duration)
        rows[identity] = row
    session.segments = rows
    return rows[26]


def synthesize_tail_fixture(*, text, output_path, **kwargs):
    """Complete already-fitted synthetic narration, not a live provider result."""
    measured = 1.67 / 1.15
    write_pcm(output_path, measured)
    return {"text": text, "tts_duration": 1.67, "speed_ratio": 1.15,
            "boundaries": [], "pacing_verification": None}


def test_tail_fit_passes_only_proven_capacity_and_does_not_publish_before_return(session, monkeypatch):
    focus = prepare_tail_session(session)
    before = snapshot(session)
    calls = []

    def synthesis(**kwargs):
        calls.append(kwargs)
        return synthesize_tail_fixture(**kwargs)

    monkeypatch.setattr(pipeline, "synthesize_natural_speech", synthesis)
    pending = session.segments_dir / "tail-fit.wav"
    spoken, timing, plan = asyncio.run(session._fit_dub(
        focus, text="Bố còn khóc cả buổi chiều.", source=focus.text_zh, output_path=pending))
    assert calls[0]["duration"] == pytest.approx(.9)
    assert calls[0]["max_duration"] == pytest.approx(1.6750833333333333, abs=.0001)
    assert calls[0]["max_duration_limit"] == calls[0]["max_duration"]
    assert calls[0]["allow_bidirectional_reflow"] is True
    assert plan[26]["dub_tail_limit"] == pytest.approx(38.57)
    assert plan[26]["dub_start"] == pytest.approx(37.02)
    assert plan[26]["dub_end"] < session.segments[27].start
    assert spoken["text"] == "Bố còn khóc cả buổi chiều."
    assert timing["speech_start"] >= plan[26]["dub_start"]
    assert timing["speech_end"] <= plan[26]["dub_end"]
    assert snapshot(session) == before


def test_tail_synthesis_and_save_reopen_preserve_ceiling_audio_and_source(session, monkeypatch):
    focus = prepare_tail_session(session)
    focus.final_vi = "Bố còn khóc cả buổi chiều."
    before = snapshot(session)
    monkeypatch.setattr(pipeline, "synthesize_natural_speech", synthesize_tail_fixture)
    asyncio.run(session._synthesize_segment(focus))
    assert focus.status == "READY"
    assert focus.dub_tail_limit == pytest.approx(38.57)
    assert focus.dub_start == pytest.approx(37.02)
    assert 38.45 < focus.dub_end < min(38.57, session.segments[27].start)
    assert focus.final_vi == "Bố còn khóc cả buổi chiều."
    assert focus.speed_ratio == 1.15
    assert focus.subtitle_cues
    assert all(cue["start"] >= focus.speech_start for cue in focus.subtitle_cues)
    assert all(cue["end"] <= focus.speech_end for cue in focus.subtitle_cues)
    for identity, row in session.segments.items():
        old = before["segments"][identity]
        assert (row.start, row.end, row.duration) == (old["start"], old["end"], old["duration"])
        if identity != 26:
            assert Path(row.audio_path).read_bytes() == before["audio"][row.audio_path]
    audio = Path(focus.audio_path).read_bytes()
    save_session(session)
    for _ in range(2):
        active_streaming_sessions.clear()
        restored = restore_saved_session(session.task_id)
        row = restored.segments[26]
        assert row.dub_tail_limit == focus.dub_tail_limit
        assert (row.dub_start, row.dub_end) == (focus.dub_start, focus.dub_end)
        assert (row.start, row.end, row.duration) == (36.67, 37.57, pytest.approx(.9))
        assert row.subtitle_cues == focus.subtitle_cues
        assert Path(row.audio_path).read_bytes() == audio
        save_session(restored)


@pytest.mark.parametrize("stage", ["synthesis", "speech_timing"])
def test_stop_during_tail_edit_cannot_publish_ceiling_or_replace_old_audio(session, monkeypatch, stage):
    focus = prepare_tail_session(session)
    before = snapshot(session)
    manifest = session.persist()
    persisted = manifest.read_bytes()
    original_timing = pipeline.build_speech_timing

    def synthesis(**kwargs):
        result = synthesize_tail_fixture(**kwargs)
        if stage == "synthesis":
            session.is_stopped = True
        return result

    def timing(*args, **kwargs):
        result = original_timing(*args, **kwargs)
        if stage == "speech_timing":
            session.is_stopped = True
        return result

    monkeypatch.setattr(pipeline, "synthesize_natural_speech", synthesis)
    monkeypatch.setattr(pipeline, "build_speech_timing", timing)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(session.edit_segment(focus.id, "Bố còn khóc cả buổi chiều."))
    assert snapshot(session) == before
    assert manifest.read_bytes() == persisted
    assert focus.dub_tail_limit is None
    assert not list(session.segments_dir.glob("edit_*.wav"))
    assert not list(session.segments_dir.glob("pending_*.wav"))


@pytest.mark.parametrize("stage", ["synthesis", "speech_timing"])
def test_tail_fit_failure_cannot_publish_ceiling_or_replace_old_audio(session, monkeypatch, stage):
    focus = prepare_tail_session(session)
    before = snapshot(session)
    manifest = session.persist()
    persisted = manifest.read_bytes()
    original_timing = pipeline.build_speech_timing

    def synthesis(**kwargs):
        result = synthesize_tail_fixture(**kwargs)
        if stage == "synthesis":
            raise RuntimeError("Offline TTS failure after temporary waveform creation")
        return result

    def timing(*args, **kwargs):
        result = original_timing(*args, **kwargs)
        if stage == "speech_timing":
            raise RuntimeError("Offline timing failure")
        return result

    monkeypatch.setattr(pipeline, "synthesize_natural_speech", synthesis)
    monkeypatch.setattr(pipeline, "build_speech_timing", timing)
    with pytest.raises(RuntimeError, match="Offline"):
        asyncio.run(session.edit_segment(focus.id, "Bố còn khóc cả buổi chiều."))
    assert snapshot(session) == before
    assert manifest.read_bytes() == persisted
    assert focus.dub_tail_limit is None
    assert not list(session.segments_dir.glob("edit_*.wav"))


@pytest.mark.parametrize("outcome", ["stop", "failure"])
def test_automatic_tail_synthesis_aborted_after_fitting_keeps_old_media(session, monkeypatch, outcome):
    focus = prepare_tail_session(session)
    focus.final_vi = "Bố còn khóc cả buổi chiều."
    before = snapshot(session)
    original_timing = pipeline.build_speech_timing
    monkeypatch.setattr(pipeline, "synthesize_natural_speech", synthesize_tail_fixture)

    def timing(*args, **kwargs):
        result = original_timing(*args, **kwargs)
        if outcome == "stop":
            session.is_stopped = True
        else:
            raise RuntimeError("Offline automatic tail timing failure")
        return result

    monkeypatch.setattr(pipeline, "build_speech_timing", timing)
    with pytest.raises(asyncio.CancelledError if outcome == "stop" else RuntimeError):
        asyncio.run(session._synthesize_segment(focus))
    assert focus.status != "READY"
    assert focus.dub_tail_limit is None
    for identity, row in session.segments.items():
        original = before["segments"][identity]
        for field in ("start", "end", "duration", "dub_start", "dub_end", "dub_tail_limit",
                      "subtitle_cues", "speech_start", "speech_end", "audio_path", "audio_url"):
            assert getattr(row, field) == original[field]
        assert Path(row.audio_path).read_bytes() == before["audio"][row.audio_path]
    assert not list(session.segments_dir.glob("pending_*.wav"))


def prepare_dense_rescue(session, focus_id=7):
    # Measured timings from the real 320 s failure; PCM is an offline fixture.
    specs = [
        (4, 7.66, 8.68, 7.6040416667, 9.03, 1.4259583333, 1.639875, 1.15),
        (5, 8.68, 9.4, 9.03, 9.761625, .731625, .841375, 1.15),
        (6, 10.17, 11.01, 9.8754583333, 11.2479166667, 1.3724583333, 1.567458, 1.15),
        (7, 11.01, 12.21, 11.2479166667, 12.4479166667, None, 0, 1),
        (8, 12.21, 13.45, 12.4479166667, 13.6706666667, 1.22275, 1.23, 1),
        (9, 13.45, 14.19, 13.6706666667, 14.7734166667, 1.10275, 1.255542, 1.15),
        (10, 15, 15.48, 14.7734166667, 15.83, 1.0565833333, 1.215083, 1.15),
        (11, 15.48, 16.96, 15.83, 17.31, 1.3333333333, 1.34, 1),
    ]
    rows = {}
    for sid, start, end, left, right, audio, raw, speed in specs:
        row = SegmentItem(sid, start, end, end - start)
        row.status = "READY" if audio else "FAILED"
        row.dub_start, row.dub_end = left, right
        if sid == 5:
            row.dub_tail_limit = 10.17
        elif sid == 9:
            row.dub_tail_limit = 15.
        row.text_zh = "今年19。" if sid == 6 else "野哥网友" if sid == 7 else "来源"
        row.final_vi = "Năm nay mười chín tuổi." if sid == 6 else "Bạn trên mạng của anh Dã." if sid == 7 else "Câu thoại."
        row.verification = {"status": "verified", "semantic_verified": True}
        row.tts_duration, row.speed_ratio = raw, speed
        if audio:
            row.audio_path = str(session.segments_dir / f"seg_{sid}.wav")
            write_pcm(row.audio_path, audio)
        rows[sid] = row
    session.segments = rows
    session.total_duration = session._source_prepared_seconds = 31.
    session._chunked_source_started = True
    from unittest.mock import Mock
    session.translator = Mock()
    return rows[focus_id]


def test_dense_failure_can_stage_verified_neighbor_without_exceeding_bounds(session, monkeypatch):
    focus = prepare_dense_rescue(session)
    before = snapshot(session)
    shorter = "Năm nay mười chín."
    proof = {"status": "verified", "text": shorter, "provider": "opencode", "address_preserved": True,
             "reason": "Giữ năm nay và tuổi mười chín, câu trung tính đủ nghĩa."}
    session.translator.rewrite_for_pacing.return_value = {"final_vi": shorter, "pacing_verification": proof}
    calls = []
    def synthesize(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            error = pipeline.PacingReviewRejected("Focus cannot shorten safely")
            error.required_dub_duration = 1.48
            raise error
        measured = 1.0515 if kwargs["text"] == shorter else 1.465
        write_pcm(kwargs["output_path"], measured)
        return {"text": kwargs["text"], "tts_duration": measured * 1.15,
                "speed_ratio": 1.15, "boundaries": [], "pacing_verification": None}
    monkeypatch.setattr(pipeline, "synthesize_natural_speech", synthesize)
    asyncio.run(session._synthesize_segment(focus))
    assert focus.status == "READY" and session.segments[6].final_vi == shorter
    assert session.segments[6].verification["pacing"] == proof
    assert session.translator.rewrite_for_pacing.call_count == 1
    previous_end = 0
    for row in session.segments.values():
        start, end = pipeline.resolve_dub_timing(row.to_dict())
        assert start >= previous_end - 1e-8
        assert session._dub_audio_duration(row.audio_path) <= end - start + 1e-8
        assert row.speed_ratio <= 1.15
        assert (row.start, row.end) == (before["segments"][row.id]["start"], before["segments"][row.id]["end"])
        previous_end = end
    session.persist()
    restored = restore_saved_session(session.task_id)
    assert restored.segments[6].audio_path == session.segments[6].audio_path
    assert restored.segments[6].final_vi == shorter
    assert restored.segments[7].status == "READY"
    assert not list(session.segments_dir.glob("pending_*.wav"))


@pytest.mark.parametrize("failure", ["manual", "rejected", "cancelled", "stale"])
def test_uncommitted_dense_rescue_preserves_neighbors(session, monkeypatch, failure):
    focus = prepare_dense_rescue(session)
    if failure == "manual":
        session.segments[6].verification["status"] = "manual"
    before = snapshot(session)
    shorter = "Năm nay mười chín."
    session.translator.rewrite_for_pacing.side_effect = pipeline.PacingReviewRejected("Unsafe neighbor") if failure == "rejected" else None
    session.translator.rewrite_for_pacing.return_value = {"final_vi": shorter, "pacing_verification": {"status":"verified", "text":shorter}}
    calls = []
    def synthesize(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            error = pipeline.PacingReviewRejected("Cannot shorten")
            error.required_dub_duration = 1.48
            raise error
        measured = 1.0515 if kwargs["text"] == shorter else 1.465
        write_pcm(kwargs["output_path"], measured)
        if failure == "cancelled":
            session.is_stopped = True
        if failure == "stale":
            focus.revision += 1
        return {"text": kwargs["text"], "tts_duration": measured * 1.15,
                "speed_ratio":1.15, "boundaries":[], "pacing_verification":None}
    monkeypatch.setattr(pipeline, "synthesize_natural_speech", synthesize)
    expected = asyncio.CancelledError if failure == "cancelled" else pipeline.SegmentEditConflict if failure == "stale" else pipeline.PacingReviewRejected
    with pytest.raises(expected):
        asyncio.run(session._synthesize_segment(focus))
    for sid, old in before["segments"].items():
        if sid != focus.id:
            assert vars(session.segments[sid]) == old
            assert Path(session.segments[sid].audio_path).read_bytes() == before["audio"][session.segments[sid].audio_path]
    assert sorted(path.name for path in session.segments_dir.glob("*.wav")) == sorted(Path(path).name for path in before["audio"])
