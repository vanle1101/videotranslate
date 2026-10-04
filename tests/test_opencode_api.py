"""OpenCode API contracts without accessing real credentials or the network."""

import subprocess
from unittest.mock import Mock

import pytest
import requests
from fastapi.testclient import TestClient

import main
from core.engines.translation import opencode_client


@pytest.fixture
def local_api(monkeypatch):
    """Keep every setting, credential, and file change local to each test."""
    monkeypatch.setattr(main.settings, "LLM_PROVIDER", "free")
    monkeypatch.setattr(main.settings, "OPENCODE_MODEL", "big-pickle")
    monkeypatch.setattr(main.settings, "GEMINI_API_KEY", "fixture-gemini-key")
    monkeypatch.setattr(main.settings, "DEEPSEEK_API_KEY", "fixture-deepseek-key")
    monkeypatch.setenv("GEMINI_API_KEY", "fixture-env-gemini-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-env-deepseek-key")
    monkeypatch.setattr(main, "resolve_api_key", lambda: "fixture-opencode-key")
    monkeypatch.setattr(main, "resolve_openrouter_key", lambda: None)
    monkeypatch.setattr(main, "find_opencode_executable", lambda: "fixture-opencode.exe")
    monkeypatch.setattr(opencode_client, "resolve_api_key", lambda explicit=None: "fixture-opencode-key")
    monkeypatch.setattr(
        requests.Session,
        "request",
        Mock(side_effect=AssertionError("Tests must never contact OpenCode")),
    )
    for launch in ("run", "Popen"):
        monkeypatch.setattr(
            subprocess, launch,
            Mock(side_effect=AssertionError("API tests must never launch OpenCode")),
        )
    persist = Mock()
    monkeypatch.setattr(main, "update_env_file", persist)
    with TestClient(main.app) as client:
        yield client, persist


def test_settings_reports_configuration_without_returning_credentials(local_api):
    client, persist = local_api
    response = client.get("/api/settings")

    assert response.status_code == 200
    body = response.json()
    assert body["opencode_configured"] is True
    assert body["opencode_cli_available"] is True
    assert body["gemini_configured"] is True
    assert body["deepseek_configured"] is True
    assert body["opencode_model"] == "big-pickle"
    assert set(body["opencode_free_models"]) == opencode_client.FREE_CHAT_MODELS
    assert not {"opencode_key", "gemini_key", "deepseek_key", "api_key"}.intersection(body)
    assert "fixture-" not in response.text
    persist.assert_not_called()


def test_settings_reports_missing_credentials(local_api, monkeypatch):
    client, _ = local_api
    monkeypatch.setattr(main, "resolve_api_key", lambda: None)
    monkeypatch.setattr(main.settings, "GEMINI_API_KEY", "")
    monkeypatch.setattr(main.settings, "DEEPSEEK_API_KEY", "")
    monkeypatch.delenv("GEMINI_API_KEY")
    monkeypatch.delenv("DEEPSEEK_API_KEY")

    body = client.get("/api/settings").json()

    assert body["opencode_configured"] is False
    assert body["gemini_configured"] is False
    assert body["deepseek_configured"] is False


def test_settings_saves_only_provider_and_canonical_free_model(local_api):
    client, persist = local_api

    response = client.post("/api/settings", json={
        "llm_provider": "opencode",
        "opencode_model": "opencode/space-bunny-free",
    })

    assert response.status_code == 200
    assert main.settings.LLM_PROVIDER == "opencode"
    assert main.settings.OPENCODE_MODEL == "space-bunny-free"
    persist.assert_called_once_with({
        "LLM_PROVIDER": "opencode",
        "OPENCODE_MODEL": "space-bunny-free",
    })
    assert "fixture-" not in response.text


@pytest.mark.parametrize("invalid_fields", [
    {"llm_provider": "opencode", "opencode_model": "gpt-5"},
    {"llm_provider": "unknown", "opencode_model": "space-bunny-free"},
])
def test_invalid_provider_or_paid_model_is_rejected_before_any_mutation(local_api, invalid_fields):
    client, persist = local_api

    response = client.post("/api/settings", json={
        **invalid_fields,
        "gemini_key": "replacement-gemini-key",
        "deepseek_key": "replacement-deepseek-key",
    })

    assert response.status_code == 422
    assert main.settings.LLM_PROVIDER == "free"
    assert main.settings.OPENCODE_MODEL == "big-pickle"
    assert main.settings.GEMINI_API_KEY == "fixture-gemini-key"
    assert main.settings.DEEPSEEK_API_KEY == "fixture-deepseek-key"
    persist.assert_not_called()
    assert "replacement-" not in response.text


def test_connection_success_returns_metadata_only(local_api, monkeypatch):
    client, persist = local_api
    provider = Mock(model="big-pickle")
    provider.translate.return_value = "Unexpected response with fixture-opencode-key"
    constructor = Mock(return_value=provider)
    monkeypatch.setattr(main, "OpenCodeZenClient", constructor)

    response = client.post("/api/test-opencode")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["model"] == "big-pickle"
    assert isinstance(body["latency_ms"], int)
    assert body["latency_ms"] >= 0
    assert set(body) == {"ok", "model", "latency_ms"}
    provider.translate.assert_called_once_with("Reply with OK.", max_tokens=16)
    assert constructor.call_args.kwargs["max_retries"] == 0
    assert "fixture-" not in response.text
    persist.assert_not_called()


def test_failed_persistence_does_not_change_active_provider(local_api):
    client, persist = local_api
    persist.side_effect = OSError("fixture-private-file-detail")
    response = client.post("/api/settings", json={"llm_provider": "opencode"})
    assert response.status_code == 500
    assert main.settings.LLM_PROVIDER == "free"
    assert "fixture-" not in response.text


@pytest.mark.parametrize("failure_at", ["construction", "request"])
def test_connection_hides_arbitrary_exception_details(local_api, monkeypatch, failure_at):
    client, _ = local_api
    private_details = "Authorization: Bearer fixture-opencode-key at C:/private/auth.json"
    provider = Mock(model="big-pickle")
    constructor = Mock(return_value=provider)
    if failure_at == "construction":
        constructor.side_effect = RuntimeError(private_details)
    else:
        provider.translate.side_effect = RuntimeError(private_details)
    monkeypatch.setattr(main, "OpenCodeZenClient", constructor)

    response = client.post("/api/test-opencode")

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["error"]
    assert "fixture-" not in response.text
    assert "Authorization" not in response.text
    assert "auth.json" not in response.text


def test_connection_returns_actionable_safe_client_error(local_api, monkeypatch):
    client, _ = local_api
    provider = Mock(model="big-pickle")
    provider.translate.side_effect = main.OpenCodeClientError("OpenCode rate limit reached; try again later")
    monkeypatch.setattr(main, "OpenCodeZenClient", Mock(return_value=provider))

    response = client.post("/api/test-opencode")

    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "error": "OpenCode rate limit reached; try again later",
    }
