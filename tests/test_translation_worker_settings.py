"""Worker preset API and persistence checks; no app process or provider calls."""

import subprocess
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import requests
from fastapi.testclient import TestClient
from pydantic import ValidationError

import main
from config import Settings, TRANSLATION_CHUNK_WORKER_OPTIONS


class WorkerSettingsMarkup(HTMLParser):
    """Read the worker control from the actual server-rendered template."""

    def __init__(self, html):
        super().__init__()
        self.select_attributes = {}
        self.options = []
        self.label = ""
        self.help = ""
        self._in_select = False
        self._in_label = False
        self._in_help = False
        self._option = None
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == "select" and attributes.get("id") == "settings-translation-workers":
            self._in_select = True
            self.select_attributes = attributes
        elif tag == "option" and self._in_select:
            self._option = {"attributes": attributes, "label": ""}
            self.options.append(self._option)
        elif tag == "label" and attributes.get("for") == "settings-translation-workers":
            self._in_label = True
        elif attributes.get("id") == "settings-translation-workers-help":
            self._in_help = True

    def handle_endtag(self, tag):
        if tag == "select":
            self._in_select = False
        elif tag == "option":
            self._option = None
        elif tag == "label":
            self._in_label = False
        elif tag == "p":
            self._in_help = False

    def handle_data(self, data):
        if self._option is not None:
            self._option["label"] += data
        if self._in_label:
            self.label += data
        if self._in_help:
            self.help += data


def assert_worker_page(client, expected_selected):
    # Writes stay in the isolated fixture, while the template's asset-version
    # stat calls use the actual installed files during this read-only request.
    with patch.object(main.settings, "BASE_DIR", Path(main.__file__).resolve().parent):
        response = client.get("/")
    assert response.status_code == 200
    markup = WorkerSettingsMarkup(response.text)
    assert markup.select_attributes["aria-describedby"] == "settings-translation-workers-help"
    assert markup.label.strip() == "Luồng dịch song song"
    assert "yêu cầu AI" in markup.help and "GPU" in markup.help and "mỗi video" in markup.help
    assert [item["attributes"]["value"] for item in markup.options] == [str(value) for value in TRANSLATION_CHUNK_WORKER_OPTIONS]
    assert [item["label"].strip() for item in markup.options] == [f"{value} luồng" for value in TRANSLATION_CHUNK_WORKER_OPTIONS]
    assert [item["attributes"]["value"] for item in markup.options if "selected" in item["attributes"]] == [str(expected_selected)]


@pytest.fixture
def worker_api(tmp_path, monkeypatch):
    monkeypatch.setattr(main.settings, "BASE_DIR", tmp_path)
    monkeypatch.setattr(main.settings, "WORKSPACE_DIR", tmp_path / "workspace")
    monkeypatch.setattr(main.settings, "TRANSLATION_CHUNK_WORKERS", 5)
    monkeypatch.setattr(main.settings, "OPENCODE_CONCURRENCY", 5)
    monkeypatch.setattr(main, "active_streaming_sessions", {})
    monkeypatch.setattr(main, "active_export_tasks", {})
    monkeypatch.setattr(main, "resolve_api_key", lambda: None)
    monkeypatch.setattr(main, "resolve_openrouter_key", lambda: None)
    monkeypatch.setattr(main, "find_opencode_executable", lambda: None)
    for key in ("TRANSLATION_CHUNK_WORKERS", "OPENCODE_CONCURRENCY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(requests.Session, "request", Mock(side_effect=AssertionError("No provider calls")))
    for entry in ("run", "Popen"):
        monkeypatch.setattr(subprocess, entry, Mock(side_effect=AssertionError("No process launch")))
    env_file = tmp_path / ".env"
    env_file.write_text("# Existing configuration\nUSER_SETTING=preserved\n", encoding="utf-8")
    with TestClient(main.app) as client:
        yield client, env_file
        # Settings tests install path/state-only fake sessions, with no worker
        # lifecycle to drain. Production ownership is exercised separately.
        main.active_streaming_sessions.clear()


def test_settings_returns_selected_workers_and_exact_presets(worker_api):
    client, env_file = worker_api
    original = env_file.read_bytes()
    result = client.get("/api/settings")
    assert result.status_code == 200
    assert result.json()["translation_chunk_workers"] == 5
    assert result.json()["translation_chunk_worker_options"] == [1, 3, 5, 10, 15, 20, 30, 40, 50]
    assert env_file.read_bytes() == original


@pytest.mark.parametrize("selected", TRANSLATION_CHUNK_WORKER_OPTIONS)
def test_settings_page_renders_current_worker_preset(worker_api, monkeypatch, selected):
    client, env_file = worker_api
    original = env_file.read_bytes()
    monkeypatch.setattr(main.settings, "TRANSLATION_CHUNK_WORKERS", selected)
    assert_worker_page(client, selected)
    assert env_file.read_bytes() == original


@pytest.mark.parametrize("selected", TRANSLATION_CHUNK_WORKER_OPTIONS)
@pytest.mark.parametrize("route", ["/api/settings", "/api/config"])
def test_save_worker_preset_persists_both_limits_and_survives_reload(worker_api, selected, route):
    client, env_file = worker_api
    result = client.post(route, json={"translation_chunk_workers": selected})
    assert result.status_code == 200
    assert main.settings.TRANSLATION_CHUNK_WORKERS == selected
    assert main.settings.OPENCODE_CONCURRENCY == selected
    assert type(main.settings.TRANSLATION_CHUNK_WORKERS) is int
    assert type(main.settings.OPENCODE_CONCURRENCY) is int
    assert client.get("/api/settings").json()["translation_chunk_workers"] == selected
    assert_worker_page(client, selected)
    reloaded = Settings(_env_file=env_file)
    assert reloaded.TRANSLATION_CHUNK_WORKERS == selected
    assert reloaded.OPENCODE_CONCURRENCY == selected
    assert "USER_SETTING=preserved" in env_file.read_text(encoding="utf-8")
    assert list(env_file.parent.glob(".env-*.tmp")) == []


@pytest.mark.parametrize("invalid", [True, False, "3", "20", 3.0, 3.5, 0, 2, 4, 8, 21, -1, [], {}])
def test_invalid_workers_rejected_without_mutating_other_fields(worker_api, invalid):
    client, env_file = worker_api
    original = env_file.read_bytes()
    old_buffer = main.settings.INITIAL_BUFFER_SECONDS
    result = client.post("/api/config", json={"translation_chunk_workers": invalid, "buffer_target": "23"})
    assert result.status_code == 422
    assert main.settings.TRANSLATION_CHUNK_WORKERS == 5
    assert main.settings.OPENCODE_CONCURRENCY == 5
    assert main.settings.INITIAL_BUFFER_SECONDS == old_buffer
    assert env_file.read_bytes() == original


@pytest.mark.parametrize("consumer", ["running", "editing", "exporting", "cancelling"])
def test_worker_default_changes_during_processing_without_retuning_existing_project(worker_api, monkeypatch, consumer):
    client, env_file = worker_api
    original = env_file.read_bytes()
    monkeypatch.setattr(main, "active_streaming_sessions", {
        "active": SimpleNamespace(is_running=consumer == "running", is_editing=consumer == "editing", ai_concurrency=5)})
    monkeypatch.setattr(main, "active_export_tasks", {
        "export": {"status": {"exporting": "RUNNING", "cancelling": "CANCELLING"}.get(consumer, "COMPLETED")}})
    result = client.post("/api/settings", json={"translation_chunk_workers": 20})
    assert result.status_code == 200
    assert main.settings.TRANSLATION_CHUNK_WORKERS == main.settings.OPENCODE_CONCURRENCY == 20
    assert main.active_streaming_sessions["active"].ai_concurrency == 5
    assert env_file.read_bytes() != original


def test_unchanged_preset_can_be_saved_while_processing(worker_api, monkeypatch):
    client, _ = worker_api
    monkeypatch.setattr(main, "active_streaming_sessions", {"active": SimpleNamespace(is_running=True)})
    result = client.post("/api/settings", json={"translation_chunk_workers": 5})
    assert result.status_code == 200


def test_concurrency_default_does_not_unlock_other_engine_changes(worker_api, monkeypatch):
    client, env_file = worker_api
    before = env_file.read_bytes()
    monkeypatch.setattr(main, "active_streaming_sessions", {"active": SimpleNamespace(is_running=True, ai_concurrency=5)})
    response = client.post('/api/settings',json={'translation_chunk_workers':50,'buffer_target':'119'})
    assert response.status_code == 409
    assert env_file.read_bytes() == before
    assert main.settings.TRANSLATION_CHUNK_WORKERS == 5


def test_default_capacity_is_twenty_without_environment(worker_api):
    _, env_file = worker_api
    fresh = Settings(_env_file=env_file)
    assert fresh.TRANSLATION_CHUNK_WORKERS == fresh.OPENCODE_CONCURRENCY == 20


def test_failed_atomic_persistence_preserves_file_and_active_limits(worker_api, monkeypatch):
    client, env_file = worker_api
    original = env_file.read_bytes()

    def fail_publish(temporary, destination):
        staged = Path(temporary).read_text(encoding="utf-8")
        assert "TRANSLATION_CHUNK_WORKERS=20" in staged
        assert "OPENCODE_CONCURRENCY=20" in staged
        assert Path(destination) == env_file
        assert env_file.read_bytes() == original
        raise OSError("fixture disk error")

    monkeypatch.setattr(main.os, "replace", fail_publish)
    result = client.post("/api/settings", json={"translation_chunk_workers": 20})
    assert result.status_code == 500
    assert "fixture" not in result.text
    assert main.settings.TRANSLATION_CHUNK_WORKERS == main.settings.OPENCODE_CONCURRENCY == 5
    assert env_file.read_bytes() == original
    assert list(env_file.parent.glob(".env-*.tmp")) == []


@pytest.mark.parametrize("field", ["TRANSLATION_CHUNK_WORKERS", "OPENCODE_CONCURRENCY"])
def test_environment_rejects_worker_capacity_above_fifty(worker_api, field):
    _, env_file = worker_api
    env_file.write_text(f"{field}=51\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        Settings(_env_file=env_file)
