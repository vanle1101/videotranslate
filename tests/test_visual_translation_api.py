"""Visual translation opt-in must reach every source path without starting AI."""
import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

import main
from config import settings


@pytest.fixture
def source_api(tmp_path, monkeypatch):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    source = tmp_path / "video thử.mp4"
    source.write_bytes(b"local video fixture")
    monkeypatch.setattr(settings, "INPUT_DIR", inputs)
    monkeypatch.setattr(settings, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "fixture-key-never-send")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    signature = inspect.signature(main.create_streaming_session)
    session = SimpleNamespace(initial_buffer_seconds=10, start=AsyncMock(), start_from_url=AsyncMock(),
                              get_progress=Mock(return_value={"phase": "resolve", "progress_pct": None}))

    def create_session(**kwargs):
        # A permissive mock must not hide an API/factory signature mismatch.
        signature.bind(**kwargs)
        return session

    factory = Mock(side_effect=create_session)
    normalize = Mock(return_value="https://www.douyin.com/video/123456789")
    monkeypatch.setattr(main, "create_streaming_session", factory)
    monkeypatch.setattr(main.downloader, "normalize_url", normalize)
    return SimpleNamespace(source=source, inputs=inputs, factory=factory, session=session, normalize=normalize)


def post_source(source_api, route, enabled):
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as api:
            payload = {"voice": "vi-VN-HoaiMyNeural", "tts_engine": "edge-tts"}
            if enabled is not None:
                payload["visual_translation"] = enabled
            if route == "upload":
                data = {key: str(value).lower() if isinstance(value, bool) else value for key, value in payload.items()}
                response = await api.post("/api/streaming/start-upload", data=data,
                                          files={"file": ("video.mp4", b"uploaded video fixture", "video/mp4")})
            else:
                payload.update({"url": "copied share https://v.douyin.com/test/"} if route == "url"
                               else {"file_path": str(source_api.source)})
                response = await api.post(f"/api/streaming/start-{'url' if route == 'url' else 'local-file'}", json=payload)
            await asyncio.sleep(0)
            return response
    return asyncio.run(request())


@pytest.mark.parametrize("route", ["url", "local", "upload"])
@pytest.mark.parametrize("enabled", [True, False, None])
def test_visual_choice_reaches_session_for_every_source(source_api, route, enabled):
    response = post_source(source_api, route, enabled)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "started"
    source_api.factory.assert_called_once()
    args = source_api.factory.call_args.kwargs
    assert args["visual_translation"] is bool(enabled)
    if route == "url":
        assert args["video_path"] is None
        source_api.session.start_from_url.assert_awaited_once_with(main.downloader, source_api.normalize.return_value)
    else:
        assert args["video_path"].read_bytes() == (b"local video fixture" if route == "local" else b"uploaded video fixture")
        source_api.session.start.assert_awaited_once()
    assert "fixture-key" not in response.text


@pytest.mark.parametrize("route", ["url", "local", "upload"])
@pytest.mark.parametrize("invalid", ["provider", "missing_key", "blank_key"])
def test_visual_configuration_rejected_before_source_work(source_api, monkeypatch, route, invalid):
    if invalid == "provider":
        monkeypatch.setattr(settings, "LLM_PROVIDER", "muse")
    else:
        monkeypatch.setattr(settings, "GEMINI_API_KEY", "  " if invalid == "blank_key" else "")
    response = post_source(source_api, route, True)
    assert response.status_code == 422, response.text
    assert "Gemini" in response.json()["detail"]
    source_api.factory.assert_not_called()
    source_api.normalize.assert_not_called()
    source_api.session.start.assert_not_awaited()
    source_api.session.start_from_url.assert_not_awaited()
    assert not list(source_api.inputs.iterdir())


@pytest.mark.parametrize("route", ["url", "local", "upload"])
def test_explicit_openrouter_visual_mode_accepts_every_source_without_gemini_key(source_api, monkeypatch, route):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openrouter-free")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    response = post_source(source_api, route, True)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "started"
    source_api.factory.assert_called_once()
    args = source_api.factory.call_args.kwargs
    assert args["visual_translation"] is True
    if route == "url":
        assert args["video_path"] is None
        source_api.session.start_from_url.assert_awaited_once_with(main.downloader, source_api.normalize.return_value)
    else:
        assert args["video_path"].read_bytes() == (b"local video fixture" if route == "local" else b"uploaded video fixture")
        source_api.session.start.assert_awaited_once()
    assert "fixture-key" not in response.text


def test_visual_configuration_accepts_environment_key(source_api, monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "fixture-environment-key")
    response = post_source(source_api, "local", True)
    assert response.status_code == 200
    assert source_api.factory.call_args.kwargs["visual_translation"] is True
    assert "fixture-environment-key" not in response.text


def test_disabled_visual_mode_does_not_require_gemini(source_api, monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openrouter-free")
    response = post_source(source_api, "url", False)
    assert response.status_code == 200
    assert source_api.factory.call_args.kwargs["visual_translation"] is False
