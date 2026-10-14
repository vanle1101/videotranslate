"""Real ASGI lifecycle + kernel lock; every workspace and worker is isolated."""
import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock

import pytest

import main
from config import settings
from core.services.workspace_lease import acquire, WorkspaceLeaseBusy


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "WORKSPACE_DIR", tmp_path / "workspace")
    monkeypatch.setattr(main, "_workspace_lease", None)
    monkeypatch.setattr(main, "_restart_discovery_task", None)
    monkeypatch.setattr(main, "_backend_shutting_down", False)
    monkeypatch.setattr(main, "_backend_background_tasks", set())
    monkeypatch.setattr(main, "active_streaming_sessions", {})
    monkeypatch.setattr(main, "active_export_tasks", {})
    discovery = AsyncMock()
    monkeypatch.setattr(main, "resume_interrupted_projects", discovery)
    for manager in ("preview_manager", "voice_preview_manager", "muse_service"):
        monkeypatch.setattr(main, manager, SimpleNamespace(shutdown=Mock()))
    yield discovery
    lease = main._workspace_lease
    if lease is not None:
        lease.release()


def test_busy_startup_never_launches_recovery(isolated):
    async def scenario():
        with acquire(settings.WORKSPACE_DIR):
            with pytest.raises(WorkspaceLeaseBusy):
                await main.app.router.startup()
        isolated.assert_not_called()
        assert main._workspace_lease is None
    asyncio.run(scenario())


def test_real_startup_lease_precedes_discovery_and_shutdown_releases(isolated):
    async def discovery():
        with pytest.raises(WorkspaceLeaseBusy):
            acquire(settings.WORKSPACE_DIR)
    isolated.side_effect = discovery
    async def scenario():
        await main.app.router.startup()
        await main._restart_discovery_task
        with pytest.raises(WorkspaceLeaseBusy):
            await main.app.router.startup()
        assert isolated.await_count == 1
        await main.app.router.shutdown()
        assert main._workspace_lease is None
        with acquire(settings.WORKSPACE_DIR):
            pass
        await main.app.router.shutdown()  # idempotent
    asyncio.run(scenario())


def test_shutdown_retains_lease_until_real_native_worker_and_export_drain(isolated):
    entered, release = threading.Event(), threading.Event()
    events = []
    async def scenario():
        await main.app.router.startup()
        await main._restart_discovery_task
        def native():
            entered.set()
            assert release.wait(5)
            events.append("native_done")
        async def export():
            worker = asyncio.create_task(asyncio.to_thread(native))
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                await worker
                events.append("export_drained")
                raise
        task = asyncio.create_task(export())
        session = SimpleNamespace(export_task=task, shutdown=lambda: events.append("session_shutdown"))
        main.active_streaming_sessions["isolated"] = session
        main.active_export_tasks["export_isolated"] = {"status":"RUNNING"}
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(.01)
        assert entered.is_set()
        stopping = asyncio.create_task(main.app.router.shutdown())
        await asyncio.sleep(.03)
        assert not stopping.done()
        with pytest.raises(WorkspaceLeaseBusy):
            acquire(settings.WORKSPACE_DIR)
        release.set()
        await asyncio.wait_for(stopping, 5)
        assert events == ["native_done", "export_drained", "session_shutdown"]
        with acquire(settings.WORKSPACE_DIR):
            pass
    try:
        asyncio.run(scenario())
    finally:
        release.set()


def test_shutdown_tracks_not_yet_started_source_and_blocks_new_auto_export(isolated, monkeypatch):
    invoked = []
    async def scenario():
        await main.app.router.startup()
        await main._restart_discovery_task
        async def pending_source():
            invoked.append(True)
        task = main._background_task(pending_source())
        await main.app.router.shutdown()
        assert task.cancelled() and not invoked
        with pytest.raises(RuntimeError, match="đang dừng"):
            main._background_task(pending_source())
        monkeypatch.setattr(main, "get_streaming_session", lambda _: pytest.fail("no export after fence"))
        main.schedule_reviewed_export("isolated")
    asyncio.run(scenario())


def test_failed_shutdown_keeps_lock_fail_closed(isolated):
    async def scenario():
        await main.app.router.startup()
        await main._restart_discovery_task
        def failed():
            raise OSError("checkpoint failed")
        main.active_streaming_sessions["isolated"] = SimpleNamespace(shutdown=failed)
        with pytest.raises(OSError, match="checkpoint failed"):
            await main.app.router.shutdown()
        with pytest.raises(WorkspaceLeaseBusy):
            acquire(settings.WORKSPACE_DIR)
    asyncio.run(scenario())
