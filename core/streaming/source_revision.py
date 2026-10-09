"""Source-stage identities without loading native models or calling providers."""
from __future__ import annotations

import hashlib
from importlib import metadata
import marshal
from pathlib import Path

from config import settings
from core.review_checkpoint import file_digest


def _code(function):
    code = getattr(function, "__code__", None)
    return hashlib.sha256(marshal.dumps(code)).hexdigest() if code is not None else None


def _assets(root, names):
    return {name: file_digest(root / name) if (root / name).is_file() else "unavailable" for name in names}


def source_runtime_identity(session):
    from core.dialogue_segments import split_dialogue_segments
    from core.streaming import chunked_source
    from core.streaming.speaker_source import annotate_source_rows, _speaker_adapter
    from core.engines.asr.faster_whisper_engine import FasterWhisperFallbackEngine
    from core.engines.asr.whisper_process import SubprocessWhisperModel
    from core.streaming.pipeline import StreamingPipelineSession
    versions = {}
    for name in ("faster-whisper", "ctranslate2", "onnxruntime", "sherpa-onnx"):
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = "unavailable"
    # Hash the actual loaded grouping/adapter functions. An old Studio must
    # never label its results with newer parent implementation files on disk.
    functions = {"dialogue": split_dialogue_segments,
        "transcribe": FasterWhisperFallbackEngine.transcribe,
        "native_adapter": SubprocessWhisperModel._run_worker,
        "source_grouping": getattr(session, "_grounded_visual_segments", StreamingPipelineSession._grounded_visual_segments),
        "source_split": getattr(session, "_split_long_visual_sentence", StreamingPipelineSession._split_long_visual_sentence),
        "legacy_preparation": getattr(session, "_start_legacy_source", StreamingPipelineSession._start_legacy_source),
        "chunk_prepare": chunked_source.prepare_interval,
        "speaker_annotation": annotate_source_rows, "speaker_adapter": _speaker_adapter}
    root = Path(__file__).resolve().parents[1]
    workers = {name: file_digest(root / name) for name in (
        "asr.py", "engines/asr/whisper_worker.py", "engines/asr/native_process.py",
        "engines/asr/diarization_worker.py", "engines/asr/speaker_diarization.py",
        "engines/asr/speaker_registry.py", "engines/asr/diarization_assets.py")}
    model = getattr(getattr(session, "faster_whisper", None), "model_size", settings.WHISPER_MODEL_SIZE)
    model_root = settings.BASE_DIR / "workspace/models" / f"faster-whisper-{model}"
    whisper = _assets(model_root, ("model.bin", "config.json", "tokenizer.json", "vocabulary.json", "vocabulary.txt"))
    # Remote-managed model names have no proven asset revision here. Keep this
    # limitation explicit instead of asserting a pinned remote model version.
    identity = {"revision": 1, "loaded_code": {name: _code(fn) for name, fn in functions.items()},
        "worker_files": workers, "versions": versions, "whisper_assets": whisper,
        "whisper_asset_revision_known": (all(whisper[name] != "unavailable"
            for name in ("model.bin", "config.json", "tokenizer.json"))
            and any(whisper[name] != "unavailable" for name in ("vocabulary.json", "vocabulary.txt"))),
        "device": settings.DEVICE, "asr_threads": settings.ASR_CPU_THREADS,
        "diarization_enabled": settings.DIARIZATION_ENABLED,
        "diarization_threads": settings.DIARIZATION_CPU_THREADS,
        "chunk_policy": {"version": chunked_source.VERSION, "lookahead": chunked_source.LOOKAHEAD_SECONDS,
                         "max_scan": chunked_source.MAX_SCAN_SECONDS}}
    identity["sensevoice_assets"] = _assets(settings.BASE_DIR / "workspace/models/sensevoice_onnx",
        ("model.int8.onnx", "tokens.txt"))
    if settings.DIARIZATION_ENABLED:
        identity["diarization_assets"] = _assets(settings.WORKSPACE_DIR / "models/speaker_diarization",
            ("pyannote-segmentation-3-0.onnx", "wespeaker_zh_cnceleb_resnet34_LM.onnx"))
    return identity
