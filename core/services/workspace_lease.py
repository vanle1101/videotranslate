"""Kernel-backed single-owner lease for one Studio workspace.

The lease is held by an open file descriptor and an advisory kernel lock. It is
deliberately non-blocking: a second backend gets an actionable Busy error rather
than guessing from a PID or deleting a file that another process may hold. The
kernel releases the lock when its process crashes.
"""
from __future__ import annotations

import json
import errno
import os
from pathlib import Path
import threading
import time
import uuid


LEASE_FILENAME = ".studio-workspace.lease"
_LOCK_BYTES = 1


class WorkspaceLeaseBusy(RuntimeError):
    """Another live process owns this workspace."""

    def __init__(self, root, path):
        self.root = Path(root)
        self.path = Path(path)
        super().__init__(
            "Workspace đang được một phiên Studio khác sử dụng; hãy đóng phiên kia trước khi khởi chạy."
        )


def _lock_windows(handle):
    import msvcrt
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, _LOCK_BYTES)
    except (OSError, IOError) as error:
        if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
            raise BlockingIOError from error
        raise


def _unlock_windows(handle):
    import msvcrt
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, _LOCK_BYTES)
    except (OSError, IOError):
        pass


def _lock_unix(handle):
    import fcntl
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError) as error:
        if error.errno in {errno.EACCES, errno.EAGAIN}:
            raise BlockingIOError from error
        raise


def _unlock_unix(handle):
    import fcntl
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except (OSError, IOError):
        pass


class WorkspaceLease:
    """An acquired lease. Keep the object alive for the whole backend lifetime."""

    def __init__(self, root, path, handle, token):
        self.root = Path(root)
        self.path = Path(path)
        self._handle = handle
        self.token = token
        self._released = False
        self._guard = threading.Lock()

    @property
    def released(self):
        return self._released

    def release(self):
        with self._guard:
            if self._released:
                return
            self._released = True
            try:
                if os.name == "nt":
                    _unlock_windows(self._handle)
                else:
                    _unlock_unix(self._handle)
            finally:
                try:
                    self._handle.close()
                except OSError:
                    pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.release()

    def __del__(self):
        # Best-effort only; kernel ownership remains the safety guarantee.
        try:
            self.release()
        except Exception:
            pass


def acquire(root) -> WorkspaceLease:
    """Acquire a non-blocking process lease, leaving the marker file in place.

    The marker is never deleted on release: deleting it while another process is
    blocked/holding an open descriptor would create a split-brain workspace.
    """
    workspace = Path(root).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    path = workspace / LEASE_FILENAME
    handle = None
    try:
        # Locking byte zero beyond EOF is supported by Windows. Do not write
        # even an initialization byte until ownership has been acquired.
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
        os.set_inheritable(descriptor, False)
        handle = os.fdopen(descriptor, "r+b")
        if os.name == "nt":
            _lock_windows(handle)
        else:
            _lock_unix(handle)
    except BlockingIOError:
        if handle is not None:
            handle.close()
        raise WorkspaceLeaseBusy(workspace, path) from None
    except OSError:
        if handle is not None:
            handle.close()
        raise
    token = uuid.uuid4().hex
    # Metadata is diagnostic only; no PID is trusted for ownership decisions.
    lease = WorkspaceLease(workspace, path, handle, token)
    try:
        handle.seek(0)
        handle.write(json.dumps({"version": 1, "token": token, "started_at": time.time()},
                                separators=(",", ":")).encode("ascii"))
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())
    except BaseException:
        lease.release()
        raise
    return lease
