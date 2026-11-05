"""Measured media coverage and worker liveness, independent of UI polling.

An unfinished asyncio owner proves that work is still scheduled, not that its
provider is making progress. Expose long silence as waiting, never invent a
heartbeat from repeated HTTP requests or turn a coverage ratio into final 100%.
"""
import math
import time


QUIET_SECONDS = 180.
SOURCE_STAGES = {
    "identity": "Đang kiểm tra video nguồn",
    "checkpoint_load": "Đang kiểm tra phần nguồn đã lưu",
    "audio_extract": "Đang trích âm thanh đoạn tiếp theo",
    "asr": "Đang nhận diện lời nói đoạn tiếp theo",
    "diarization": "Đang phân biệt giọng nói",
    "segment": "Đang chia câu theo lời nói",
    "background_extract": "Đang chuẩn bị âm thanh nền",
    "background_suppress": "Đang lọc thoại gốc và lưu nhạc nền",
    "source_validate": "Đang kiểm tra dữ liệu nguồn",
    "checkpoint_write": "Đang lưu tiến độ nguồn",
}


def mark_activity(session, *, now=None):
    """Call only for a real work event; clocks are deliberately not persisted."""
    session._activity_last_at = time.monotonic() if now is None else now
    session._activity_run = getattr(session, "_run_started_at", None)


def _percent(value, total):
    if (type(total) not in (int, float) or not math.isfinite(total) or total <= 0
            or type(value) not in (int, float) or not math.isfinite(value)):
        return None
    return round(min(100., max(0., value * 100 / total)), 1)


def progress_activity(session, snapshot, *, now=None):
    now = time.monotonic() if now is None else now
    run = getattr(session, "_run_started_at", None)
    source_active = bool(getattr(session, "_source_preparation_active", False))
    source_stage = getattr(session, "_source_preparation_stage", None) if source_active else None
    # Capture changes between emitted events too (chunk commits, failed rows).
    # Elapsed time, ETA and shared/global AI queue counters are not work evidence.
    signature = (
        run, source_stage, snapshot.get("phase"), snapshot.get("stage"),
        snapshot.get("source_prepared_seconds"), snapshot.get("processed_seconds"),
        snapshot.get("processing_completed_segments"), snapshot.get("processing_total_segments"),
        tuple(sorted(snapshot.get("segment_states", {}).items())),
        snapshot.get("completed_chunks"), snapshot.get("retry_chunks"),
        *(snapshot.get(key) for key in ("asr_completed_seconds", "speaker_completed_frames",
                                      "batch_progress_pct", "stage_progress_pct", "progress_pct")),
    )
    previous = getattr(session, "_activity_signature", None)
    if getattr(session, "_activity_run", None) != run:
        session._activity_last_at = run
        session._activity_run = run
        previous = None
    if previous is not None and signature != previous:
        mark_activity(session, now=now)
    session._activity_signature = signature
    last = getattr(session, "_activity_last_at", run)
    age = round(max(0., now - last), 1) if last is not None else None
    owners = [getattr(session, name, None) for name in (
        "start_task", "worker_task", "review_task", "_chunk_followup_task",
        "export_task", "auto_export_task", "_source_preparation_task")]
    owners.extend(getattr(session, "edit_tasks", ()))
    live = any(owner is not None and not owner.done() for owner in owners)
    status = snapshot.get("status")
    if status in {"CANCELLING", "STOPPED", "FAILED", "PAUSED"}:
        state = {"CANCELLING": "stopping", "STOPPED": "stopped", "FAILED": "failed", "PAUSED": "paused"}[status]
    elif status in {"COMPLETED", "PREVIEW_READY"} and not session.is_running:
        state = "completed"
    elif session.is_running or live:
        state = "stalled" if not live else "waiting" if age is not None and age >= QUIET_SECONDS else "active"
    else:
        state = "idle"
    total = snapshot.get("total_seconds")
    return {
        "media_translation_pct": _percent(snapshot.get("processed_seconds"), total),
        "media_ready_pct": _percent(snapshot.get("processing_ready_seconds"), total),
        "source_progress_pct": _percent(snapshot.get("source_prepared_seconds"), total),
        "activity_state": state, "activity_age_seconds": age,
        "activity_has_worker": live, "activity_quiet_threshold_seconds": QUIET_SECONDS,
        "activity_stage": SOURCE_STAGES.get(source_stage, snapshot.get("stage") or ""),
        "source_activity_stage": source_stage,
    }
