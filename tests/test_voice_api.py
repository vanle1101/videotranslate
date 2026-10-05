"""Voice routing and preview lifecycle; no network/model inference."""
import struct
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

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
        manager = preview.VoicePreviewManager()
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
        output.write_bytes(b"RIFF" + struct.pack("<I", 56) + b"WAVE" + bytes(52))
    monkeypatch.setattr(preview.VieNeuEngine, "synthesize", synth)
    return paths


def test_preview_audio_is_playable_bytes_and_deleted_on_request(api, monkeypatch):
    client, _, _, _ = api
    paths = sample_synth(monkeypatch)
    response = client.post("/api/voices/preview", json={"voice_id": "vieneu:Trúc Ly"})
    assert response.status_code == 200, response.text
    data = response.json()
    audio = client.get(data["audio_url"])
    assert audio.status_code == 200 and audio.content[:4] == b"RIFF"
    assert audio.headers["content-type"] == "audio/wav"
    assert audio.headers["cache-control"] == "no-store"
    assert not paths[0].parent.exists()
    assert client.delete(f"/api/voices/preview/{data['preview_id']}").status_code == 200
    assert client.get(data["audio_url"]).status_code == 404


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
