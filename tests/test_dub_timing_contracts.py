"""Narration windows never mutate source timing or silently truncate speech."""
import json
import wave
from pathlib import Path

import pytest

from config import settings
from core.streaming import audio_cache, export, session_store
from core.streaming.export import HQExporter
from core.streaming.pipeline import SegmentItem, StreamingPipelineSession, active_streaming_sessions


def test_absent_or_null_dub_timing_uses_source_without_mutation():
    for extra in ({}, {"dub_start": None, "dub_end": None}):
        row = {"start": .5, "end": 1., **extra}
        before = dict(row)
        assert audio_cache.resolve_dub_timing(row) == (.5, 1.)
        assert row == before


def test_valid_dub_timing_preserves_source_including_boundary_shift():
    row = {"start": 1., "end": 2., "dub_start": .65, "dub_end": 2.35}
    assert audio_cache.resolve_dub_timing(row) == (.65, 2.35)
    assert (row["start"], row["end"]) == (1., 2.)


@pytest.mark.parametrize("values", [
    {"dub_start": .6}, {"dub_end": 1.2},
    {"dub_start": "0.6", "dub_end": 1.2},
    {"dub_start": True, "dub_end": 1.2},
    {"dub_start": .6, "dub_end": False},
    {"dub_start": float("nan"), "dub_end": 1.2},
    {"dub_start": .6, "dub_end": float("inf")},
    {"dub_start": -.01, "dub_end": 1.2},
    {"dub_start": .6, "dub_end": .6},
    {"dub_start": .6, "dub_end": .5},
    {"dub_start": .1, "dub_end": 1.2},
    {"dub_start": .6, "dub_end": 1.35001},
])
def test_invalid_or_unbounded_dub_timing_rejected(values):
    with pytest.raises(ValueError, match="lồng tiếng"):
        audio_cache.resolve_dub_timing({"start": .5, "end": 1., **values})


@pytest.fixture
def decoder(tmp_path, monkeypatch):
    """Honor the actual decode limit so the original slot clipping fails tests."""
    clips = {}
    calls = []

    def make_clip(name, frames):
        path = tmp_path / name
        path.write_bytes(b"wav-input")
        # Nonzero stereo PCM makes placement and complete sample preservation observable.
        clips[str(path)] = b"\x01\x00\x02\x00" * frames
        return str(path)

    def decode(command, cancel_check=None, capture_output=False):
        assert capture_output
        calls.append(command)
        raw = clips[command[command.index("-i") + 1]]
        limit = float(command[command.index("-t") + 1])
        return raw[:round(limit * 44100) * 4]

    monkeypatch.setattr(export, "run_media", decode)
    return make_clip, calls


def test_export_places_complete_voice_at_dub_window_and_keeps_source(tmp_path, decoder):
    make_clip, _ = decoder
    row = {"id": 6, "start": .5, "end": 1., "dub_start": .4, "dub_end": 1.2,
           "audio_path": make_clip("voice.wav", 33075)}
    before = dict(row)
    destination = tmp_path / "result.wav"
    HQExporter._assemble_voice_timeline([row], 2, destination)
    with wave.open(str(destination)) as track:
        assert track.getnframes() == 88200
        assert track.readframes(17640) == bytes(17640 * 4)
        assert track.readframes(33075) == b"\x01\x00\x02\x00" * 33075
        assert track.readframes(88200) == bytes((88200 - 17640 - 33075) * 4)
    assert row == before


@pytest.mark.parametrize("extra_frames", [2, 4410, 44100 * 20])
def test_export_rejects_oversize_audio_instead_of_clipping(tmp_path, decoder, extra_frames):
    make_clip, calls = decoder
    frames = 22050 + extra_frames
    row = {"id": 6, "start": .5, "end": 1., "audio_path": make_clip("long.wav", frames)}
    with pytest.raises(ValueError, match="vượt khung lồng tiếng"):
        HQExporter._assemble_voice_timeline([row], 2, tmp_path / "result.wav")
    assert float(calls[0][calls[0].index("-t") + 1]) == pytest.approx(.6)


def test_export_allows_only_one_sample_rounding_tolerance(tmp_path, decoder):
    make_clip, _ = decoder
    row = {"start": .5, "end": 1., "audio_path": make_clip("rounding.wav", 22051)}
    destination = tmp_path / "result.wav"
    HQExporter._assemble_voice_timeline([row], 2, destination)
    with wave.open(str(destination)) as track:
        assert track.getnframes() == 88200
        track.setpos(22050)
        assert track.readframes(22050) == b"\x01\x00\x02\x00" * 22050
        assert track.readframes(1) == bytes(4)


def test_export_rejects_dub_overlap_between_spoken_lines(tmp_path, decoder):
    make_clip, _ = decoder
    path = make_clip("voice.wav", 33075)
    rows = [
        {"start": .5, "end": 1., "dub_start": .5, "dub_end": 1.3, "audio_path": path},
        {"start": 1., "end": 1.8, "dub_start": 1.1, "dub_end": 1.8, "audio_path": path},
    ]
    with pytest.raises(ValueError, match="chồng lấn"):
        HQExporter._assemble_voice_timeline(rows, 2, tmp_path / "result.wav")


def test_export_rejects_narration_outside_video(tmp_path, decoder):
    make_clip, _ = decoder
    row = {"start": .5, "end": 1., "dub_start": .5, "dub_end": 1.2,
           "audio_path": make_clip("voice.wav", 22050)}
    with pytest.raises(ValueError, match="vượt quá thời lượng"):
        HQExporter._assemble_voice_timeline([row], 1, tmp_path / "result.wav")


def test_export_rejects_empty_decoded_audio(tmp_path, decoder):
    make_clip, _ = decoder
    row = {"start": .5, "end": 1., "audio_path": make_clip("empty.wav", 0)}
    with pytest.raises(ValueError, match="giải mã"):
        HQExporter._assemble_voice_timeline([row], 2, tmp_path / "result.wav")


def test_audio_cache_identity_tracks_dub_placement_with_identical_audio(tmp_path, monkeypatch):
    source, voice = tmp_path / "source.mp4", tmp_path / "voice.wav"
    source.write_bytes(b"source")
    voice.write_bytes(b"voice")
    monkeypatch.setattr(settings, "SEPARATION_ENGINE", "none")
    monkeypatch.setattr(audio_cache, "run_media", lambda *args, **kwargs: b"test ffmpeg version")
    exporter = HQExporter()
    row = {"start": .5, "end": 1., "audio_path": str(voice), "final_vi": "Mười chín."}

    def key():
        identity = audio_cache.build_audio_cache_identity(source, [row], 2, exporter.mixer, exporter.suppressor)
        return identity.key if identity is not None else None

    original = key()
    assert original
    row.update(dub_start=.5, dub_end=1.)
    assert key() == original
    row.update(dub_start=.45, dub_end=1.1)
    assert key() != original
    row.update(dub_start=.1)
    assert key() is None


@pytest.fixture
def saved_project(tmp_path, monkeypatch):
    for name, value in {
        "BASE_DIR": tmp_path, "WORKSPACE_DIR": tmp_path / "workspace",
        "INPUT_DIR": tmp_path / "workspace/inputs", "OUTPUT_DIR": tmp_path / "workspace/outputs",
        "TEMP_DIR": tmp_path / "workspace/temp",
    }.items():
        value.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(settings, name, value)
    source = settings.INPUT_DIR / "clip.mp4"
    source.write_bytes(b"source fixture")
    session = StreamingPipelineSession("dub-contract", source)
    session.initialized, session.total_duration = True, 2.
    segment = SegmentItem(0, .5, 1., .5)
    segment.status, segment.text_zh, segment.final_vi = "READY", "十九", "Mười chín."
    segment.audio_path = str(session.segments_dir / "seg_0.wav")
    with wave.open(segment.audio_path, "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x01\x00" * 100)
    session.segments[0] = segment
    old = dict(active_streaming_sessions)
    active_streaming_sessions.clear()
    yield session
    active_streaming_sessions.clear()
    active_streaming_sessions.update(old)


def test_dub_window_and_cues_round_trip_without_changing_source(saved_project):
    segment = saved_project.segments[0]
    segment.dub_start, segment.dub_end = .4, 1.2
    segment.subtitle_cues = [{"text": "Mười chín.", "start": .4, "end": 1.2}]
    session_store.save_session(saved_project)
    for _ in range(2):
        restored = session_store.restore_saved_session(saved_project.task_id)
        row = restored.segments[0]
        assert (row.start, row.end, row.duration) == (.5, 1., .5)
        assert (row.dub_start, row.dub_end) == (.4, 1.2)
        assert row.subtitle_cues == segment.subtitle_cues
        session_store.save_session(restored)
        active_streaming_sessions.clear()


def test_legacy_manifest_without_dub_fields_restores_original_bounds(saved_project):
    session_store.save_session(saved_project)
    path = session_store._project_path(saved_project.task_id)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["segments"][0].pop("dub_start", None)
    record["segments"][0].pop("dub_end", None)
    path.write_text(json.dumps(record), encoding="utf-8")
    restored = session_store.restore_saved_session(saved_project.task_id)
    row = restored.segments[0]
    assert (row.start, row.end, row.duration) == (.5, 1., .5)
    assert getattr(row, "dub_start", None) is None
    assert getattr(row, "dub_end", None) is None


@pytest.mark.parametrize("values", [
    {"dub_start": .4}, {"dub_start": True, "dub_end": 1.2},
    {"dub_start": "0.4", "dub_end": 1.2},
    {"dub_start": .4, "dub_end": 1.351},
    {"dub_start": .4, "dub_end": .3},
])
def test_corrupt_dub_manifest_is_not_restored(saved_project, values):
    session_store.save_session(saved_project)
    path = session_store._project_path(saved_project.task_id)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["segments"][0].pop("dub_start", None)
    record["segments"][0].pop("dub_end", None)
    record["segments"][0].update(values)
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="lồng tiếng"):
        session_store.restore_saved_session(saved_project.task_id)


def test_save_rejects_invalid_dub_fields_before_replacing_manifest(saved_project):
    session_store.save_session(saved_project)
    path = session_store._project_path(saved_project.task_id)
    before = path.read_bytes()
    saved_project.segments[0].dub_start = .1
    saved_project.segments[0].dub_end = 1.2
    with pytest.raises(ValueError, match="lồng tiếng"):
        session_store.save_session(saved_project)
    assert path.read_bytes() == before
