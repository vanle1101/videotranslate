"""Bounded native-inference workers shared by recognition and review.

Worker processes own their descendants. Windows workers start suspended and
join a kill-on-close job before their initial thread can spawn any children.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time

from config import settings

WORKER_SLOT = threading.BoundedSemaphore(1)
MAX_OUTPUT_BYTES = 32 * 1024 * 1024
MAX_DIAGNOSTIC_BYTES = 8 * 1024 * 1024
MAX_LINE_BYTES = 2 * 1024 * 1024


class ASRProcessError(RuntimeError):
    pass


def positive_deadline(name, default):
    value = float(getattr(settings, name, default))
    if not math.isfinite(value) or not 0 < value <= 21600:
        raise ValueError(f"{name} must be finite and between 0 and 21600 seconds")
    return value


def check_cancelled(cancel_check):
    if cancel_check and cancel_check():
        raise asyncio.CancelledError("Speech recognition cancelled")


@contextmanager
def native_worker_slot(cancel_check=None):
    """One native model at a time, independent of AI network concurrency."""
    check_cancelled(cancel_check)
    admission_deadline = time.monotonic() + positive_deadline("ASR_PROCESS_QUEUE_TIMEOUT", 180)
    while not WORKER_SLOT.acquire(timeout=.05):
        check_cancelled(cancel_check)
        if time.monotonic() >= admission_deadline:
            raise TimeoutError("Đang chờ bộ nhận giọng quá lâu; checkpoint trước vẫn được giữ.")
    try:
        check_cancelled(cancel_check)
        yield
    finally:
        WORKER_SLOT.release()


class _WindowsJob:
    """A job owns only this suspended subprocess and descendants it creates."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BASIC_LIMIT(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong), ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class EXTENDED_LIMIT(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        class BASIC_ACCOUNTING(ctypes.Structure):
            _fields_ = [(name, ctypes.c_longlong) for name in (
                "TotalUserTime", "TotalKernelTime", "ThisPeriodTotalUserTime", "ThisPeriodTotalKernelTime")]
            _fields_ += [(name, wintypes.DWORD) for name in (
                "TotalPageFaultCount", "TotalProcesses", "ActiveProcesses", "TotalTerminatedProcesses")]

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int,
                                                     ctypes.c_void_p, wintypes.DWORD)
        self.api.SetInformationJobObject.restype = wintypes.BOOL
        self.api.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        self.api.AssignProcessToJobObject.restype = wintypes.BOOL
        self.api.QueryInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int,
            ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p)
        self.api.QueryInformationJobObject.restype = wintypes.BOOL
        self.api.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        self.api.TerminateJobObject.restype = wintypes.BOOL
        self.accounting_type = BASIC_ACCOUNTING
        self.api.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        self.api.OpenThread.restype = wintypes.HANDLE
        self.api.ResumeThread.argtypes = (wintypes.HANDLE,)
        self.api.ResumeThread.restype = wintypes.DWORD
        self.api.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.api.CloseHandle.restype = wintypes.BOOL
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = EXTENDED_LIMIT()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def attach_and_resume(self, process):
        import ctypes
        import psutil
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())
        threads = psutil.Process(process.pid).threads()
        if len(threads) != 1:
            raise ASRProcessError("Worker nhận giọng chưa được cách ly trước khi khởi chạy.")
        handle = self.api.OpenThread(0x0002, False, threads[0].id)  # THREAD_SUSPEND_RESUME
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if self.api.ResumeThread(handle) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self.api.CloseHandle(handle)

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None

    def terminate_and_drain(self, timeout=10):
        """Wait for every owned process, even after the initial parent exits.

        Closing a kill-on-close job initiates termination but does not wait for
        children to release PCM handles. Keep the job handle until its native
        active-process count reaches zero, then remove the work directory.
        """
        import ctypes
        if not self.handle:
            return
        try:
            if not self.api.TerminateJobObject(self.handle, 1):
                raise ctypes.WinError(ctypes.get_last_error())
            deadline = time.monotonic() + timeout
            while True:
                accounting = self.accounting_type()
                if not self.api.QueryInformationJobObject(self.handle, 1, ctypes.byref(accounting),
                        ctypes.sizeof(accounting), None):
                    raise ctypes.WinError(ctypes.get_last_error())
                if accounting.ActiveProcesses == 0:
                    return
                if time.monotonic() >= deadline:
                    raise ASRProcessError("Worker nhận giọng chưa kết thúc hết tiến trình trong job.")
                time.sleep(.01)
        finally:
            self.close()


def stop_and_reap(process, owner=None):
    # Capture identity-checked psutil handles before closing the job/group.
    # No global name/PID search or unrelated user process is involved.
    import psutil
    try:
        descendants = psutil.Process(process.pid).children(recursive=True)
    except psutil.Error:
        descendants = []
    try:
        if owner is not None:
            owner.terminate_and_drain()
        elif os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        _, surviving = psutil.wait_procs(descendants, timeout=5)
        if surviving:
            raise ASRProcessError("Worker nhận giọng chưa kết thúc hết tiến trình con.")
    finally:
        if owner is not None:
            owner.close()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        handle = getattr(process, "_handle", None)
        close = getattr(handle, "Close", None)
        if callable(close):
            close()


def iter_native_records(module_name, request, *, timeout, cancel_check=None,
                        prefix="native-asr-", threads=4):
    """Disk-backed JSON lines; callers validate their own terminal schema."""
    check_cancelled(cancel_check)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Native worker timeout must be finite and positive")
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        console = executable.with_name("python.exe")
        if console.is_file():
            executable = console
    environment = dict(os.environ)
    environment.update(MKL_NUM_THREADS=str(threads), OMP_NUM_THREADS=str(threads),
                       OPENBLAS_NUM_THREADS=str(threads), MKL_DYNAMIC="FALSE",
                       MKL_DISABLE_FAST_MM="1", PYTHONDONTWRITEBYTECODE="1")
    with tempfile.TemporaryDirectory(prefix=prefix, dir=settings.TEMP_DIR) as directory:
        request_path = Path(directory) / "request.json"
        result_path = Path(directory) / "records.jsonl"
        owned_request = {**request, "work_directory": str(Path(directory).resolve())}
        request_path.write_text(json.dumps(owned_request, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        result_path.touch()
        with tempfile.TemporaryFile() as diagnostics, result_path.open("rb") as results:
            owner = _WindowsJob() if os.name == "nt" else None
            process = None
            try:
                process = subprocess.Popen(
                    [str(executable), "-B", "-X", "utf8", "-m", module_name,
                     str(request_path), str(result_path)], cwd=settings.BASE_DIR, env=environment,
                    stdout=diagnostics, stderr=diagnostics,
                    creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) | 0x00000004)
                                  if owner else 0, start_new_session=owner is None)
                if owner:
                    owner.attach_and_resume(process)
                deadline = time.monotonic() + timeout
                while True:
                    check_cancelled(cancel_check)
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Nhận diện giọng quá thời gian; dừng worker và giữ checkpoint trước.")
                    if result_path.stat().st_size > MAX_OUTPUT_BYTES:
                        raise ASRProcessError("Dữ liệu nhận giọng vượt giới hạn của một đoạn.")
                    if diagnostics.seek(0, 2) > MAX_DIAGNOSTIC_BYTES:
                        raise ASRProcessError("Bộ nhận giọng trả quá nhiều lỗi; đã dừng worker.")
                    position = results.tell()
                    line = results.readline(MAX_LINE_BYTES + 1)
                    if len(line) > MAX_LINE_BYTES:
                        raise ASRProcessError("Một câu nhận giọng vượt giới hạn dữ liệu.")
                    if line and line.endswith(b"\n"):
                        try:
                            record = json.loads(line)
                        except (UnicodeDecodeError, ValueError):
                            raise ASRProcessError("Bộ nhận giọng trả JSON không hợp lệ.") from None
                        if not isinstance(record, dict):
                            raise ASRProcessError("Bộ nhận giọng trả dữ liệu không đúng cấu trúc.")
                        yield record
                        continue
                    results.seek(position)
                    returncode = process.poll()
                    if returncode is not None:
                        if returncode or line:
                            size = diagnostics.seek(0, 2)
                            diagnostics.seek(max(0, size - 1800))
                            detail = diagnostics.read(1800).decode("utf-8", errors="replace").strip()
                            raise ASRProcessError(f"Bộ nhận giọng không hoàn tất (exit={returncode}): {detail}")
                        return
                    time.sleep(.05)
            finally:
                if process is not None:
                    stop_and_reap(process, owner)
                elif owner is not None:
                    owner.close()
