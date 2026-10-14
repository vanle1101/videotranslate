"""Offline regressions for history/startup owner ordering, with no disk restore."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import main
import pytest


@pytest.mark.parametrize("restored_before_scan", [False, True])
def test_history_restored_idle_owner_still_auto_resumes(monkeypatch, restored_before_scan):
    owner = SimpleNamespace(task_id="history-race-fixture", _restart_auto_resume=True,
        _restart_intent="shutdown", is_running=False, is_stopped=False, can_retry=True,
        error=None, _persist_if_enabled=Mock(), retry_failed_synthesis=AsyncMock())
    live = {owner.task_id: owner} if restored_before_scan else {}
    monkeypatch.setattr(main, "active_streaming_sessions", live)
    monkeypatch.setattr(main, "editable_session", AsyncMock(return_value=owner))

    def discover(*, excluded_task_ids):
        # Reproduce a read-only history restore while disk discovery is in flight.
        # The restored object has no worker and has not consumed its restart hint.
        live[owner.task_id] = owner
        return [] if owner.task_id in excluded_task_ids else [{"task_id": owner.task_id}]

    monkeypatch.setattr("core.streaming.session_store.list_restart_candidates", discover)
    asyncio.run(main.resume_interrupted_projects())
    owner.retry_failed_synthesis.assert_awaited_once_with()
