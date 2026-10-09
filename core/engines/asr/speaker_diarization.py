"""Bounded real diarization with cache, native ownership and durable identities.

The source caller owns ASR and UI states. This adapter returns provisional
audio hypotheses and does not claim transcription, speaker roles or pronouns.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import math
from pathlib import Path

from config import settings
from core.runtime_context import current_execution_context
from core.engines.asr.diarization_assets import installed_assets
from core.engines.asr.native_process import (ASRProcessError, check_cancelled,
    iter_native_records, native_worker_slot, positive_deadline)
from core.engines.asr.speaker_registry import (MatchingPolicy, SpeakerRegistry,
    annotate_rows, digest, validate_observation)


@dataclass(frozen=True)
class DiarizationPolicy:
    cluster_distance: float = .5
    window_shift_ratio: float = .1
    min_duration_on: float = .15
    min_duration_off: float = .15
    min_embedding_seconds: float = 1.5
    max_embedding_seconds: float = 8.
    max_references: int = 3

    def validate(self):
        limits = {"cluster_distance": (0., 2.), "window_shift_ratio": (.01, 1.),
                  "min_duration_on": (.05, 2.), "min_duration_off": (0., 2.),
                  "min_embedding_seconds": (.5, 10.), "max_embedding_seconds": (1., 30.)}
        for name, (lower, upper) in limits.items():
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError("Invalid bounded diarization policy: " + name)
        if (self.max_embedding_seconds < self.min_embedding_seconds or type(self.max_references) is not int
                or not 1 <= self.max_references <= 8):
            raise ValueError("Invalid diarization reference limits")
        return self


def media_identity(path):
    path = Path(path).resolve(strict=True)
    stat = path.stat()
    if not path.is_file() or stat.st_size <= 0:
        raise ValueError("Diarization source is not a non-empty media file")
    return {"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "ctime_ns": stat.st_ctime_ns, "file_id": stat.st_ino}


class BoundedSpeakerDiarizer:
    def __init__(self, registry_path, source_id, *, model_directory=None,
                 policy=None, matching_policy=None):
        self.registry_path = Path(registry_path)
        self.source_id = source_id
        self.model_directory = Path(model_directory or settings.WORKSPACE_DIR / "models" / "speaker_diarization")
        self.policy = (policy or DiarizationPolicy()).validate()
        self.matching_policy = (matching_policy or MatchingPolicy()).validate()

    def diarize(self, media_path, start, end, *, owned_start=None, owned_end=None,
                cancel_check=None, progress_callback=None):
        check = cancel_check or current_execution_context().cancel_check
        check_cancelled(check)
        values = (start, end, start if owned_start is None else owned_start,
                  end if owned_end is None else owned_end)
        if (any(type(value) not in (int, float) or not math.isfinite(value) for value in values)
                or not 0 <= values[0] <= values[2] < values[3] <= values[1]
                or not 0 < end - start <= 90):
            raise ValueError("Diarization interval/ownership must be finite and at most 90 seconds")
        start, end, owned_start, owned_end = values
        identity = media_identity(media_path)
        segmentation, embedding, models = installed_assets(self.model_directory)
        import importlib.metadata
        runtime_version = importlib.metadata.version("sherpa-onnx")
        # A worker/registry implementation change invalidates its own cache,
        # without asking the caller to discard ASR, translations or WAVs.
        implementation = {}
        for filename in ("speaker_diarization.py", "speaker_registry.py", "diarization_worker.py", "diarization_assets.py"):
            import hashlib
            with Path(__file__).with_name(filename).open("rb") as code:
                implementation[filename] = hashlib.file_digest(code, "sha256").hexdigest()
        models = {**models, "runtime_version": runtime_version, "inference_policy": asdict(self.policy),
                  "adapter_version": 1, "implementation": implementation}
        registry = SpeakerRegistry(self.registry_path, self.source_id, models, self.matching_policy, media_identity=identity)
        request_key = digest({"source": identity, "models": models, "matching": asdict(self.matching_policy),
                              "start": start, "end": end, "owned_start": owned_start, "owned_end": owned_end})
        cached = registry.cached(request_key)
        if cached is not None:
            check_cancelled(check)
            if media_identity(media_path) != identity:
                raise ValueError("Diarization source changed during checkpoint retrieval")
            return {**cached, "cache_hit": True}
        threads = getattr(settings, "DIARIZATION_CPU_THREADS", 2)
        if type(threads) is not int or not 1 <= threads <= 16:
            raise ValueError("Invalid diarization CPU thread limit")
        request = {"media_path": identity["path"], "start": start, "end": end,
                   "segmentation_model": str(segmentation), "embedding_model": str(embedding),
                   "threads": threads, "policy": asdict(self.policy)}
        timeout = positive_deadline("DIARIZATION_PROCESS_TIMEOUT", 300)
        result, completed = None, False
        with native_worker_slot(check):
            records = iter_native_records("core.engines.asr.diarization_worker", request,
                timeout=timeout, cancel_check=check, prefix="diarization-worker-", threads=threads)
            try:
                for record in records:
                    if completed:
                        raise ASRProcessError("Diarization worker returned data after completion")
                    if record.get("event") == "progress":
                        processed, total = record.get("processed"), record.get("total")
                        if (type(processed) is not int or type(total) is not int
                                or not 0 <= processed <= total or total <= 0):
                            raise ASRProcessError("Diarization worker returned invalid progress")
                        if progress_callback:
                            progress_callback({"processed": processed, "total": total, "start": start, "end": end})
                    elif record.get("event") == "diarization":
                        if result is not None or record.get("start") != start or record.get("end") != end:
                            raise ASRProcessError("Diarization worker duplicated or changed its interval")
                        try:
                            result = deepcopy(validate_observation(record))
                        except ValueError as error:
                            raise ASRProcessError(str(error)) from error
                        if record.get("engine") != "sherpa_onnx" or record.get("runtime_version") != runtime_version:
                            raise ASRProcessError("Diarization worker/runtime identity mismatch")
                    elif record.get("event") == "completed":
                        if (result is None or type(record.get("intervals")) is not int
                                or type(record.get("embeddings")) is not int
                                or record["intervals"] != len(result["intervals"])
                                or record["embeddings"] != len(result["embeddings"])):
                            raise ASRProcessError("Diarization worker omitted interval/embedding output")
                        completed = True
                    else:
                        raise ASRProcessError("Unknown diarization worker response")
                if not completed:
                    raise ASRProcessError("Diarization worker did not finish; no checkpoint published")
            finally:
                records.close()
        check_cancelled(check)
        if media_identity(media_path) != identity:
            raise ValueError("Diarization source changed during inference; checkpoint not published")
        # Child and descendants have stopped before any mapping/checkpoint is
        # committed. Partial worker output never enters the durable registry.
        return {**registry.record(request_key, result, owned_start, owned_end), "cache_hit": False}

    def annotate(self, rows, result):
        return annotate_rows(rows, result, minimum_coverage=self.matching_policy.minimum_word_coverage)


__all__ = ["BoundedSpeakerDiarizer", "DiarizationPolicy", "media_identity"]
