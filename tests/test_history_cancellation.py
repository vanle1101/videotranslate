"""History cancellation reaps its owned threads and native validation process."""
import asyncio
import subprocess
import sys
import threading
import time
from unittest.mock import AsyncMock

import pytest

import main
from core import history_work
from core.streaming import session_store as store
from test_history_api import saved_project


@pytest.fixture(autouse=True)
def isolated_history_state():
    history_work.reset_history_reads()
    yield
    history_work.reset_history_reads()


def test_cancelled_request_drains_real_manifest_audio_inspection(saved_project, monkeypatch):
    entered, exited = threading.Event(), threading.Event()
    def inspecting(row, path):
        entered.set()
        try:
            while True:
                history_work.check_history_read()
                time.sleep(.01)
        finally:
            exited.set()
    monkeypatch.setattr(store, "_valid_row_audio", inspecting)
    async def run():
        task = asyncio.create_task(main.list_tasks())
        while not entered.is_set():
            await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert exited.is_set()
        assert not history_work._active
    asyncio.run(run())


def test_shutdown_cancels_both_pending_history_reads_without_partial_success(saved_project, monkeypatch):
    entered = []
    def inspecting(row, path):
        entered.append(threading.get_ident())
        while True:
            history_work.check_history_read()
            time.sleep(.01)
    monkeypatch.setattr(store, "_valid_row_audio", inspecting)
    async def run():
        jobs = [asyncio.create_task(main.list_tasks()) for _ in range(2)]
        while len(entered) < 2:
            await asyncio.sleep(.01)
        history_work.cancel_history_reads()
        results = await asyncio.wait_for(asyncio.gather(*jobs, return_exceptions=True), 2)
        assert all(isinstance(error, main.HTTPException) and error.status_code == 503 for error in results)
        assert not history_work._active
        with pytest.raises(main.HTTPException) as caught:
            await main.list_tasks()
        assert caught.value.status_code == 503
    asyncio.run(run())


def test_disconnected_browser_releases_history_worker():
    exited = threading.Event()
    def read():
        try:
            while True:
                history_work.check_history_read()
                time.sleep(.01)
        finally:
            exited.set()
    request = type("Request", (), {"is_disconnected": AsyncMock(return_value=True)})()
    with pytest.raises(history_work.HistoryReadCancelled):
        asyncio.run(history_work.read_history(read, request=request))
    assert exited.is_set() and not history_work._active


def test_filesystem_failure_during_cancellation_releases_worker_ownership():
    entered = threading.Event()
    def read():
        entered.set()
        while True:
            try:
                history_work.check_history_read()
            except history_work.HistoryReadCancelled:
                raise OSError("read failed while shutting down") from None
            time.sleep(.01)
    async def run():
        worker = asyncio.create_task(history_work.read_history(read))
        while not entered.is_set():
            await asyncio.sleep(.01)
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(worker, 2)
        assert not history_work._active
    asyncio.run(run())


def test_history_deadline_is_not_a_corrupt_project_or_partial_result(saved_project, monkeypatch):
    monkeypatch.setattr(history_work, "HISTORY_READ_TIMEOUT", .03)
    def inspect(row, path):
        while True:
            history_work.check_history_read()
            time.sleep(.01)
    monkeypatch.setattr(store, "_valid_row_audio", inspect)
    with pytest.raises(main.HTTPException) as caught:
        asyncio.run(main.list_tasks())
    assert caught.value.status_code == 503
    assert store._project_path(saved_project.task_id).exists()


def test_cancelling_native_probe_kills_and_reaps_only_owned_child(monkeypatch):
    owned = []
    real_popen = subprocess.Popen
    def spawn(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        owned.append(child)
        return child
    monkeypatch.setattr(store.subprocess, "Popen", spawn)
    async def run():
        worker = asyncio.create_task(history_work.read_history(store._run_output_probe,
            [sys.executable, "-c", "import time; time.sleep(30)"], timeout=30, capture_output=True))
        while not owned:
            await asyncio.sleep(.01)
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(worker, 3)
        assert owned[0].poll() is not None
        assert not history_work._active
    asyncio.run(run())


def test_cancelled_validation_does_not_cache_corrupt_output(tmp_path, monkeypatch):
    path = tmp_path / "isolated.mp4"
    path.write_bytes(b"isolated fixture" * 4)
    identity = store._output_identity(path, 1.)
    event = threading.Event()
    def probe(*args):
        event.set()
        history_work.check_history_read()
    monkeypatch.setattr(store, "_probe_saved_output", probe)
    with history_work.history_read_scope(event):
        with pytest.raises(history_work.HistoryReadCancelled):
            store._valid_output(path, 1.)
    assert identity not in store._OUTPUT_CHECKS
