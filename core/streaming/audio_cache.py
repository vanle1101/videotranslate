"""Bounded, validated reuse of the final audio mix across caption-only exports.

Each record is one atomically published manifest + lossless FLAC archive. Media
is hashed and copied in chunks; neither long PCM tracks nor archives enter RAM.
Only a successful video export may commit its prepared audio record.
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
from core.engines.alignment.dub_timing import MAX_TAIL_LIMIT_EXTENSION


SCHEMA = 1
MAX_ENTRY_BYTES = 64 * 1024 * 1024
MAX_CACHE_BYTES = 96 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
_OWNED_NAME = re.compile(r"[0-9a-f]{64}\.mix\Z")
_LOCK = threading.RLock()


def resolve_dub_timing(segment):
    """Resolve validated narration bounds without changing the source timeline."""
    def valid(value):
        return type(value) in (int, float) and math.isfinite(value) and value >= 0

    start, end = segment.get("start"), segment.get("end")
    if not valid(start) or not valid(end) or end <= start:
        raise ValueError("Mốc thời gian nguồn của câu thoại không hợp lệ.")
    dub_start, dub_end = segment.get("dub_start"), segment.get("dub_end")
    tail_limit = segment.get("dub_tail_limit")
    if tail_limit is not None and (not valid(tail_limit)
                                   or tail_limit < end - 1e-9
                                   or tail_limit > end + MAX_TAIL_LIMIT_EXTENSION + 1e-9):
        raise ValueError("Bằng chứng khoảng nghỉ lồng tiếng không hợp lệ.")
    if dub_start is None and dub_end is None:
        return float(start), float(end)
    end_limit = end + .35
    if tail_limit is not None:
        end_limit = max(end_limit, float(tail_limit))
    if dub_end is not None and valid(dub_end) and dub_end > end + .35 + 1e-9 and tail_limit is None:
        raise ValueError("Mốc lồng tiếng không hợp lệ hoặc thiếu bằng chứng khoảng nghỉ.")
    if (not valid(dub_start) or not valid(dub_end) or dub_end <= dub_start
            or abs(dub_start - start) > .35 + 1e-9
            or dub_end > end_limit + 1e-9
            or dub_end < end - .35 - 1e-9):
        raise ValueError("Mốc lồng tiếng không hợp lệ hoặc lệch quá 0,35 giây so với nguồn.")
    return float(dub_start), float(dub_end)


@dataclass(frozen=True)
class AudioCacheIdentity:
    key: str
    duration: float


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
        raise ValueError("Audio cache input is not a nonempty file.")
    digest = _digest(path, cancel_check)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Audio cache input changed during hashing.")
    return {"path": str(path), "size": after.st_size, "mtime": after.st_mtime_ns, "sha256": digest}


def _package_version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def build_audio_cache_identity(video_path, segments, duration, mixer, suppressor, cancel_check=None):
    """Missing/unidentified input disables reuse; it never fabricates valid audio.

    Caption text/color/position/blur are absent. Narration bytes, revisions and
    placement, the exact source, mixer parameters, separator/model/runtime and
    implementation fingerprints all participate in the key. Unknown adapters
    opt out rather than sharing a production cache by a friendly engine name.
    """
    _check_cancel(cancel_check)
    from core.audio_ducking import PremiumAudioMixer
    from core.engines.separator.realtime_suppressor import RealtimeVocalSuppressor
    if type(mixer) is not PremiumAudioMixer or type(suppressor) is not RealtimeVocalSuppressor:
        return None
    try:
        duration = float(duration)
        if not math.isfinite(duration) or duration <= 0:
            return None
        rows = []
        for segment in segments:
            dub_start, dub_end = resolve_dub_timing(segment)
            rows.append({"start": segment["start"], "end": segment["end"],
                         "dub_start": dub_start, "dub_end": dub_end,
                         "revision": segment.get("revision", 0),
                         "audio": _file_identity(segment["audio_path"], cancel_check)
                         if segment.get("audio_path") else None,
                         "has_translation": bool(segment.get("final_vi") or segment.get("text_vi"))})
        core_dir = Path(__file__).resolve().parents[1]
        files = [Path(__file__), core_dir / "streaming/export.py", core_dir / "audio_ducking.py",
                 core_dir / "media_process.py", core_dir / "engines/separator/realtime_suppressor.py",
                 core_dir / "engines/separator/roformer_engine.py"]
        runtime = {"ffmpeg": run_media(["ffmpeg", "-version"], cancel_check, capture_output=True).decode("utf-8", "replace"),
                   "numpy": _package_version("numpy")}
        model = None
        if settings.SEPARATION_ENGINE == "roformer":
            runtime.update({name: _package_version(name) for name in ("audio-separator", "torch", "onnxruntime", "onnxruntime-gpu")})
            model_path = settings.BASE_DIR / "workspace/models/audio_separator" / settings.ROFORMER_MODEL
            # No downloaded checkpoint means no stable RoFormer identity yet.
            if not model_path.is_file():
                return None
            model = {"weights": _file_identity(model_path, cancel_check),
                     "configuration": {p.name: _file_identity(p, cancel_check) for p in model_path.parent.iterdir()
                                       if p.is_file() and p.suffix.lower() in {".yaml", ".yml", ".json"}}}
        payload = {"schema": SCHEMA, "source": _file_identity(video_path, cancel_check),
                   "duration": duration, "segments": rows,
                   "separation": settings.SEPARATION_ENGINE, "suppression": settings.SUPPRESSION_MODE,
                   "roformer_model": settings.ROFORMER_MODEL, "model": model, "device": settings.DEVICE,
                   "mixer": {name: getattr(mixer, name) for name in
                             ("voice_gain_db", "bgm_gain_db", "duck_amount_db", "attack_ms", "release_ms")},
                   "suppression_level_db": suppressor.suppression_level_db,
                   "runtime": runtime, "implementation": {str(p.relative_to(core_dir)): _digest(p, cancel_check) for p in files}}
        return AudioCacheIdentity(hashlib.sha256(_json(payload)).hexdigest(), duration)
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _directory():
    return Path(settings.WORKSPACE_DIR) / "cache" / "export_audio"


def _valid_audio(path, duration, cancel_check=None):
    _check_cancel(cancel_check)
    try:
        metadata = json.loads(run_media(["ffprobe", "-v", "error", "-show_entries",
            "format=duration:stream=codec_type,codec_name,channels,sample_rate", "-of", "json", str(path)],
            cancel_check, capture_output=True))
        streams = metadata["streams"]
        actual = float(metadata["format"]["duration"])
        if (len(streams) != 1 or streams[0].get("codec_type") != "audio"
                or streams[0].get("codec_name") != "flac" or streams[0].get("channels") != 2
                or int(streams[0].get("sample_rate", 0)) != 44100 or not math.isfinite(actual)
                or abs(actual - duration) > max(.05, 1 / 44100)):
            return False
        # Full stream validation, not just a valid header or first frame.
        run_media(["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-i", str(path),
                   "-map", "0:a:0", "-f", "null", "-"], cancel_check)
        return True
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        _check_cancel(cancel_check)
        return False


def _valid_metadata(metadata):
    return (isinstance(metadata, dict) and set(metadata) == {"separation_engine", "warnings"}
            and isinstance(metadata["separation_engine"], str)
            and metadata["separation_engine"] in {"none", "dsp", "roformer"}
            and isinstance(metadata["warnings"], list)
            and len(metadata["warnings"]) <= 10
            and all(isinstance(value, str) and len(value) < 2000 for value in metadata["warnings"]))


def _copy(source, destination, cancel_check=None):
    while chunk := source.read(1024 * 1024):
        _check_cancel(cancel_check)
        destination.write(chunk)


def load_audio_cache(identity, task_dir, cancel_check=None):
    """Copy and validate a hit in this export's scratch directory before use."""
    if identity is None:
        return None
    _check_cancel(cancel_check)
    cached = _directory() / f"{identity.key}.mix"
    audio = Path(task_dir) / "cached_master.flac"
    loaded = False
    try:
        with _LOCK:
            if cached.is_symlink() or not cached.is_file() or cached.stat().st_size > MAX_ENTRY_BYTES:
                return None
            with zipfile.ZipFile(cached) as record:
                if sorted(record.namelist()) != ["manifest.json", "mix.flac"]:
                    return None
                if any(info.compress_type != zipfile.ZIP_STORED or info.flag_bits & 1 for info in record.infolist()):
                    return None
                if (record.getinfo("manifest.json").file_size > MAX_MANIFEST_BYTES
                        or record.getinfo("mix.flac").file_size > MAX_ENTRY_BYTES):
                    return None
                manifest = json.loads(record.read("manifest.json"))
                if (not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA or manifest.get("identity") != identity.key
                        or manifest.get("duration") != identity.duration or not _valid_metadata(manifest.get("metadata"))):
                    return None
                with record.open("mix.flac") as source, audio.open("wb") as target:
                    _copy(source, target, cancel_check)
        if (_digest(audio, cancel_check) != manifest.get("sha256")
                or not _valid_audio(audio, identity.duration, cancel_check)):
            return None
        _check_cancel(cancel_check)
        loaded = True
        try:
            cached.touch()
        except OSError:
            pass
        return audio, manifest["metadata"]
    except (OSError, ValueError, TypeError, KeyError, EOFError, zipfile.BadZipFile):
        return None
    finally:
        if not loaded:
            audio.unlink(missing_ok=True)


def prepare_audio_cache(identity, audio_path, metadata, task_dir, cancel_check=None):
    """Prepare a small lossless record, but publish nothing before video success."""
    if identity is None or not _valid_metadata(metadata):
        return None
    _check_cancel(cancel_check)
    flac = Path(task_dir) / "cache_master.flac"
    pending = Path(task_dir) / "audio_cache.mix"
    prepared = False
    try:
        if not Path(audio_path).is_file():
            return None
        # Bound the extra artifact even for hours of audio. Oversized/truncated
        # encodes fail validation and simply remain uncached.
        run_media(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(audio_path), "-vn",
                   "-c:a", "flac", "-compression_level", "5", "-fs", str(MAX_ENTRY_BYTES - MAX_MANIFEST_BYTES),
                   str(flac)], cancel_check)
        if not flac.is_file() or flac.stat().st_size > MAX_ENTRY_BYTES - MAX_MANIFEST_BYTES:
            return None
        if not _valid_audio(flac, identity.duration, cancel_check):
            return None
        manifest = {"schema": SCHEMA, "identity": identity.key, "duration": identity.duration,
                    "sha256": _digest(flac, cancel_check), "metadata": metadata}
        with zipfile.ZipFile(pending, "w", compression=zipfile.ZIP_STORED) as record:
            record.writestr("manifest.json", _json(manifest))
            with flac.open("rb") as source, record.open("mix.flac", "w") as target:
                _copy(source, target, cancel_check)
        _check_cancel(cancel_check)
        prepared = pending.stat().st_size <= MAX_ENTRY_BYTES
        return pending if prepared else None
    except (OSError, ValueError, TypeError, RuntimeError):
        _check_cancel(cancel_check)
        return None
    finally:
        flac.unlink(missing_ok=True)
        if not prepared:
            pending.unlink(missing_ok=True)


def commit_audio_cache(identity, pending):
    """Best-effort atomic cache commit, called only after MP4 publication.

    An already published output wins over late cancellation. Cache failures
    must not turn that successful deliverable into an export error.
    """
    if identity is None or pending is None:
        return False
    temporary = None
    try:
        incoming = Path(pending).stat().st_size
        if incoming > min(MAX_ENTRY_BYTES, MAX_CACHE_BYTES):
            return False
        with _LOCK:
            directory = _directory()
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / f"{identity.key}.mix"
            entries = []
            for path in directory.iterdir():
                if not _OWNED_NAME.fullmatch(path.name) or path.is_symlink() or not path.is_file():
                    continue
                info = path.stat()
                entries.append((info.st_mtime_ns, path, info.st_size))
            total = sum(size for _, path, size in entries if path != destination)
            for _, path, size in sorted(entries):
                if total + incoming <= MAX_CACHE_BYTES:
                    break
                if path == destination:
                    continue
                path.unlink()
                total -= size
            if total + incoming > MAX_CACHE_BYTES:
                return False
            with tempfile.NamedTemporaryFile(prefix="audio-cache-", suffix=".tmp", dir=directory, delete=False) as target:
                temporary = Path(target.name)
                with Path(pending).open("rb") as source:
                    _copy(source, target)
                target.flush()
                os.fsync(target.fileno())
            temporary.replace(destination)
        return True
    except OSError:
        return False
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
