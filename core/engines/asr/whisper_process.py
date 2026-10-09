"""Disposable Whisper workers: native allocators never outlive an ASR call.

The selected model/precision and decoding arguments are preserved. Only one
worker may decode at once; queueing, decoding and cancellation are bounded.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

from config import settings
from core.runtime_context import current_execution_context

_WORKER_SLOT = threading.BoundedSemaphore(1)
MAX_OUTPUT_BYTES = 32 * 1024 * 1024
MAX_DIAGNOSTIC_BYTES = 8 * 1024 * 1024
MAX_LINE_BYTES = 2 * 1024 * 1024


class ASRProcessError(RuntimeError):
    pass


def _positive_deadline(name, default):
    value = float(getattr(settings, name, default))
    if not math.isfinite(value) or not 0 < value <= 21600:
        raise ValueError(f"{name} must be finite and between 0 and 21600 seconds")
    return value


def _check_cancelled(cancel_check):
    if cancel_check and cancel_check():
        raise asyncio.CancelledError("Speech recognition cancelled")


def _stop_and_reap(process):
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)
    handle = getattr(process, "_handle", None)
    close = getattr(handle, "Close", None)
    if callable(close):
        close()


def _decode_segment(record):
    """Validate child output before it becomes a source checkpoint."""
    if not isinstance(record, dict) or record.get("event") != "segment":
        raise ASRProcessError("Bộ nhận giọng trả dữ liệu không đúng cấu trúc.")
    values = record.get("segment")
    if (not isinstance(values, dict) or not isinstance(values.get("text"), str)
            or any(type(values.get(key)) not in (int, float) or not math.isfinite(values[key])
                   for key in ("start", "end"))
            or not 0 <= values["start"] < values["end"]
            or not isinstance(values.get("words"), list)):
        raise ASRProcessError("Bộ nhận giọng trả lời thoại hoặc mốc thời gian không hợp lệ.")
    words = []
    for word in values["words"]:
        if (not isinstance(word, dict) or not isinstance(word.get("word"), str)
                or any(type(word.get(key)) not in (int, float) or not math.isfinite(word[key])
                       for key in ("start", "end"))
                or not 0 <= word["start"] <= word["end"]):
            raise ASRProcessError("Bộ nhận giọng trả mốc từ không hợp lệ.")
        words.append(SimpleNamespace(**word))
    return SimpleNamespace(start=values["start"], end=values["end"], text=values["text"],
                           words=words, speaker=None)


class SubprocessWhisperModel:
    """Small model-compatible handle; no CTranslate2/ONNX model lives here."""

    def __init__(self, model_size, device=None):
        self.model_size = str(model_size)
        self.device = device or settings.DEVICE

    def transcribe(self, audio_path, **options):
        # Match Whisper's lazy iterator contract. The child is acquired when
        # iteration begins and reaped before this iterator finishes or closes.
        return self._segments(audio_path, options), None

    def _segments(self, audio_path, options):
        context = current_execution_context()
        check = context.cancel_check
        _check_cancelled(check)
        source = Path(audio_path).resolve(strict=True)
        timeout = _positive_deadline("ASR_PROCESS_TIMEOUT", 600)
        queue_timeout = _positive_deadline("ASR_PROCESS_QUEUE_TIMEOUT", 180)
        admission_deadline = time.monotonic() + queue_timeout
        while not _WORKER_SLOT.acquire(timeout=.05):
            _check_cancelled(check)
            if time.monotonic() >= admission_deadline:
                raise TimeoutError("Đang chờ bộ nhận giọng quá lâu; checkpoint trước vẫn được giữ.")
        try:
            _check_cancelled(check)
            yield from self._run_worker(source, options, timeout, check)
        finally:
            _WORKER_SLOT.release()

    def _run_worker(self, source, options, timeout, check):
        threads = max(1, int(getattr(settings, "ASR_CPU_THREADS", 4)))
        request = {"audio_path": str(source), "model_size": self.model_size,
                   "device": self.device, "compute_type": settings.WHISPER_COMPUTE_TYPE,
                   "cpu_threads": threads, "options": options}
        executable = Path(sys.executable)
        if executable.name.lower() == "pythonw.exe":
            console = executable.with_name("python.exe")
            if console.is_file():
                executable = console
        environment = dict(os.environ)
        environment.update(MKL_NUM_THREADS=str(threads), OMP_NUM_THREADS=str(threads),
                           OPENBLAS_NUM_THREADS=str(threads), MKL_DYNAMIC="FALSE",
                           MKL_DISABLE_FAST_MM="1", PYTHONDONTWRITEBYTECODE="1")
        # Request/results are bounded disk-backed files, not an unbounded IPC
        # queue of audio arrays or captured subprocess output in RAM.
        with tempfile.TemporaryDirectory(prefix="whisper-worker-", dir=settings.TEMP_DIR) as directory:
            request_path = Path(directory) / "request.json"
            result_path = Path(directory) / "segments.jsonl"
            request_path.write_text(json.dumps(request, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            result_path.touch()
            with tempfile.TemporaryFile() as diagnostics, result_path.open("rb") as results:
                process = subprocess.Popen(
                    [str(executable), "-B", "-X", "utf8", "-m", "core.engines.asr.whisper_worker",
                     str(request_path), str(result_path)], cwd=settings.BASE_DIR, env=environment,
                    stdout=diagnostics, stderr=diagnostics,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                deadline = time.monotonic() + timeout
                count, complete = 0, False
                try:
                    while True:
                        _check_cancelled(check)
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
                            if record.get("event") == "completed":
                                if complete or type(record.get("segments")) is not int or record["segments"] != count:
                                    raise ASRProcessError("Kết quả nhận giọng thiếu hoặc trùng câu.")
                                complete = True
                            else:
                                if complete:
                                    raise ASRProcessError("Bộ nhận giọng trả câu sau khi kết thúc.")
                                segment = _decode_segment(record)
                                count += 1
                                yield segment
                            continue
                        results.seek(position)
                        returncode = process.poll()
                        if returncode is not None:
                            # An exited process has closed its result stream.
                            # Any unfinished JSON line or missing terminal
                            # marker is failure, even after partial transcripts.
                            if returncode or not complete or line:
                                size = diagnostics.seek(0, 2)
                                diagnostics.seek(max(0, size - 1800))
                                detail = diagnostics.read(1800).decode("utf-8", errors="replace").strip()
                                raise ASRProcessError(f"Bộ nhận giọng không hoàn tất (exit={returncode}): {detail}")
                            return
                        time.sleep(.05)
                finally:
                    _stop_and_reap(process)
