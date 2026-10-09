"""Disposable Whisper workers: native allocators never outlive an ASR call.

The selected model/precision and decoding arguments are preserved. Only one
worker may decode at once; queueing, decoding and cancellation are bounded.
"""
from __future__ import annotations

import math
from pathlib import Path
import subprocess
from types import SimpleNamespace

from config import settings
from core.runtime_context import current_execution_context
from core.engines.asr.native_process import (
    ASRProcessError, WORKER_SLOT as _WORKER_SLOT, positive_deadline,
    check_cancelled, native_worker_slot, iter_native_records,
)


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
        check_cancelled(check)
        source = Path(audio_path).resolve(strict=True)
        timeout = positive_deadline("ASR_PROCESS_TIMEOUT", 600)
        with native_worker_slot(check):
            yield from self._run_worker(source, options, timeout, check)

    def _run_worker(self, source, options, timeout, check):
        threads = max(1, int(getattr(settings, "ASR_CPU_THREADS", 4)))
        request = {"audio_path": str(source), "model_size": self.model_size,
                   "device": self.device, "compute_type": settings.WHISPER_COMPUTE_TYPE,
                   "cpu_threads": threads, "options": options}
        records = iter_native_records("core.engines.asr.whisper_worker", request, timeout=timeout,
                                      cancel_check=check, prefix="whisper-worker-", threads=threads)
        count, complete = 0, False
        try:
            for record in records:
                if complete:
                    raise ASRProcessError("Bộ nhận giọng trả dữ liệu sau khi kết thúc.")
                if record.get("event") == "completed":
                    if type(record.get("segments")) is not int or record["segments"] != count:
                        raise ASRProcessError("Kết quả nhận giọng thiếu hoặc trùng câu.")
                    complete = True
                else:
                    segment = _decode_segment(record)
                    count += 1
                    yield segment
            if not complete:
                raise ASRProcessError("Bộ nhận giọng thiếu xác nhận hoàn tất.")
        finally:
            records.close()
