"""Source-stage identities without loading native models or calling providers."""
from __future__ import annotations

import hashlib
from importlib import metadata
import json
from pathlib import Path
from types import CodeType

from config import settings
from core.review_checkpoint import file_digest


def _code_constant(value):
    """Canonical constants, independent of live Python reference counts.

    marshal's reference flags change when an executing parent creates a nested
    function from a code constant. That is serialization state, not a changed
    implementation, and must never invalidate a source halfway through ASR.
    """
    if isinstance(value, CodeType):
        names = ("co_argcount", "co_posonlyargcount", "co_kwonlyargcount", "co_nlocals",
                 "co_stacksize", "co_flags", "co_name", "co_qualname", "co_firstlineno",
                 "co_names", "co_varnames", "co_freevars", "co_cellvars", "co_code",
                 "co_lnotab", "co_linetable", "co_exceptiontable", "co_consts")
        return ["code", {name: _code_constant(getattr(value, name))
                         for name in names if hasattr(value, name)}]
    if isinstance(value, tuple):
        return ["tuple", [_code_constant(item) for item in value]]
    if isinstance(value, frozenset):
        items = [_code_constant(item) for item in value]
        return ["frozenset", sorted(items, key=lambda item: json.dumps(item, sort_keys=True))]
    if isinstance(value, bytes):
        return ["bytes", value.hex()]
    if isinstance(value, float):
        return ["float", value.hex()]
    if isinstance(value, complex):
        return ["complex", value.real.hex(), value.imag.hex()]
    if value is None or value is Ellipsis or type(value) in (str, int, bool):
        return [type(value).__name__, repr(value)]
    raise TypeError("Unsupported source implementation constant")


def _code(function):
    code = getattr(function, "__code__", None)
    if code is None:
        return None
    encoded = json.dumps(_code_constant(code), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _assets(root, names):
    return {name: file_digest(root / name) if (root / name).is_file() else "unavailable" for name in names}


def source_runtime_identity(session):
    from core.dialogue_segments import split_dialogue_segments
    from core.streaming import chunked_source
    from core.streaming import source_progress
    from core.streaming.speaker_source import annotate_source_rows, _speaker_adapter
    from core.engines.asr.faster_whisper_engine import FasterWhisperFallbackEngine
    from core.engines.asr.whisper_process import SubprocessWhisperModel, _merge_focused_recovery
    from core.engines.asr.partial_coverage import validate_coverage, input_duration
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
        "focused_gap_merge": _merge_focused_recovery,
        "focused_gap_retry": SubprocessWhisperModel._retry_unresolved_on_cpu,
        "partial_coverage": validate_coverage, "partial_input": input_duration,
        "source_grouping": getattr(session, "_grounded_visual_segments", StreamingPipelineSession._grounded_visual_segments),
        "source_split": getattr(session, "_split_long_visual_sentence", StreamingPipelineSession._split_long_visual_sentence),
        "legacy_preparation": getattr(session, "_start_legacy_source", StreamingPipelineSession._start_legacy_source),
        "chunk_prepare": chunked_source.prepare_interval,
        "chunk_prepare_impl": chunked_source._prepare_interval,
        "chunk_stage_timer": chunked_source._timed_stage,
        "source_packet_coverage": source_progress.packet_intervals,
        "source_packet_commit": source_progress.commit_packet,
        "speaker_annotation": annotate_source_rows, "speaker_adapter": _speaker_adapter}
    root = Path(__file__).resolve().parents[1]
    workers = {name: file_digest(root / name) for name in (
        "asr.py", "engines/asr/whisper_worker.py", "engines/asr/utterance_language.py", "engines/asr/native_process.py",
        "engines/asr/vocal_evidence.py", "engines/asr/vocal_evidence_worker.py",
        "engines/asr/language_disagreement.py", "engines/asr/gap_context.py", "engines/asr/speech_gap_diagnostic.py",
        "engines/asr/sensevoice_engine.py",
        "engines/separator/roformer_engine.py",
        "engines/asr/diarization_worker.py", "engines/asr/speaker_diarization.py",
        "engines/asr/speaker_registry.py", "engines/asr/diarization_assets.py",
        "streaming/chunked_source.py")}
    model = getattr(getattr(session, "faster_whisper", None), "model_size", settings.WHISPER_MODEL_SIZE)
    model_root = settings.BASE_DIR / "workspace/models" / f"faster-whisper-{model}"
    whisper = _assets(model_root, ("model.bin", "config.json", "tokenizer.json", "vocabulary.json", "vocabulary.txt"))
    # Remote-managed model names have no proven asset revision here. Keep this
    # limitation explicit instead of asserting a pinned remote model version.
    identity = {"revision": 3, "loaded_code": {name: _code(fn) for name, fn in functions.items()},
        "worker_files": workers, "versions": versions, "whisper_assets": whisper,
        "whisper_asset_revision_known": (all(whisper[name] != "unavailable"
            for name in ("model.bin", "config.json", "tokenizer.json"))
            and any(whisper[name] != "unavailable" for name in ("vocabulary.json", "vocabulary.txt"))),
        "device": settings.DEVICE, "asr_threads": settings.ASR_CPU_THREADS,
        # Speaker annotation is optional enrichment after ASR. It does not
        # change recognized words or timings, so toggling it must not force
        # an expensive re-decode of already covered source packets.
        "diarization_threads": settings.DIARIZATION_CPU_THREADS,
        "chunk_policy": {"version": chunked_source.VERSION, "lookahead": chunked_source.LOOKAHEAD_SECONDS,
                         "max_scan": chunked_source.MAX_SCAN_SECONDS}}
    identity["sensevoice_assets"] = _assets(settings.BASE_DIR / "workspace/models/sensevoice_onnx",
        ("model.int8.onnx", "tokens.txt"))
    identity["source_vocal_evidence_assets"] = _assets(settings.BASE_DIR / "workspace/models/audio_separator",
        ("model_bs_roformer_ep_317_sdr_12.9755.ckpt", "model_bs_roformer_ep_317_sdr_12.9755.yaml"))
    identity["diarization_assets"] = _assets(settings.WORKSPACE_DIR / "models/speaker_diarization",
        ("pyannote-segmentation-3-0.onnx", "wespeaker_zh_cnceleb_resnet34_LM.onnx"))
    return identity
