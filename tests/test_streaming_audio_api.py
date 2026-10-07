"""Real HTTP audio reads must stay inside the loaded segment's owned cache."""
import os
import wave
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

import main
from config import settings
from core.streaming.pipeline import SegmentItem, StreamingPipelineSession, active_streaming_sessions


@pytest.fixture
def audio_api(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    path = tmp_path / "workspace/cache/audio-test/segments/seg_0.wav"
    path.parent.mkdir(parents=True)
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x01\x00" * 96)
    segment = SimpleNamespace(status="READY", audio_path=str(path))
    sessions = {"audio-test": SimpleNamespace(segments={0: segment})}
    monkeypatch.setattr(main, "get_streaming_session", sessions.get)
    with TestClient(main.app) as client:
        yield client, path, segment, sessions


def test_ready_audio_preserves_bytes_range_and_no_store(audio_api):
    client, path, segment, _ = audio_api
    for status in ("READY", "PLAYED"):
        segment.status = status
        response = client.get("/api/streaming/audio/audio-test/0?rev=3")
        assert response.status_code == 200
        assert response.content == path.read_bytes()
        assert response.headers["content-type"] == "audio/wav"
        assert response.headers["cache-control"] == "no-store"
    part = client.get("/api/streaming/audio/audio-test/0", headers={"Range": "bytes=0-43"})
    assert part.status_code == 206 and part.content == path.read_bytes()[:44]


@pytest.mark.skipif(os.name != "nt", reason="Windows treats backslash as a filesystem separator")
def test_encoded_backslash_cannot_read_outside_cache(audio_api):
    client, path, _, _ = audio_api
    outside = settings.BASE_DIR / "outside/segments/seg_0.wav"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(path.read_bytes())
    response = client.get("/api/streaming/audio/..%5C..%5Coutside/0")
    assert response.status_code == 404
    assert response.content != outside.read_bytes()


@pytest.mark.parametrize("task_id", ["..", ".", "a:b", "a\\b", "x" * 81])
def test_invalid_task_ids_are_not_media_paths(audio_api, task_id):
    client, *_ = audio_api
    assert client.get(f"/api/streaming/audio/{quote(task_id, safe='')}/0").status_code == 404


@pytest.mark.parametrize("damage", ["orphan", "unknown_segment", "pending", "no_audio", "foreign_audio", "missing", "empty", "directory"])
def test_unowned_or_unready_audio_is_not_served(audio_api, damage):
    client, path, segment, sessions = audio_api
    if damage == "orphan":
        sessions.clear()
    elif damage == "unknown_segment":
        sessions["audio-test"].segments.clear()
    elif damage == "pending":
        segment.status = "TTS"
    elif damage == "no_audio":
        segment.audio_path = None
    elif damage == "foreign_audio":
        segment.audio_path = str(path.parent / "other.wav")
    else:
        path.unlink()
        if damage == "empty":
            path.write_bytes(b"")
        elif damage == "directory":
            path.mkdir()
    assert client.get("/api/streaming/audio/audio-test/0").status_code == 404


@pytest.mark.parametrize("segment_id", [-1, 2147483648])
def test_invalid_segment_ids_are_rejected(audio_api, segment_id):
    client, path, segment, sessions = audio_api
    renamed = path.with_name(f"seg_{segment_id}.wav")
    path.rename(renamed)
    segment.audio_path = str(renamed)
    sessions["audio-test"].segments[segment_id] = segment
    assert client.get(f"/api/streaming/audio/audio-test/{segment_id}").status_code == 404


@pytest.mark.parametrize("level", ["file", "segments", "task", "cache", "workspace"])
def test_windows_reparse_points_are_rejected_at_every_owned_level(audio_api, monkeypatch, level):
    client, path, _, _ = audio_api
    suspect = {"file": path, "segments": path.parent, "task": path.parents[1],
               "cache": path.parents[2], "workspace": path.parents[3]}[level]
    original = Path.lstat
    def flagged(candidate, *args, **kwargs):
        info = original(candidate, *args, **kwargs)
        if candidate == suspect:
            return SimpleNamespace(st_mode=info.st_mode, st_size=info.st_size, st_file_attributes=0x400)
        return info
    monkeypatch.setattr(Path, "lstat", flagged)
    assert client.get("/api/streaming/audio/audio-test/0").status_code == 404


@pytest.fixture
def persisted_audio(tmp_path, monkeypatch):
    paths = {
        "BASE_DIR": tmp_path,
        "WORKSPACE_DIR": tmp_path / "workspace",
        "INPUT_DIR": tmp_path / "workspace" / "inputs",
        "OUTPUT_DIR": tmp_path / "workspace" / "outputs",
        "TEMP_DIR": tmp_path / "workspace" / "temp",
    }
    for name, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(settings, name, path)

    source = settings.INPUT_DIR / "source.mp4"
    source.write_bytes(b"saved source")
    session = StreamingPipelineSession("restored-audio", source)
    session.initialized = True
    session.total_duration = 1
    segment = SegmentItem(0, 0, 1, 1)
    segment.status = "READY"
    segment.text_zh = "你好"
    segment.final_vi = "Xin chào."
    segment.audio_path = str(session.segments_dir / "seg_0.wav")
    with wave.open(segment.audio_path, "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x01\x00" * 96)
    session.segments[0] = segment
    session.persist()

    previous = dict(active_streaming_sessions)
    active_streaming_sessions.clear()
    try:
        yield session
    finally:
        active_streaming_sessions.clear()
        active_streaming_sessions.update(previous)


def test_history_restore_then_audio_range_uses_persisted_wav(persisted_audio):
    with TestClient(main.app) as client:
        snapshot = client.get("/api/streaming/restored-audio")
        assert snapshot.status_code == 200
        assert snapshot.json()["segments"][0]["status"] == "READY"

        full = client.get("/api/streaming/audio/restored-audio/0")
        assert full.status_code == 200
        assert full.headers["content-type"] == "audio/wav"
        assert len(full.content) > 44

        part = client.get("/api/streaming/audio/restored-audio/0",
                          headers={"Range": "bytes=0-43"})
        assert part.status_code == 206
        assert part.content == full.content[:44]
