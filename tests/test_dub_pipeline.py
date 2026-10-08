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
