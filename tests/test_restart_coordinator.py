"""Startup ownership tests; no user manifests, engines or provider calls."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import main


@pytest.fixture
def owner(monkeypatch):
    row = SimpleNamespace(task_id="restart-fixture", _restart_auto_resume=True,
        _restart_intent="shutdown", is_running=False, is_stopped=False, can_retry=True,
        error=None, _persist_if_enabled=Mock(), retry_failed_synthesis=AsyncMock())
    monkeypatch.setattr(main, "active_streaming_sessions", {})
    monkeypatch.setattr(main, "editable_session", AsyncMock(return_value=row))
    monkeypatch.setattr("core.streaming.session_store.list_restart_candidates",
        lambda **kwargs: [{"task_id":row.task_id}])
    return row


def test_startup_dispatches_existing_id_once(owner, monkeypatch):
    async def restore(task_id):
        assert task_id == owner.task_id
        main.active_streaming_sessions[task_id] = owner
        return owner
    monkeypatch.setattr(main, "editable_session", restore)
    async def scenario():
        await main.resume_interrupted_projects()
        await main.resume_interrupted_projects()
    asyncio.run(scenario())
    owner.retry_failed_synthesis.assert_awaited_once_with()


@pytest.mark.parametrize("intent", ["user_stopped", "paused", "halted", "completed"])
def test_user_intent_changed_during_discovery_wins(owner, intent):
    owner._restart_intent = intent
    asyncio.run(main.resume_interrupted_projects())
    owner.retry_failed_synthesis.assert_not_awaited()
    owner._persist_if_enabled.assert_not_called()


def test_source_change_requires_review_without_new_task(owner, monkeypatch):
    owner._source_file_signature = "saved"
    owner.video_path = "fixture.mp4"
    monkeypatch.setattr(main, "local_file_signature", lambda path: "changed")
    asyncio.run(main.resume_interrupted_projects())
    owner.retry_failed_synthesis.assert_not_awaited()
    assert owner._restart_intent == "halted" and owner.error
    owner._persist_if_enabled.assert_called_once()


def test_one_restore_failure_does_not_block_next_project(owner, monkeypatch):
    monkeypatch.setattr("core.streaming.session_store.list_restart_candidates",
        lambda **kwargs: [{"task_id":"broken"},{"task_id":owner.task_id}])
    async def restore(task_id):
        if task_id == "broken":
            raise ValueError("fixture corrupt checkpoint")
        return owner
    monkeypatch.setattr(main, "editable_session", restore)
    asyncio.run(main.resume_interrupted_projects())
    owner.retry_failed_synthesis.assert_awaited_once()


def test_shutdown_cancels_and_drains_discovery(owner, monkeypatch, tmp_path):
    monkeypatch.setattr(main.settings, "WORKSPACE_DIR", tmp_path / "workspace")
    monkeypatch.setattr(main, "_workspace_lease", None)
    monkeypatch.setattr(main, "_restart_discovery_task", None)
    monkeypatch.setattr(main, "_backend_background_tasks", set())
    monkeypatch.setattr(main, "_backend_shutting_down", False)
    monkeypatch.setattr(main, "active_export_tasks", {})
    for name in ("preview_manager", "voice_preview_manager", "muse_service"):
        monkeypatch.setattr(main, name, SimpleNamespace(shutdown=Mock()))
    async def wait_forever():
        await asyncio.Event().wait()
    monkeypatch.setattr(main, "resume_interrupted_projects", wait_forever)
    async def scenario():
        await main._start_restart_discovery()
        task = main._restart_discovery_task
        await asyncio.sleep(0)
        await main._stop_restart_discovery()
        assert task.cancelled() and main._restart_discovery_task is None
    asyncio.run(scenario())
