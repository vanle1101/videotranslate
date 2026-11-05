"""Validated, restart-safe cache for the separated background stem.

The background stem is independent of translated text and narration.  This
module deliberately has a small API so export code can reuse it without
coupling the cache to a particular mixer or caption implementation.
"""

import hashlib
import importlib.metadata
import json
import math
import os
import re
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path

from config import settings
from core.media_process import run_media


SCHEMA = 1
MAX_ENTRY_BYTES = 128 * 1024 * 1024
MAX_CACHE_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 96 * 1024
_NAME = re.compile(r"[0-9a-f]{64}\.bg\Z")
_LOCK = threading.RLock()


@dataclass(frozen=True)
class BackgroundCacheIdentity:
    key: str
    duration: float
    sample_rate: int = 44100
    channels: int = 2


def _check_cancel(cancel_check):
    if cancel_check and cancel_check():
        raise RuntimeError("Tác vụ đã bị hủy bởi người dùng.")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(path, cancel_check=None):
    result = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            _check_cancel(cancel_check)
            result.update(chunk)
    return result.hexdigest()


def _file_identity(path, cancel_check=None):
    path = Path(path).resolve(strict=True)
    before = path.stat()
    if not path.is_file() or before.st_size <= 0:
        raise ValueError("Background cache input is not a nonempty file.")
    digest = _digest(path, cancel_check)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Background cache input changed during hashing.")
    # Timestamps detect concurrent changes, but relocating identical source
    # bytes must not invalidate an expensive background separation.
    return {"size": after.st_size, "sha256": digest}


def _package_version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _implementation(cancel_check=None):
    core_dir = Path(__file__).parents[1]
    files = [Path(__file__), core_dir / "media_process.py",
             core_dir / "engines/separator/realtime_suppressor.py",
             core_dir / "engines/separator/roformer_engine.py"]
    return {str(p.relative_to(core_dir)): _digest(p, cancel_check) for p in files}


def _engine_payload(engine, suppressor, cancel_check=None):
    from core.engines.separator.realtime_suppressor import RealtimeVocalSuppressor
    if type(suppressor) is not RealtimeVocalSuppressor:
        raise ValueError("Unknown background suppressor implementation")
    actual = settings.SEPARATION_ENGINE if engine is None else engine
    if actual == "realtime":
        actual = "dsp"
    if actual not in {"dsp", "roformer", "none"}:
        raise ValueError("Unknown background separation engine")
    payload = {"actual": actual,
               "ffmpeg": run_media(["ffmpeg", "-version"], cancel_check,
                                   capture_output=True).decode("utf-8", "replace"),
               "packages": {"numpy": _package_version("numpy")}}
    if actual == "dsp":
        payload.update({"mode": settings.SUPPRESSION_MODE,
                        # suppression_level_db is last-run output telemetry,
                        # not an input to process_file's DSP filter selection.
                        "analysis_timeout": suppressor.analysis_timeout})
    elif actual == "roformer":
        from core.engines.separator.roformer_engine import BSRoFormerSeparator
        model = settings.BASE_DIR / "workspace/models/audio_separator" / settings.ROFORMER_MODEL
        if not model.is_file():
            raise ValueError("Missing RoFormer checkpoint")
        payload.update({"model_name": settings.ROFORMER_MODEL,
                        "weights": _file_identity(model, cancel_check),
                        "configuration": {p.name: _file_identity(p, cancel_check)
                            for p in model.parent.iterdir()
                            if p.is_file() and p.suffix.lower() in {".yaml", ".yml", ".json"}},
                        "device": settings.DEVICE,
                        "segment_size": BSRoFormerSeparator.SEGMENT_SIZE,
                        "step_seconds": float(getattr(settings, "ROFORMER_STEP_SECONDS",
                                                       BSRoFormerSeparator.STEP_SECONDS))})
        payload["packages"].update({name: _package_version(name) for name in
                                   ("torch", "audio-separator", "onnxruntime", "onnxruntime-gpu")})
    return payload


def build_background_identity(video_path, duration, suppressor, cancel_check=None, engine=None):
    """Return a content/configuration key, or ``None`` when reuse is unsafe."""
    _check_cancel(cancel_check)
    try:
        duration = float(duration)
        if not math.isfinite(duration) or duration <= 0:
            return None
        source = _file_identity(video_path, cancel_check)
        payload = {"schema": SCHEMA, "source": source, "duration": duration,
                   "sample_rate": 44100, "channels": 2,
                   "engine": _engine_payload(engine, suppressor, cancel_check),
                   "implementation": _implementation(cancel_check)}
        return BackgroundCacheIdentity(hashlib.sha256(_json(payload)).hexdigest(), duration)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        _check_cancel(cancel_check)
        return None


def _directory():
    return Path(settings.WORKSPACE_DIR) / "cache" / "export_background"


def _probe(path, cancel_check=None):
    raw = run_media(["ffprobe", "-v", "error", "-show_entries",
                     "format=duration:stream=codec_type,codec_name,channels,sample_rate",
                     "-of", "json", str(path)], cancel_check, capture_output=True)
    metadata = json.loads(raw)
    streams = metadata["streams"]
    if len(streams) != 1 or streams[0].get("codec_type") != "audio":
        return None
    rate, channels = int(streams[0].get("sample_rate", 0)), int(streams[0].get("channels", 0))
    actual = float(metadata["format"]["duration"])
    if rate != 44100 or channels != 2 or not math.isfinite(actual):
        return None
    return actual, rate, channels, streams[0].get("codec_name")


def _valid_media(path, identity, cancel_check=None, *, require_flac=False):
    _check_cancel(cancel_check)
    try:
        info = _probe(path, cancel_check)
        if (info is None or abs(info[0] - identity.duration) > max(.05, 1 / identity.sample_rate)
                or (require_flac and info[3] != "flac")):
            return False
        run_media(["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-i", str(path),
                   "-map", "0:a:0", "-f", "null", "-"], cancel_check)
        return True
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        _check_cancel(cancel_check)
        return False


def _copy(source, destination, cancel_check=None):
    while chunk := source.read(1024 * 1024):
        _check_cancel(cancel_check)
        destination.write(chunk)


def load_background_cache(identity, task_dir, cancel_check=None):
    """Copy a fully verified stem into a task scratch directory."""
    if identity is None:
        return None
    _check_cancel(cancel_check)
    cached = _directory() / f"{identity.key}.bg"
    target = Path(task_dir) / "cached_background.flac"
    loaded = False
    try:
        with _LOCK:
            if cached.is_symlink() or not cached.is_file() or cached.stat().st_size > MAX_ENTRY_BYTES:
                return None
            with zipfile.ZipFile(cached) as record:
                if sorted(record.namelist()) != ["background.flac", "manifest.json"]:
                    return None
                if any(info.compress_type != zipfile.ZIP_STORED or info.flag_bits & 1 for info in record.infolist()):
                    return None
                if record.getinfo("manifest.json").file_size > MAX_MANIFEST_BYTES:
                    return None
                manifest = json.loads(record.read("manifest.json"))
                if (not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA or manifest.get("kind") != "background_stem"
                        or manifest.get("verified") is not True or manifest.get("identity") != identity.key
                        or manifest.get("duration") != identity.duration
                        or manifest.get("sample_rate") != identity.sample_rate
                        or manifest.get("channels") != identity.channels):
                    return None
                info = record.getinfo("background.flac")
                if info.file_size <= 0 or info.file_size > MAX_ENTRY_BYTES or record.getinfo("manifest.json").file_size > MAX_MANIFEST_BYTES:
                    return None
                with record.open("background.flac") as source, target.open("wb") as destination:
                    _copy(source, destination, cancel_check)
        if _digest(target, cancel_check) != manifest.get("sha256") or not _valid_media(target, identity, cancel_check, require_flac=True):
            return None
        _check_cancel(cancel_check)
        loaded = True
        try:
            cached.touch()
        except OSError:
            pass
        return target
    except (OSError, ValueError, TypeError, KeyError, EOFError, zipfile.BadZipFile):
        return None
    finally:
        if not loaded:
            target.unlink(missing_ok=True)


def store_background_cache(identity, path, cancel_check=None):
    """Validate and atomically publish one stem; best effort on cache errors."""
    if identity is None:
        return False
    _check_cancel(cancel_check)
    temporary = None
    scratch = None
    try:
        source = Path(path)
        if not source.is_file() or source.stat().st_size <= 0:
            return False
        if not _valid_media(source, identity, cancel_check):
            return False
        scratch = tempfile.TemporaryDirectory(prefix="background-cache-")
        archive_audio = Path(scratch.name) / "background.flac"
        run_media(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(source), "-vn",
                   "-c:a", "flac", "-compression_level", "5", "-fs",
                   str(MAX_ENTRY_BYTES - MAX_MANIFEST_BYTES), str(archive_audio)], cancel_check)
        if (not archive_audio.is_file()
                or archive_audio.stat().st_size > MAX_ENTRY_BYTES - MAX_MANIFEST_BYTES
                or not _valid_media(archive_audio, identity, cancel_check, require_flac=True)):
            return False
        digest = _digest(archive_audio, cancel_check)
        manifest = {"schema": SCHEMA, "kind": "background_stem", "verified": True,
                    "identity": identity.key, "duration": identity.duration,
                    "sample_rate": identity.sample_rate, "channels": identity.channels,
                    "sha256": digest}
        with _LOCK:
            directory = _directory()
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / f"{identity.key}.bg"
            with tempfile.NamedTemporaryFile(prefix="background-", suffix=".tmp", dir=directory, delete=False) as out:
                temporary = Path(out.name)
                with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED) as record:
                    record.writestr("manifest.json", _json(manifest))
                    with archive_audio.open("rb") as src, record.open("background.flac", "w") as dst:
                        _copy(src, dst, cancel_check)
                out.flush()
                os.fsync(out.fileno())
            if temporary.stat().st_size > MAX_ENTRY_BYTES:
                return False
            entries = []
            for item in directory.iterdir():
                if _NAME.fullmatch(item.name) and item.is_file() and not item.is_symlink():
                    entries.append((item.stat().st_mtime_ns, item, item.stat().st_size))
            total = sum(size for _, item, size in entries if item != destination)
            for _, item, size in sorted(entries):
                if total + temporary.stat().st_size <= MAX_CACHE_BYTES:
                    break
                if item != destination:
                    item.unlink(missing_ok=True)
                    total -= size
            if total + temporary.stat().st_size > MAX_CACHE_BYTES:
                return False
            _check_cancel(cancel_check)
            temporary.replace(destination)
        temporary = None
        return True
    except (OSError, ValueError, TypeError, RuntimeError):
        _check_cancel(cancel_check)
        return False
    finally:
        if scratch is not None:
            scratch.cleanup()
        if temporary is not None:
            temporary.unlink(missing_ok=True)
