"""Muse endpoints never launch a browser during status or transmit real content."""
import asyncio
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import main
from config import settings
from core.services.muse_service import MuseError


@pytest.fixture
def muse(monkeypatch):
    service = Mock()
    state = {"installed": True, "running": True, "logged_in": True, "composer_ready": True}
    service.status.return_value = state.copy()
    service.start_login.return_value = state.copy()
    service.translate.return_value = "OK"
    monkeypatch.setattr(main, "muse_service", service)
    with TestClient(main.app) as client:
        yield service, client


def test_status_only_reads_whitelisted_metadata_in_worker_thread(muse):
    service, client = muse
    def status():
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()
        return {"installed": True, "running": False, "logged_in": False,
                "composer_ready": False, "token": "private-value", "profile_dir": "private-path"}
    service.status.side_effect = status
    result = client.get("/api/muse/status")
    assert result.status_code == 200
    assert result.json() == {"ok": True, "installed": True, "running": False,
                             "logged_in": False, "composer_ready": False, "browser_mode": "dedicated"}
    service.start_login.assert_not_called()
    service.translate.assert_not_called()


def test_login_returns_safe_status_and_does_not_send_prompt(muse):
    service, client = muse
    service.start_login.return_value.update(logged_in=False, composer_ready=False, transcript="private")
    result = client.post("/api/muse/login").json()
    assert result["ok"] and result["running"] and not result["logged_in"]
    assert "transcript" not in result
    service.start_login.assert_called_once()
    service.translate.assert_not_called()


@pytest.mark.parametrize("changed,code", [
    ({"installed": False}, "not_installed"),
    ({"logged_in": False}, "login_required"),
    ({"composer_ready": False}, "not_ready"),
])
def test_probe_requires_ready_logged_in_account_and_never_starts_browser(muse, changed, code):
    service, client = muse
    service.status.return_value.update(changed)
    result = client.post("/api/test-muse").json()
    assert result["ok"] is False and result["error_code"] == code
    service.start_login.assert_not_called()
    service.translate.assert_not_called()


def test_probe_sends_only_synthetic_prompt(muse):
    service, client = muse
    result = client.post("/api/test-muse").json()
    assert result["ok"] is True and result["model"] == "muse-browser"
    assert isinstance(result["latency_ms"], int)
    service.translate.assert_called_once_with("Reply with exactly OK.")


def test_probe_rejects_unexpected_text_without_returning_it(muse):
    service, client = muse
    service.translate.return_value = "private browser content"
    result = client.post("/api/test-muse").json()
    assert result["error_code"] == "unexpected_reply"
    assert "private" not in str(result)


@pytest.mark.parametrize("route,method", [
    ("/api/muse/status", "status"), ("/api/muse/login", "start_login"),
    ("/api/test-muse", "translate"), ("/api/muse/stop", "stop"),
])
def test_failures_do_not_expose_raw_exception_or_browser_data(muse, route, method):
    service, client = muse
    getattr(service, method).side_effect = MuseError("secret-token / private-profile / transcript")
    response = client.get(route) if method == "status" else client.post(route)
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert all(term not in response.text for term in ("secret-token", "private-profile", "transcript"))


def test_stop_only_stops_owned_service_and_returns_status(muse):
    service, client = muse
    service.status.return_value.update(running=False, logged_in=False, composer_ready=False)
    result = client.post("/api/muse/stop").json()
    assert result["ok"] and not result["running"]
    service.stop.assert_called_once()


def test_settings_accept_muse_without_touching_local_secrets(muse, monkeypatch):
    _, client = muse
    save = Mock()
    monkeypatch.setattr(main, "update_env_file", save)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openrouter-free")
    result = client.post("/api/settings", json={"llm_provider": "muse"})
    assert result.status_code == 200
    assert settings.LLM_PROVIDER == "muse"
    save.assert_called_once_with({"LLM_PROVIDER": "muse"})


def test_existing_mode_saved_only_after_service_reconfiguration(muse, monkeypatch):
    service, client = muse
    monkeypatch.setattr(settings, "MUSE_BROWSER_MODE", "dedicated")
    save = Mock()
    monkeypatch.setattr(main, "update_env_file", save)
    def change(mode, apply):
        assert mode == "existing"
        save.assert_not_called()
        apply()
    service.change_browser_mode.side_effect = change
    result = client.post("/api/settings", json={"muse_browser_mode": "existing"})
    assert result.status_code == 200
    assert client.get("/api/settings").json()["muse_browser_mode"] == "existing"
    save.assert_called_once_with({"MUSE_BROWSER_MODE": "existing"})
    service.change_browser_mode.assert_called_once()


def test_invalid_browser_mode_changes_nothing(muse, monkeypatch):
    service, client = muse
    save = Mock()
    monkeypatch.setattr(main, "update_env_file", save)
    result = client.post("/api/settings", json={"muse_browser_mode": "http://external/"})
    assert result.status_code == 422
    save.assert_not_called()
    service.change_browser_mode.assert_not_called()


def test_chrome_permission_error_is_fixed_and_actionable(muse):
    service, client = muse
    service.start_login.side_effect = MuseError("private chrome details", code="chrome_connection_required")
    data = client.post("/api/muse/login").json()
    assert data["error_code"] == "chrome_connection_required"
    assert "chrome://inspect/#remote-debugging" in data["error"]
    assert "private" not in data["error"]


def test_status_allows_only_known_browser_modes(muse):
    service, client = muse
    for value, expected in [("existing", "existing"), ("secret arbitrary value", "dedicated")]:
        service.status.return_value["browser_mode"] = value
        data = client.get("/api/muse/status").json()
        assert data["browser_mode"] == expected
        assert "secret" not in str(data)
