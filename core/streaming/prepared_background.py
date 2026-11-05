"""Reuse complete, proven DSP preview intervals without another suppression pass."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace

from config import settings
from core.media_process import run_media


def background_provenance(suppressor):
    """Fingerprint loaded DSP code, not newer source files on disk after updates."""
    from core.engines.separator.realtime_suppressor import RealtimeVocalSuppressor
    from core.streaming.source_revision import _code
    if type(suppressor) is not RealtimeVocalSuppressor:
        return None
    # suppression_level_db is last-result telemetry (-20/-26), not an input
    # to this implementation's fixed filter graph. Do not key on that mutable
    # value or a mono interval would invalidate all following intervals.
    return {"schema": 1, "engine": "dsp", "mode": settings.SUPPRESSION_MODE,
            "implementation": {name: _code(value) for name, value in
                               vars(RealtimeVocalSuppressor).items() if callable(value)}}


def _check_cancel(cancel_check):
    if cancel_check and cancel_check():
        raise RuntimeError("Tác vụ đã bị hủy bởi người dùng.")


def _validated_records(video_path, task_id, duration, cancel_check=None):
    _check_cancel(cancel_check)
    if settings.SEPARATION_ENGINE not in ("dsp", "realtime"):
        return None
    if (not isinstance(task_id, str) or not task_id or task_id in (".", "..")
            or any(char in task_id for char in "/\\:")):
        return None
    try:
        duration = float(duration)
        if not math.isfinite(duration) or duration <= 0:
            return None
        from core.engines.separator.realtime_suppressor import RealtimeVocalSuppressor
        from core.streaming.chunked_source import _load, _media_identity
        provenance = background_provenance(RealtimeVocalSuppressor())
        root = Path(settings.BASE_DIR) / "workspace" / "cache" / task_id
        if root.is_symlink() or not root.is_dir() or (root / "source_preparation").is_symlink():
            return None
        session = SimpleNamespace(video_path=Path(video_path), cache_dir=root, total_duration=duration)
        source_identity = _media_identity(session)
        records, cursor = [], 0.0
        # Bound malformed chains independently of video duration.
        while cursor < duration - .001:
            _check_cancel(cancel_check)
            record = _load(session, cursor, background_only=True)
            if (record is None or record.get("background_provenance") != provenance
                    or record["end"] - cursor < .001 or len(records) >= 100000):
                return None
            records.append(record)
            cursor = record["end"]
        if not records or abs(cursor - duration) > .001:
            return None
        if _media_identity(session) != source_identity:
            return None
        return session, source_identity, records
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        _check_cancel(cancel_check)
        return None


def _records_identity(source_identity, records):
    payload = {"source": source_identity, "provenance": records[0]["background_provenance"],
               "intervals": [[record["start"], record["end"], record["bgm"]["sha256"]] for record in records]}
    # Recognition settings do not affect the already-produced DSP packets.
    payload["source"] = {key: value for key, value in source_identity.items() if key not in ("model", "compute")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def prepared_background_identity(video_path, task_id, duration, cancel_check=None):
    """Prove the packet chain for cache keys without decoding or creating files.

    Actual media decoding/duration validation is performed by the loader before
    first publication; the digest binds subsequent cache use to those bytes.
    """
    validated = _validated_records(video_path, task_id, duration, cancel_check)
    if validated is None:
        return None
    _, source_identity, records = validated
    return _records_identity(source_identity, records)


def load_prepared_background(video_path, task_id, duration, task_dir, cancel_check=None):
    """Return (owned full-length background, metadata), or None on any cache miss.

    Each source interval must be contiguous and prove its input identity, asset
    hashes, DSP configuration and implementation. Legacy records without DSP
    provenance deliberately miss. An incomplete prefix never becomes silence
    over the unprepared tail. RoFormer output cannot be replaced with DSP.
    """
    validated = _validated_records(video_path, task_id, duration, cancel_check)
    if validated is None:
        return None
    session, source_identity, records = validated
    duration = session.total_duration
    from core.streaming.chunked_source import _load, _media_identity
    try:
        task_dir = Path(task_dir)
        task_dir.mkdir(parents=True, exist_ok=True)
        target = task_dir / "prepared_background.wav"
        # Decode independently: Opus encoder pre-skip must not accumulate at
        # interval boundaries. Normalize each interval to its measured offsets.
        rate, frame_bytes = 44100, 4
        with tempfile.TemporaryDirectory(prefix="prepared_background_", dir=task_dir) as temporary:
            temporary = Path(temporary)
            combined = temporary / "background.pcm"
            with combined.open("wb") as output:
                for record in records:
                    _check_cancel(cancel_check)
                    pcm = temporary / "interval.pcm"
                    run_media(["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-y", "-i", record["bgm"]["path"],
                               "-vn", "-ac", "2", "-ar", str(rate), "-f", "s16le", str(pcm)], cancel_check)
                    measured = round(record["end"] * rate) - round(record["start"] * rate)
                    size = pcm.stat().st_size
                    if size % frame_bytes or abs(size / frame_bytes - measured) > rate * .02:
                        return None
                    remaining = measured * frame_bytes
                    with pcm.open("rb") as handle:
                        while remaining:
                            _check_cancel(cancel_check)
                            chunk = handle.read(min(1024 * 1024, remaining))
                            if not chunk:
                                # Only a verified sub-20ms encoder tail may be padded.
                                output.write(b"\0" * remaining)
                                break
                            output.write(chunk)
                            remaining -= len(chunk)
            pending = temporary / "background.wav"
            run_media(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "s16le", "-ar", str(rate), "-ac", "2",
                       "-i", str(combined), "-c:a", "pcm_s16le", "-rf64", "auto", str(pending)], cancel_check)
            measured = float(run_media(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                       "-of", "default=noprint_wrappers=1:nokey=1", str(pending)],
                                       cancel_check, capture_output=True).strip())
            if not math.isfinite(measured) or abs(measured - duration) > .001:
                return None
            # Recheck immutable packets/source after decoding, before publication.
            if _media_identity(session) != source_identity:
                return None
            for record in records:
                _check_cancel(cancel_check)
                if _load(session, record["start"], background_only=True) != record:
                    return None
            _check_cancel(cancel_check)
            pending.replace(target)
        identity = _records_identity(source_identity, records)
        return target, {"separation_engine": "dsp", "warnings": [],
                        "background_source": "prepared_intervals", "background_intervals": len(records),
                        "background_interval_digest": identity}
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        _check_cancel(cancel_check)
        return None
