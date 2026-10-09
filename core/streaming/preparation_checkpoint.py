"""Persist completed source preparation so interruption does not repeat ASR."""
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path

from config import settings


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def identity(session):
    from core.streaming.source_revision import source_runtime_identity
    source = session.video_path.resolve(strict=True)
    info = source.stat()
    return {"version": 1, "source": str(source), "size": info.st_size, "mtime": info.st_mtime_ns,
            "asr": session.asr_engine_name, "model": settings.WHISPER_MODEL_SIZE,
            "visual": session.visual_translation, "suppression": settings.SUPPRESSION_MODE,
            "runtime": source_runtime_identity(session)}


def location(key):
    root = settings.BASE_DIR / "workspace" / "cache" / "preparation"
    digest = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()
    return root / (digest + ".json")


def save(session):
    key = identity(session)
    target = location(key)
    target.parent.mkdir(parents=True, exist_ok=True)
    assets = {}
    for name in ("raw_audio_16k", "bgm_audio_path"):
        path = getattr(session, name).resolve(strict=True)
        assets[name] = {"path": str(path), "size": path.stat().st_size, "digest": digest(path)}
    payload = {"key": key, "assets": assets, "duration": session.total_duration,
               "video_size": session.video_size, "suppression_stats": session.suppression_stats,
               "segments": [{"id": s.id, "start": s.start, "end": s.end,
                             "duration": s.duration, "text_zh": s.text_zh, "emotion": s.emotion,
                             "asr_pretranscribed": getattr(s, "asr_pretranscribed", False)}
                            for s in session.segments.values()]}
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".tmp", dir=target.parent, delete=False) as handle:
            temp = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(target)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def load(session):
    try:
        key = identity(session)
        if not key["runtime"]["whisper_asset_revision_known"]:
            return None
        target = location(key)
        if target.is_symlink() or not target.is_file() or target.stat().st_size > 8_000_000:
            return None
        data = json.loads(target.read_text(encoding="utf-8"))
        if data["key"] != key or not 0 < float(data["duration"]) < 86400:
            return None
        if (not isinstance(data["video_size"], list) or len(data["video_size"]) != 2
                or any(type(v) is not int or not 0 < v <= 16384 for v in data["video_size"])
                or not isinstance(data["suppression_stats"], dict) or not isinstance(data["segments"], list)):
            return None
        root = (settings.BASE_DIR / "workspace" / "cache").resolve()
        for asset in data["assets"].values():
            path = Path(asset["path"])
            if (path.is_symlink() or not path.resolve().is_relative_to(root)
                    or not path.is_file() or path.stat().st_size != asset["size"] or asset["size"] <= 0
                    or digest(path) != asset["digest"]):
                return None
        if set(data["assets"]) != {"raw_audio_16k", "bgm_audio_path"}:
            return None
        previous_end = 0
        for index, row in enumerate(data["segments"]):
            if (row["id"] != index or any(not math.isfinite(row[k]) for k in ("start", "end", "duration"))
                    or not 0 <= previous_end <= row["start"] < row["end"] <= data["duration"] + .05
                    or abs(row["end"] - row["start"] - row["duration"]) > .02
                    or not isinstance(row["text_zh"], str) or not isinstance(row["emotion"], str)
                    or type(row["asr_pretranscribed"]) is not bool):
                return None
            previous_end = row["end"]
        return data
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None
