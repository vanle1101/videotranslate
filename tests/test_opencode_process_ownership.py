import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from core.engines.translation import opencode_client as oc


@pytest.fixture
def owned_factory(monkeypatch):
    events = []

    class Handle(int):
        def Close(self):
            events.append("process-close")

    class Process:
        def __init__(self, argv, **options):
            self._handle = Handle(123)
            self.pid = 456
            self.stdin = self.stdout = self.stderr = None
            self.returncode = None
            events.append(("spawn", options))

        def kill(self):
            events.append("root-kill")

        def wait(self, timeout):
            events.append("wait")
            self.returncode = 0
            return 0

    class Job:
        def __init__(self):
            self.handle = 789
            events.append("job-create")

        def assign(self, process):
            events.append("assign")

        def resume(self, process):
            events.append("resume")

        def terminate(self):
            events.append("terminate")

        def active(self):
            events.append("active")
            return 0

        def close(self):
            events.append("job-close")
            self.handle = None

    monkeypatch.setattr(oc, "_POPEN_TYPE", Process)
    monkeypatch.setattr(oc.subprocess, "Popen", Process)
    monkeypatch.setattr(oc, "_WindowsJob", Job)
    return events, Process, Job


@pytest.mark.skipif(os.name != "nt", reason="Windows launch contract")
def test_assign_before_resume_and_idempotent_handle_cleanup(owned_factory):
    events, _, _ = owned_factory
    process = oc._start_process(["dummy"], SimpleNamespace(cancel_check=None), shell=False, creationflags=0x08000000)
    assert events[0][0] == "spawn"
    assert events[0][1]["creationflags"] == 0x08000004
    assert events[0][1]["shell"] is False
    assert events[1:] == ["job-create", "assign", "resume"]
    assert oc._close_owned_process(process)
    assert events[-5:] == ["active", "root-kill", "wait", "process-close", "job-close"]
    before = list(events)
    assert oc._close_owned_process(process)
    assert events == before


@pytest.mark.skipif(os.name != "nt", reason="Windows launch contract")
@pytest.mark.parametrize("stage", ["create", "assign", "resume", "cancel", "interrupt"])
def test_failed_attach_or_cancel_never_releases_unowned_cli(owned_factory, monkeypatch, stage):
    events, _, Job = owned_factory
    def fail(*args):
        raise OSError("test attach failure")
    if stage in {"create", "assign", "resume"}:
        monkeypatch.setattr(Job, "__init__" if stage == "create" else stage, fail)
    checks = []
    def cancelled():
        checks.append(True)
        if len(checks) == 2 and stage == "interrupt":
            raise KeyboardInterrupt()
        return len(checks) == 2 and stage == "cancel"
    expected = (KeyboardInterrupt if stage == "interrupt" else
                oc.OpenCodeCancelledError if stage == "cancel" else OSError)
    with pytest.raises(expected):
        oc._start_process(["dummy"], SimpleNamespace(cancel_check=cancelled))
    assert "root-kill" in events and "wait" in events and "process-close" in events
    if stage != "create":
        assert "job-close" in events
    if stage != "resume":
        assert "resume" not in events
    assert sum(isinstance(event, tuple) and event[0] == "spawn" for event in events) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows launch contract")
def test_unconfirmed_tree_cleanup_preserves_timeout_but_disables_retry(owned_factory, monkeypatch):
    events, Process, Job = owned_factory
    process = oc._start_process(["dummy"], SimpleNamespace(cancel_check=None))
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("dummy", 0.01)
    process.communicate = timeout
    monkeypatch.setattr(Job, "active", lambda self: (_ for _ in ()).throw(OSError("query failed")))
    with pytest.raises(oc.OpenCodeTimeoutError) as error:
        oc._communicate(process, "input", .1, SimpleNamespace(cancel_check=None))
    assert error.value.code == "provider_timeout"
    assert error.value.retryable is False
    assert process._opencode_job.handle is None
    assert not process._opencode_cleanup_complete
    assert "wait" in events and "process-close" in events


@pytest.mark.skipif(os.name != "nt", reason="Windows launch contract")
def test_communication_thread_start_failure_preserves_primary_and_closes_handles(owned_factory, monkeypatch):
    events, _, _ = owned_factory
    process = oc._start_process(["dummy"], SimpleNamespace(cancel_check=None))
    def failed_start(self):
        raise RuntimeError("can't start new thread")
    monkeypatch.setattr(threading.Thread, "start", failed_start)
    with pytest.raises(RuntimeError, match="can't start new thread"):
        oc._communicate(process, "input", .1, SimpleNamespace(cancel_check=None))
    assert process._opencode_cleanup_complete
    assert "process-close" in events and "job-close" in events
    assert not hasattr(process, "_opencode_communication_thread")


@pytest.mark.skipif(os.name != "nt", reason="Windows launch contract")
def test_root_wait_failure_still_closes_handle_and_marks_cleanup_unconfirmed(owned_factory, monkeypatch):
    events, Process, _ = owned_factory
    process = oc._start_process(["dummy"], SimpleNamespace(cancel_check=None))
    def fail_wait(self, timeout):
        raise subprocess.TimeoutExpired("dummy", timeout)
    monkeypatch.setattr(Process, "wait", fail_wait)
    assert oc._close_owned_process(process) is False
    assert "process-close" in events and "job-close" in events


@pytest.mark.skipif(os.name != "nt", reason="Windows launch contract")
def test_job_active_wait_uses_one_bounded_deadline(owned_factory, monkeypatch):
    _, _, Job = owned_factory
    process = oc._start_process(["dummy"], SimpleNamespace(cancel_check=None))
    clock = [0.0]
    monkeypatch.setattr(oc.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(oc.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(Job, "active", lambda self: 1)
    assert oc._close_owned_process(process) is False
    assert 5 <= clock[0] <= 5.02
    assert process._opencode_job.handle is None


_DUMMY = r'''
import ctypes, json, os, pathlib, subprocess, sys, threading, time
from ctypes import wintypes
root, role, mode = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
timer = threading.Timer(25, lambda: os._exit(90))
timer.daemon = True
timer.start()
api = ctypes.WinDLL("kernel32", use_last_error=True)
api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
api.CreateFileW.restype = wintypes.HANDLE
lock = None
if role != "parent":
    lock = api.CreateFileW(str(root / (role + ".lock")), 0xC0000000, 0, None, 2, 0x80, None)
    if lock == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
(root / (role + ".json")).write_text(json.dumps({"pid": os.getpid()}))
if role != "grandchild":
    child = "child" if role == "parent" else "grandchild"
    streams = {"stdin": sys.stdin, "stdout": sys.stdout, "stderr": sys.stderr}
    if mode == "success":
        streams = {key: subprocess.DEVNULL for key in streams}
    subprocess.Popen([sys.executable, "-B", "-c", os.environ["OWNERSHIP_DUMMY"], str(root), child, mode], **streams)
if role == "parent":
    deadline = time.monotonic() + 10
    while not (root / "grandchild.json").exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("grandchild readiness")
        time.sleep(.01)
    if mode in ("exited", "success"):
        print("finished", flush=True)
        sys.exit(0)
time.sleep(20)
'''


def _wait_for(predicate, seconds=8):
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("bounded process readiness expired")
        time.sleep(.01)


def _lock_is_free(path):
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                               wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.CreateFileW(str(path), 0xC0000000, 0, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        return False
    api.CloseHandle(handle)
    return True


@pytest.mark.skipif(os.name != "nt", reason="actual Windows Job Objects")
@pytest.mark.parametrize("mode", ["alive", "exited", "cancel", "success", "nested"])
def test_local_windows_tree_has_no_descendants_or_pipes_after_return(tmp_path, monkeypatch, mode):
    import psutil
    real_popen = subprocess.Popen
    outer = oc._WindowsJob() if mode == "nested" else None
    if outer is not None:
        def spawn(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            outer.assign(process)
            return process
        monkeypatch.setattr(oc.subprocess, "Popen", spawn)
    sentinel = real_popen([sys.executable, "-B", "-c", "import time; time.sleep(25)"],
                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    process = None
    try:
        env = dict(os.environ, OWNERSHIP_DUMMY=_DUMMY)
        dummy_mode = "success" if mode == "nested" else mode
        process = oc._start_process([sys.executable, "-B", "-c", _DUMMY, str(tmp_path), "parent", dummy_mode],
                                    SimpleNamespace(cancel_check=None), env=env, shell=False,
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)
        _wait_for(lambda: (tmp_path / "grandchild.json").exists())
        identities = []
        for role in ("child", "grandchild"):
            pid = json.loads((tmp_path / (role + ".json")).read_text())["pid"]
            identities.append((pid, psutil.Process(pid).create_time()))
            assert not _lock_is_free(tmp_path / (role + ".lock"))
        if mode in {"exited", "success", "nested"}:
            process.wait(timeout=5)
            assert process.returncode == 0
        started = time.monotonic()
        context = SimpleNamespace(cancel_check=(lambda: time.monotonic() - started > .15) if mode == "cancel" else None)
        if mode in {"success", "nested"}:
            stdout, stderr = oc._communicate(process, "", 2, context)
            assert stdout.strip() == "finished" and not stderr
        else:
            expected = oc.OpenCodeCancelledError if mode == "cancel" else oc.OpenCodeTimeoutError
            with pytest.raises(expected):
                oc._communicate(process, "x" * 1048576, .3, context)
        elapsed = time.monotonic() - started
        assert elapsed < 6
        assert process._opencode_cleanup_complete is True
        assert process._opencode_job.handle is None
        assert process._handle.closed
        assert all(pipe.closed for pipe in (process.stdin, process.stdout, process.stderr))
        assert not any(thread.name == f"opencode-io-{process.pid}" for thread in threading.enumerate())
        for name in ("stdout_thread", "stderr_thread"):
            thread = getattr(process, name, None)
            assert thread is None or not thread.is_alive()
        for pid, created in identities:
            try:
                descendant = psutil.Process(pid)
                if descendant.create_time() == created:
                    descendant.wait(timeout=1)
            except psutil.NoSuchProcess:
                pass
        assert all(_lock_is_free(tmp_path / (role + ".lock")) for role in ("child", "grandchild"))
        assert sentinel.poll() is None
        print(json.dumps({"mode": mode, "elapsed_seconds": round(elapsed, 3),
                          "root_pid": process.pid, "descendants": identities,
                          "cleanup_complete": True, "sentinel_alive": True}))
    finally:
        if process is not None:
            oc._close_owned_process(process)
        if outer is not None:
            outer.close()
        sentinel.kill()
        sentinel.wait(timeout=5)
        sentinel._handle.Close()


@pytest.mark.skipif(os.name != "nt", reason="actual Windows suspended launch")
@pytest.mark.parametrize("stage", ["assign", "resume", "cancel"])
def test_actual_failed_setup_cannot_run_child_code(tmp_path, monkeypatch, stage):
    real_popen = subprocess.Popen
    processes = []
    def spawn(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(oc.subprocess, "Popen", spawn)
    def failed(*args):
        raise OSError("test native setup failure")
    if stage != "cancel":
        monkeypatch.setattr(oc._WindowsJob, stage, failed)
    checks = []
    def cancelled():
        checks.append(True)
        return stage == "cancel" and len(checks) == 2
    marker = tmp_path / "ran"
    with pytest.raises(oc.OpenCodeCancelledError if stage == "cancel" else OSError):
        oc._start_process([sys.executable, "-B", "-c",
                           "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('ran')", str(marker)],
                          SimpleNamespace(cancel_check=cancelled), shell=False,
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert len(processes) == 1
    process = processes[0]
    assert process.returncode is not None
    assert process._opencode_cleanup_complete
    assert process._opencode_job.handle is None
    assert process._handle.closed
    assert all(pipe.closed for pipe in (process.stdin, process.stdout, process.stderr))
    assert not marker.exists()


def test_posix_launch_options_are_unchanged(monkeypatch):
    if os.name == "nt":
        pytest.skip("native POSIX contract")
    calls = []
    monkeypatch.setattr(oc.subprocess, "Popen", lambda *args, **kwargs: calls.append((args, kwargs)))
    oc._start_process(["dummy"], SimpleNamespace(cancel_check=None), shell=False, creationflags=0)
    assert calls == [((["dummy"],), {"shell": False, "creationflags": 0})]
