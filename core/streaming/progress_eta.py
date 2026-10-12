"""Measured, explicitly scoped ETAs. No assumed provider or encoding speed."""
import hashlib
import json
import logging
import math
import sqlite3
import statistics
import threading
import time
from contextlib import closing
from pathlib import Path

from config import settings

_RATE_CACHE = {}
_RATE_LOCK = threading.Lock()


def _positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def export_profile(video_size, caption_style=None):
    from core.subtitle_cues import CAPTION_RENDERER_VERSION, normalize_caption_style
    style = normalize_caption_style(caption_style)
    payload = [CAPTION_RENDERER_VERSION, list(video_size), settings.SEPARATION_ENGINE,
               settings.SUPPRESSION_MODE, style["blur_original"], style["font_family"],
               style["font_size_pt"], style["bold"]]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _database():
    return Path(settings.WORKSPACE_DIR) / "cache" / "export_performance.sqlite3"


def record_validated_export(profile, media_seconds, elapsed_seconds):
    """Call only after decoding and publishing a real uncached final export.

    Measurements are optional diagnostics: disk/cache failures cannot revoke a
    successfully published MP4. Bounded retention also prevents cache growth.
    """
    if not _positive(media_seconds) or not _positive(elapsed_seconds):
        return
    try:
        path = _database()
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=.1)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS samples (id INTEGER PRIMARY KEY, profile TEXT, media REAL, elapsed REAL)")
            db.execute("INSERT INTO samples(profile, media, elapsed) VALUES (?, ?, ?)",
                       (profile, media_seconds, elapsed_seconds))
            db.execute("DELETE FROM samples WHERE id NOT IN (SELECT id FROM samples ORDER BY id DESC LIMIT 128)")
    except (OSError, sqlite3.Error):
        logging.getLogger("pipeline").warning("EXPORT_ETA_SAMPLE_UNAVAILABLE")


def estimated_export_seconds(profile, media_seconds):
    if not _positive(media_seconds):
        return None
    path = _database()
    try:
        info = path.stat()
        key = (str(path.resolve()), profile, info.st_mtime_ns, info.st_size)
        with _RATE_LOCK:
            if key in _RATE_CACHE:
                rate = _RATE_CACHE[key]
                return rate * media_seconds if rate is not None else None
        # Progress runs on the backend event loop: never wait for an exporter
        # holding SQLite's write lock, nor query again on every UI poll.
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0)) as db:
            rows = db.execute("SELECT media, elapsed FROM samples WHERE profile=? ORDER BY id DESC LIMIT 5",
                              (profile,)).fetchall()
        rates = [elapsed / media for media, elapsed in rows if _positive(media) and _positive(elapsed)]
        rate = statistics.median(rates) if rates else None
        with _RATE_LOCK:
            if len(_RATE_CACHE) >= 64:
                _RATE_CACHE.pop(next(iter(_RATE_CACHE)))
            _RATE_CACHE[key] = rate
        return rate * media_seconds if rate is not None else None
    except (OSError, sqlite3.Error, ValueError):
        return None


def export_eta(expected_seconds, elapsed_seconds, status="RUNNING"):
    if status == "COMPLETED":
        return {"eta_seconds": 0, "eta_scope": "final", "eta_reason": "validated"}
    if status != "RUNNING":
        return {"eta_seconds": None, "eta_scope": "final", "eta_reason": "inactive"}
    remaining = expected_seconds - max(0, elapsed_seconds) if _positive(expected_seconds) else None
    return {"eta_seconds": max(.1, round(remaining, 1)) if remaining is not None and remaining > 0 else None,
            "eta_scope": "final", "eta_reason": "measured_export" if remaining is not None and remaining > 0
            else "estimate_exceeded" if remaining is not None else "insufficient_samples"}


def _clock(session, now):
    start = getattr(session, "_run_started_at", None)
    if getattr(session, "_eta_clock_run", None) != start:
        session._eta_clock_run = start
        session._eta_sample_started = start
        session._eta_ready_baseline = getattr(session, "_run_ready_baseline", 0)
        session._eta_media_baseline = 0. if session._eta_ready_baseline == 0 else None
        session._eta_pause_started = None
    return getattr(session, "_eta_sample_started", start)


def reset_eta(session, *, now=None, snapshot=None):
    """Take a fresh baseline; work completed while paused is never free work."""
    now = time.monotonic() if now is None else now
    _clock(session, now)
    if snapshot is None:
        snapshot = session._processing_progress_metadata()
    session._eta_sample_started = now
    session._eta_ready_baseline = snapshot["processing_completed_segments"]
    session._eta_media_baseline = snapshot.get("processing_ready_seconds")
    session._eta_pause_started = None


def pause_eta(session, *, now=None):
    now = time.monotonic() if now is None else now
    _clock(session, now)
    if getattr(session, "_eta_pause_started", None) is None:
        session._eta_pause_started = now


def resume_eta(session, *, now=None):
    now = time.monotonic() if now is None else now
    _clock(session, now)
    paused = getattr(session, "_eta_pause_started", None)
    if paused is not None:
        reset_eta(session, now=now)


def processing_eta(session, snapshot, *, now=None):
    """The live sample includes real queue/review/TTS latency, never idle pause.

    A final ETA is only possible once source/review dependencies are resolved
    and a validated comparable export supplies the remaining media cost.
    Otherwise expose the measured speech/preview estimate with its true scope.
    """
    now = time.monotonic() if now is None else now
    start = _clock(session, now)
    preview = session.translation_mode == "preview"
    result = {"eta_seconds": None, "eta_scope": "preview" if preview else "final",
              "eta_reason": "insufficient_samples"}
    if snapshot.get("status") == "COMPLETED" and getattr(session, "output_filename", "") and not session.caption_output_outdated:
        return {**result, "eta_seconds": 0, "eta_scope": "final", "eta_reason": "validated"}
    if snapshot.get("status") == "PREVIEW_READY":
        return {**result, "eta_seconds": 0, "eta_scope": "preview", "eta_reason": "preview_ready"}
    if session.is_paused or session.is_stopped or session.error or not session.is_running:
        return {**result, "eta_reason": "inactive"}
    counts = snapshot.get("segment_states", {})
    if counts.get("FAILED", 0) or counts.get("RETRY_PENDING", 0) or snapshot.get("retry_chunks", 0):
        return {**result, "eta_reason": "retry_pending"}
    completed = snapshot["processing_completed_segments"]
    remaining = snapshot["processing_total_segments"] - completed
    produced = completed - getattr(session, "_eta_ready_baseline", 0)
    elapsed = max(0, now - start) if start is not None else 0
    estimate = None
    total_known = snapshot.get("processing_total_known")
    if total_known:
        estimate = elapsed * remaining / produced if elapsed > 0 and produced >= 3 and remaining > 0 else None
    elif not preview and session.total_duration > 0:
        coverage = snapshot.get("processing_ready_seconds", 0)
        baseline = getattr(session, "_eta_media_baseline", None)
        if baseline is None:
            # Legacy restored sessions have no measured coverage baseline.
            # Start sampling now instead of crediting their existing files.
            reset_eta(session, now=now, snapshot=snapshot)
        else:
            gained = coverage - baseline
            # This is a minimum sample size, not an assumed provider speed.
            meaningful_sample = min(10., session.total_duration * .1)
            if produced >= 3 and gained >= meaningful_sample and elapsed > 0 and coverage < session.total_duration:
                estimate = elapsed * (session.total_duration - coverage) / gained
        if estimate is None:
            return {**result, "eta_reason": "source_unknown"}
    if estimate is not None:
        result.update(eta_seconds=max(.1, round(estimate, 1)), eta_scope="preview" if preview else "speech",
                      eta_reason="measured_processing" if total_known else "measured_media_coverage")
    if preview:
        return result
    review = getattr(session, "review_summary", {})
    review_ready = (review.get("status") == "completed" and not review.get("incomplete")
                    and not getattr(session, "_reviewing_segment_ids", [])
                    and getattr(session, "_visual_prepass_complete", False))
    if not review_ready or not total_known:
        return {**result, "eta_reason": "review_pending"}
    if (estimate is not None or remaining == 0) and getattr(session, "auto_export_result", False):
        expected_export = estimated_export_seconds(export_profile(session.video_size, session.caption_style), session.total_duration)
        if expected_export is not None:
            result.update(eta_seconds=round((estimate or 0) + expected_export, 1), eta_scope="final",
                          eta_reason="measured_processing_and_export")
        elif result["eta_seconds"] is None:
            result["eta_reason"] = "awaiting_export_sample"
    return result
