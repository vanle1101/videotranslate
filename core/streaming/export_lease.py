"""A review-only coordinator may lend its stable media revision to export.

The boundary and lease are runtime ownership, never persisted review approval.
The pipeline owns the boundary; the exporter owns and releases the lease.
"""
import asyncio
import math
import wave

from core.streaming.output_gate import missing_spoken_output_ids
from core.streaming.audio_cache import resolve_dub_timing


def review_export_ready(session):
    if (not getattr(session, "_review_export_boundary", False)
            or not getattr(session, "is_running", False)
            or getattr(session, "is_paused", False) or getattr(session, "is_stopped", False)
            or getattr(session, "_stop_draining", False) or getattr(session, "is_editing", False)
            or getattr(session, "error", None)
            or getattr(session, "review_summary", {}).get("status") == "running"
            or getattr(session, "translation_mode", None) != "full"
            or not getattr(session, "_visual_prepass_complete", False)
            or not getattr(session, "segments", None)
            or getattr(session, "_source_prepared_seconds", 0) + .001 < session.total_duration
            or getattr(session, "_visual_completed_seconds", 0) + .001 < session.total_duration):
        return False
    records = getattr(session, "_retry_schedule", [])
    if not records or any(row.get("stage") != "review" or row.get("state") != "RETRY_PENDING" for row in records):
        return False
    queue = getattr(session, "queue", None)
    if queue is None or not queue.empty():
        return False
    if any(row.get("state") != "COMPLETED" for row in getattr(session, "_chunk_jobs", [])):
        return False
    if missing_spoken_output_ids(session.segments.values()):
        return False
    media_gate = getattr(session, "final_media_metadata", None)
    if not callable(media_gate) or media_gate().get("final_media_blocked") is not False:
        return False
    audio_duration = getattr(session, "_cached_dub_audio_duration", None)
    if not callable(audio_duration):
        return False
    try:
        timeline = sorted(((*resolve_dub_timing({key: getattr(row, key, None) for key in
            ("start", "end", "dub_start", "dub_end", "dub_tail_limit")}), row)
            for row in session.segments.values() if not getattr(row, "confirmed_silence", False)), key=lambda item: item[0])
        # Match the renderer's 44.1 kHz sample timeline. Slot ends are capacity,
        # not occupied speech: a short WAV may safely end before the next slot
        # starts even when the allocated intervals overlap.
        rate, position = 44100, 0
        total_frames = round(session.total_duration * rate)
        for start, end, row in timeline:
            if not all(math.isfinite(value) for value in (start, end)):
                return False
            first, last = round(start * rate), round(end * rate)
            if last > total_frames + 1:
                return False
            last = min(last, total_frames)
            duration = audio_duration(row.audio_path)
            if not math.isfinite(duration) or duration <= 0 or first < position or last <= first:
                return False
            frames = round(duration * rate)
            if frames > last - first + 1:
                return False
            position = first + min(frames, last - first)
    except (OSError, EOFError, wave.Error, ValueError, TypeError, KeyError, ZeroDivisionError):
        return False
    return True


def acquire_review_export(session):
    existing = getattr(session, "_review_export_lease", None)
    if existing is not None and not existing.done():
        return None
    if not review_export_ready(session):
        return None
    lease = asyncio.get_running_loop().create_future()
    session._review_export_lease = lease
    return lease


def review_export_leased(session):
    lease = getattr(session, "_review_export_lease", None)
    return lease is not None and not lease.done()


async def wait_review_export(session):
    """Drain a borrowed media revision before admitting review or cancellation.

    Cancelling the coordinator cannot cancel the exporter's lease Future. The
    exporter releases it only after its native media worker has stopped.
    """
    cancelled = False
    while review_export_leased(session):
        lease = session._review_export_lease
        try:
            await asyncio.shield(lease)
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError


def release_review_export(session, lease):
    if lease is not None and getattr(session, "_review_export_lease", None) is lease:
        if not lease.done():
            lease.set_result(None)
        session._review_export_lease = None
        session._review_export_owner = None
