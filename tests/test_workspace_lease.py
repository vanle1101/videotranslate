"""Kernel lease checks using isolated real subprocesses; no Studio launch."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from core.services.workspace_lease import acquire, WorkspaceLeaseBusy, LEASE_FILENAME


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = '''
import os, sys
from core.services.workspace_lease import acquire, WorkspaceLeaseBusy
try:
    lease = acquire(sys.argv[1])
except WorkspaceLeaseBusy:
    print("busy", flush=True)
    sys.exit(7)
print("acquired", flush=True)
command = sys.stdin.readline().strip()
if command == "crash":
    os._exit(23)
lease.release()
print("released", flush=True)
'''


def spawn(root):
    return subprocess.Popen([sys.executable, "-u", "-c", SCRIPT, str(root)], cwd=ROOT,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def finish(process, command="release"):
    output, errors = process.communicate(command + "\n", timeout=10)
    assert not errors
    return output


def test_same_process_conflict_release_and_marker_retention(tmp_path):
    first = acquire(tmp_path)
    try:
        with pytest.raises(WorkspaceLeaseBusy):
            acquire(tmp_path / ".")
    finally:
        first.release()
    first.release()
    assert first.released and (tmp_path / LEASE_FILENAME).is_file()
    with acquire(tmp_path) as second:
        assert second.token != first.token
        assert (tmp_path / LEASE_FILENAME).stat().st_size < 1024


@pytest.mark.parametrize("command,exit_code", [("release", 0), ("crash", 23)])
def test_real_subprocess_conflict_and_kernel_release(tmp_path, command, exit_code):
    owner = spawn(tmp_path)
    try:
        assert owner.stdout.readline().strip() == "acquired"
        contender = spawn(tmp_path)
        output, errors = contender.communicate(timeout=10)
        assert output.strip() == "busy" and not errors and contender.returncode == 7
        with pytest.raises(WorkspaceLeaseBusy):
            acquire(tmp_path)
        finish(owner, command)
        assert owner.returncode == exit_code
        with acquire(tmp_path):
            pass
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.communicate(timeout=10)


def test_different_workspaces_do_not_block_each_other(tmp_path):
    with acquire(tmp_path / "a"), acquire(tmp_path / "b"):
        pass


def test_stale_metadata_does_not_prevent_acquisition(tmp_path):
    marker = tmp_path / LEASE_FILENAME
    marker.write_text('corrupted stale marker; no live lock', encoding="utf-8")
    with acquire(tmp_path):
        assert marker.exists()


def test_metadata_write_failure_releases_kernel_lock(tmp_path, monkeypatch):
    real_fsync = os.fsync
    def fail(_):
        raise OSError("simulated disk failure")
    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(OSError, match="simulated"):
        acquire(tmp_path)
    monkeypatch.setattr(os, "fsync", real_fsync)
    with acquire(tmp_path):
        pass
