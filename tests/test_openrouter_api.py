"""Free OpenRouter API contracts, isolated from credentials and network access."""

from unittest.mock import Mock

import pytest
import requests
from fastapi.testclient import TestClient

import main
from core.engines.translation import openrouter_client


MODEL = "inclusionai/ling-3.0-flash-sante:free"


@pytest.fixture
def local_api(monkeypatch):
    monkeypatch.setattr(main.settings, "LLM_PROVIDER", "free")
    monkeypatch.setattr(main.settings, "OPENROUTER_MODEL", MODEL)
    monkeypatch.setattr(main.settings, "GEMINI_API_KEY", "fixture-gemini-key")
    monkeypatch.setattr(main.settings, "DEEPSEEK_API_KEY", "fixture-deepseek-key")
    monkeypatch.setattr(main, "resolve_api_key", lambda: None)
    monkeypatch.setattr(main, "find_opencode_executable", lambda: None)
    monkeypatch.setattr(main, "resolve_openrouter_key", lambda: "fixture-openrouter-key")
    monkeypatch.setattr(openrouter_client, "resolve_openrouter_key", lambda: "fixture-openrouter-key")
    monkeypatch.setattr(requests.Session, "request", Mock(side_effect=AssertionError("No network in API tests")))
    persist = Mock()
    monkeypatch.setattr(main, "update_env_file", persist)
    with TestClient(main.app) as client:
        yield client, persist


def test_settings_reports_free_model_and_configuration_without_key(local_api):
    client, persist = local_api

    response = client.get("/api/settings")

    assert response.status_code == 200
    body = response.json()
    assert body["openrouter_configured"] is True
    assert body["openrouter_model"] == MODEL
    assert not {"openrouter_key", "openrouter_api_key", "api_key"}.intersection(body)
    assert "fixture-" not in response.text
    persist.assert_not_called()


def test_settings_reports_missing_openrouter_credentials(local_api, monkeypatch):
    client, _ = local_api
    monkeypatch.setattr(main, "resolve_openrouter_key", lambda: None)

    assert client.get("/api/settings").json()["openrouter_configured"] is False


def test_settings_persists_free_provider_and_model_without_credentials(local_api):
    client, persist = local_api
    new_model = "provider/model-v2.5:free"

    response = client.post("/api/settings", json={
        "llm_provider": "openrouter-free", "openrouter_model": new_model,
    })

    assert response.status_code == 200
    assert main.settings.LLM_PROVIDER == "openrouter-free"
    assert main.settings.OPENROUTER_MODEL == new_model
    persist.assert_called_once_with({"LLM_PROVIDER": "openrouter-free", "OPENROUTER_MODEL": new_model})
    assert "fixture-" not in response.text


@pytest.mark.parametrize("model", [
    "openai/gpt-5", "openrouter/auto", "provider/paid:free\nGEMINI_API_KEY=replacement", "", ":free",
])
def test_paid_or_malformed_model_rejected_before_any_mutation(local_api, model):
    client, persist = local_api

    response = client.post("/api/settings", json={
        "llm_provider": "openrouter-free", "openrouter_model": model,
        "gemini_key": "replacement-key",
    })

    assert response.status_code == 422
    assert main.settings.LLM_PROVIDER == "free"
    assert main.settings.OPENROUTER_MODEL == MODEL
    assert main.settings.GEMINI_API_KEY == "fixture-gemini-key"
    assert "replacement-key" not in response.text
    persist.assert_not_called()


def test_failed_persistence_preserves_active_provider_and_model(local_api):
    client, persist = local_api
    persist.side_effect = OSError("fixture-private-env-location")

    response = client.post("/api/settings", json={
        "llm_provider": "openrouter-free", "openrouter_model": "provider/alternative:free",
    })

    assert response.status_code == 500
    assert main.settings.LLM_PROVIDER == "free"
    assert main.settings.OPENROUTER_MODEL == MODEL
    assert "fixture-" not in response.text


def test_connection_returns_only_metadata(local_api, monkeypatch):
    client, persist = local_api
    provider = Mock(model=MODEL)
    provider.translate.return_value = "fixture-openrouter-key must never be echoed"
    constructor = Mock(return_value=provider)
    monkeypatch.setattr(main, "OpenRouterFreeClient", constructor)

    response = client.post("/api/test-openrouter")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["model"] == MODEL
    assert isinstance(body["latency_ms"], int)
    assert body["latency_ms"] >= 0
    assert set(body) == {"ok", "model", "latency_ms"}
    provider.translate.assert_called_once_with("Reply with OK.", max_tokens=1024)
    assert constructor.call_args.kwargs["model"] == MODEL
    assert "fixture-" not in response.text
    persist.assert_not_called()


@pytest.mark.parametrize("failure_at", ["construction", "request"])
def test_connection_never_exposes_arbitrary_exception_text(local_api, monkeypatch, failure_at):
    client, _ = local_api
    provider = Mock(model=MODEL)
    constructor = Mock(return_value=provider)
    error = RuntimeError("Authorization: Bearer fixture-openrouter-key at C:/private/auth.json")
    if failure_at == "construction":
        constructor.side_effect = error
    else:
        provider.translate.side_effect = error
    monkeypatch.setattr(main, "OpenRouterFreeClient", constructor)

    response = client.post("/api/test-openrouter")

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["error"]
    assert "fixture-" not in response.text
    assert "Authorization" not in response.text
    assert "auth.json" not in response.text


def test_connection_returns_actionable_safe_client_error(local_api, monkeypatch):
    client, _ = local_api
    provider = Mock(model=MODEL)
    provider.translate.side_effect = main.OpenRouterClientError("OpenRouter free quota exhausted; try later")
    monkeypatch.setattr(main, "OpenRouterFreeClient", Mock(return_value=provider))

    response = client.post("/api/test-openrouter")

    assert response.status_code == 200
    assert response.json() == {"ok": False, "error": "OpenRouter free quota exhausted; try later"}
