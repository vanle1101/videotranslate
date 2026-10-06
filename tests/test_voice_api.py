"""Voice routing and preview lifecycle; no network/model inference."""
import logging
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

for _name in ("app", "ai", "pipeline", "errors"):
    logging.getLogger(_name).addHandler(logging.NullHandler())

import main
import core.voice_preview as preview
import core.voice_catalog as catalog
from config import settings


@pytest.fixture
def api(monkeypatch):
    with TemporaryDirectory(prefix="studio-voice-tests-") as directory:
        root = Path(directory)
        monkeypatch.setattr(settings, "INPUT_DIR", root)
        monkeypatch.setattr(preview.VieNeuEngine, "is_available", property(lambda self: True))
        monkeypatch.setattr(catalog, "_vieneu_presets", lambda: (
            {"name": "Trúc Ly", "description": "Nữ", "aliases": (), "featured": 1},))
        monkeypatch.setattr(catalog, "_vieneu_available", lambda: True)
        session = SimpleNamespace(initial_buffer_seconds=10, start=AsyncMock(),
                                  start_from_url=AsyncMock(), get_progress=Mock(return_value={}))
        create = Mock(return_value=session)
        monkeypatch.setattr(main, "create_streaming_session", create)
        monkeypatch.setattr(preview.VoicePreviewManager, "_engine_revision",
                            staticmethod(lambda engine: {"engine": engine, "revision": "test-runtime-1"}))
        manager = preview.VoicePreviewManager(cache_dir=root / "previews")
        monkeypatch.setattr(main, "voice_preview_manager", manager)
        with TestClient(main.app) as client:
            yield client, root, create, manager
        manager.shutdown()


def test_catalog_has_real_sources_and_does_not_initialize_model(api, monkeypatch):
    client, _, _, _ = api
    monkeypatch.setattr(preview.VieNeuEngine, "_ensure_loaded", Mock(side_effect=AssertionError("loaded")))
    result = client.get("/api/voices")
    assert result.status_code == 200
    data = result.json()
    assert any(v["id"] == "vieneu:Trúc Ly" and v["offline"] for v in data["voices"])
    assert any(v["id"] == data["default_voice_id"] for v in data["voices"])


@pytest.mark.parametrize("source", ["url", "upload", "local-file"])
def test_every_start_source_routes_catalog_voice_to_its_engine(api, source):
    client, root, create, _ = api
    if source == "upload":
        result = client.post("/api/streaming/start-upload", data={"voice_id": "vieneu:Trúc Ly"},
                             files={"file": ("source.mp4", b"fixture", "video/mp4")})
    else:
        video = root / "source.mp4"
        video.write_bytes(b"fixture")
        payload = {"voice_id": "vieneu:Trúc Ly"}
        payload.update({"url": "https://v.douyin.com/_lAiSDH0bK8/"} if source == "url"
                       else {"file_path": str(video)})
        result = client.post(f"/api/streaming/start-{source}", json=payload)
    assert result.status_code == 200, result.text
    assert create.call_args.kwargs["voice"] == "Trúc Ly"
    assert create.call_args.kwargs["tts_engine_name"] == "vieneu-tts"


@pytest.mark.parametrize("fields", [
    {"voice_id": "made-up:voice"},
    {"voice_id": "vieneu:Trúc Ly", "tts_engine": "edge-tts"},
    {"voice_id": "vieneu:Trúc Ly", "voice": "vi-VN-NamMinhNeural"},
])
def test_invalid_voice_rejected_before_upload_or_session_creation(api, fields):
    client, root, create, _ = api
    result = client.post("/api/streaming/start-upload", data=fields,
                         files={"file": ("source.mp4", b"fixture", "video/mp4")})
    assert result.status_code == 422
    create.assert_not_called()
    assert not list(root.iterdir())


def test_missing_vieneu_does_not_silently_fall_back_to_edge(api, monkeypatch):
    client, _, create, _ = api
    monkeypatch.setattr(preview.VieNeuEngine, "is_available", property(lambda self: False))
    result = client.post("/api/streaming/start-url", json={
        "url": "https://v.douyin.com/_lAiSDH0bK8/", "voice_id": "vieneu:Trúc Ly"})
    assert result.status_code == 422
    create.assert_not_called()


def sample_synth(monkeypatch):
    paths = []
    def synth(self, text, output, voice):
        assert voice == "Trúc Ly"
        assert text == preview.SAMPLE_TEXT
        paths.append(output)
        with wave.open(str(output), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\x01\x01\xff\xfe" * 8000)
    monkeypatch.setattr(preview.VieNeuEngine, "synthesize", synth)
    return paths


def test_preview_audio_is_playable_bytes_and_deleted_on_request(api, monkeypatch):
    client, _, _, _ = api
    paths = sample_synth(monkeypatch)
    response = client.post("/api/voices/preview", json={"voice_id": "vieneu:Trúc Ly"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["cached"] is False
    audio = client.get(data["audio_url"])
    assert audio.status_code == 200 and audio.content[:4] == b"RIFF"
    assert audio.headers["content-type"] == "audio/wav"
    assert audio.headers["cache-control"] == "no-store"
    assert not paths[0].parent.exists()
    assert client.delete(f"/api/voices/preview/{data['preview_id']}").status_code == 200
    assert client.get(data["audio_url"]).status_code == 404
    repeated = client.post("/api/voices/preview", json={"voice_id": "vieneu:Trúc Ly"}).json()
    assert repeated["cached"] is True
    assert repeated["preview_id"] != data["preview_id"]
    assert client.get(repeated["audio_url"]).content == audio.content
    assert len(paths) == 1


def test_preview_failure_cleans_temp_and_does_not_expose_internal_error(api, monkeypatch):
    client, _, _, _ = api
    paths = []
    def fail(self, text, output, voice):
        paths.append(output)
        output.write_bytes(b"partial")
        raise RuntimeError("internal path/private diagnostic")
    monkeypatch.setattr(preview.VieNeuEngine, "synthesize", fail)
    response = client.post("/api/voices/preview", json={"voice_id": "vieneu:Trúc Ly"})
    assert response.status_code == 503
    assert "private diagnostic" not in response.text
    assert not paths[0].parent.exists()


def test_preview_busy_bounded_cache_expiry_and_shutdown(api, monkeypatch):
    client, _, _, manager = api
    sample_synth(monkeypatch)
    manager._synthesis_lock.acquire()
    try:
        assert client.post("/api/voices/preview", json={"voice_id": "vieneu:Trúc Ly"}).status_code == 409
    finally:
        manager._synthesis_lock.release()
    ids = [manager.create("vieneu:Trúc Ly")["preview_id"] for _ in range(manager.max_samples + 1)]
    assert len(manager._samples) == manager.max_samples
    with pytest.raises(KeyError):
        manager.audio(ids[0])
    monkeypatch.setattr(manager, "lifetime_seconds", 0)
    with pytest.raises(KeyError):
        manager.audio(ids[-1])
    manager.shutdown()
    assert not manager._samples
    assert client.post("/api/voices/preview", json={"voice_id": "vieneu:Trúc Ly"}).status_code == 409


def test_disk_sample_survives_session_delete_and_manager_restart(api, monkeypatch):
    _, _, _, manager = api
    paths = sample_synth(monkeypatch)
    first = manager.create("vieneu:Trúc Ly")
    expected = manager.audio(first["preview_id"])
    manager.delete(first["preview_id"])
    manager.shutdown()
    restarted = preview.VoicePreviewManager(cache_dir=manager.cache_dir)
    try:
        # Canonical legacy IDs and catalog IDs must address the same sample.
        result = restarted.create("Trúc Ly")
        assert result["cached"] is True
        assert restarted.audio(result["preview_id"]) == expected
        assert len(paths) == 1
    finally:
        restarted.shutdown()


def test_cached_sample_does_not_wait_for_another_inference(api, monkeypatch):
    _, _, _, manager = api
    paths = sample_synth(monkeypatch)
    manager.create("vieneu:Trúc Ly")
    manager._synthesis_lock.acquire()
    try:
        assert manager.create("vieneu:Trúc Ly")["cached"] is True
    finally:
        manager._synthesis_lock.release()
    assert len(paths) == 1


def test_sample_published_before_lock_acquisition_is_reused(api, monkeypatch):
    _, _, _, manager = api
    paths = sample_synth(monkeypatch)
    initial = manager.create("vieneu:Trúc Ly")
    expected = manager.audio(initial["preview_id"])
    # Reproduce the first read racing the completion of another request.
    lookup = Mock(side_effect=[None, expected])
    monkeypatch.setattr(manager, "_cached_audio", lookup)
    result = manager.create("vieneu:Trúc Ly")
    assert result["cached"] is True
    assert manager.audio(result["preview_id"]) == expected
    assert lookup.call_count == 2
    assert len(paths) == 1


def test_default_cache_is_outside_transient_task_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "WORKSPACE_DIR", tmp_path)
    manager = preview.VoicePreviewManager()
    assert manager.cache_dir == tmp_path / "cache" / "voice_previews"
    assert not manager.cache_dir.exists()


@pytest.mark.parametrize("change", ["text", "schema", "model_runtime"])
def test_cache_identity_invalidates_changed_sample_or_engine(api, monkeypatch, change):
    _, _, _, manager = api
    paths = sample_synth(monkeypatch)
    manager.create("vieneu:Trúc Ly")
    if change == "text":
        monkeypatch.setattr(preview, "SAMPLE_TEXT", "Đây là câu mẫu mới.")
    elif change == "schema":
        monkeypatch.setattr(manager, "cache_schema", manager.cache_schema + 1)
    else:
        monkeypatch.setattr(manager, "_engine_revision", lambda engine: {"revision": "test-runtime-2"})
    assert manager.create("vieneu:Trúc Ly")["cached"] is False
    assert len(paths) == 2
    assert len(list(manager.cache_dir.glob("*.wav"))) == 2


@pytest.mark.parametrize("corruption", ["header_only", "truncated", "silence"])
def test_corrupted_disk_sample_is_regenerated(api, monkeypatch, corruption):
    _, _, _, manager = api
    paths = sample_synth(monkeypatch)
    first = manager.create("vieneu:Trúc Ly")
    expected = manager.audio(first["preview_id"])
    cached = next(manager.cache_dir.glob("*.wav"))
    if corruption == "header_only":
        cached.write_bytes(b"RIFF" + bytes(80))
    elif corruption == "truncated":
        cached.write_bytes(expected[:-100])
    else:
        with wave.open(str(cached), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(bytes(32000))
    result = manager.create("vieneu:Trúc Ly")
    assert result["cached"] is False
    assert manager.audio(result["preview_id"]) == expected
    assert len(paths) == 2
    assert not list(manager.cache_dir.glob("studio-voice-*"))


def test_invalid_generated_pcm_never_becomes_ready_or_cached(api, monkeypatch):
    client, _, _, manager = api
    def bad_synth(self, text, output, voice):
        output.write_bytes(b"RIFF" + bytes(80))
    monkeypatch.setattr(preview.VieNeuEngine, "synthesize", bad_synth)
    response = client.post("/api/voices/preview", json={"voice_id": "vieneu:Trúc Ly"})
    assert response.status_code == 503
    assert not manager._samples
    assert not list(manager.cache_dir.iterdir())


def test_shutdown_during_synthesis_does_not_publish_sample(api, monkeypatch):
    _, _, _, manager = api
    sample_synth(monkeypatch)
    original = preview.VieNeuEngine.synthesize
    def synth_and_close(self, text, output, voice):
        original(self, text, output, voice)
        manager.shutdown()
    monkeypatch.setattr(preview.VieNeuEngine, "synthesize", synth_and_close)
    with pytest.raises(preview.VoicePreviewBusy):
        manager.create("vieneu:Trúc Ly")
    assert not manager._samples
    assert not list(manager.cache_dir.iterdir())


@pytest.mark.parametrize("limit", ["count", "bytes"])
def test_disk_cache_prunes_only_old_owned_samples(api, monkeypatch, limit):
    _, _, _, manager = api
    sample_synth(monkeypatch)
    first = manager.create("vieneu:Trúc Ly")
    sample_size = len(manager.audio(first["preview_id"]))
    oldest = next(manager.cache_dir.glob("*.wav"))
    user_file = manager.cache_dir / "my-recording.wav"
    user_file.write_bytes(b"user-owned recording")
    if limit == "count":
        monkeypatch.setattr(manager, "max_cached_samples", 2)
    else:
        monkeypatch.setattr(manager, "max_cache_bytes", sample_size * 2)
    for index in range(2):
        monkeypatch.setattr(preview, "SAMPLE_TEXT", f"Câu mẫu {index}.")
        assert manager.create("vieneu:Trúc Ly")["cached"] is False
    owned = [path for path in manager.cache_dir.glob("*.wav") if path != user_file]
    assert len(owned) == 2
    assert sum(path.stat().st_size for path in owned) == sample_size * 2
    assert not oldest.exists()
    assert user_file.read_bytes() == b"user-owned recording"
