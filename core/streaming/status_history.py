"""Bounded wall-clock lifecycle journal, written by work events, never by polling."""
import math
import time

MAX_ENTRIES = 120
MAX_MESSAGE = 1800
STATUSES = frozenset({"RUNNING", "PAUSED", "CANCELLING", "STOPPED", "FAILED",
                      "COMPLETED", "PREVIEW_READY", "PREPARED"})


def validate_history(history, changed_at=None):
    if not isinstance(history, list) or len(history) > MAX_ENTRIES:
        raise ValueError("Lịch sử trạng thái không hợp lệ.")
    for row in history:
        if (not isinstance(row, dict) or set(row) != {"timestamp", "status", "phase", "message"}
                or type(row["timestamp"]) not in (int, float)
                or not math.isfinite(row["timestamp"]) or row["timestamp"] <= 0
                or not isinstance(row["status"], str) or row["status"] not in STATUSES
                or not isinstance(row["phase"], str) or len(row["phase"]) > 80
                or not isinstance(row["message"], str) or len(row["message"]) > MAX_MESSAGE):
            raise ValueError("Mốc lịch sử trạng thái không hợp lệ.")
    if changed_at is not None and (type(changed_at) not in (int, float)
            or not math.isfinite(changed_at) or changed_at <= 0):
        raise ValueError("Thời gian chuyển trạng thái không hợp lệ.")


def record_status(session, status, phase, message, *, timestamp=None):
    """Return whether a transition was added; callers own durable checkpointing.

    Sentence numbers, frame progress and polling do not generate new entries.
    A changed terminal explanation is retained, even within the same phase.
    Explicit timestamps are only for importing verified historical events.
    """
    if status not in STATUSES:
        return False
    from core.streaming.session_store import _clean
    phase = str(phase or "")[:80]
    message = _clean(str(message or "").replace("\x00", "")[:100_000])[:MAX_MESSAGE]
    history = getattr(session, "status_history", [])
    previous = history[-1] if history else None
    if previous and previous["status"] == status and previous["phase"] == phase:
        if status in {"RUNNING", "PAUSED", "CANCELLING"} or previous["message"] == message:
            return False
    timestamp = time.time() if timestamp is None else timestamp
    row = {"timestamp": timestamp, "status": status, "phase": phase, "message": message}
    validate_history([row], timestamp)
    session.status_history = [*history, row][-MAX_ENTRIES:]
    session.status_changed_at = timestamp
    return True


def record_snapshot(session, snapshot):
    status = snapshot.get("status", "RUNNING")
    message = str(snapshot.get("stage") or "")
    if status == "PAUSED":
        message = "Đã tạm dừng theo yêu cầu. " + message
    if status == "FAILED":
        # Keep the actual available causes alongside the high-level summary.
        # These are existing user-visible row errors, never raw provider payloads.
        reasons = []
        for row in getattr(session, "segments", {}).values():
            reason = getattr(row, "error", None)
            if row.status == "FAILED" and reason and reason not in reasons and reason not in message:
                reasons.append(reason)
                if len(reasons) >= 3:
                    break
        if reasons:
            message += " Chi tiết: " + " · ".join(reasons)
    return record_status(session, status, snapshot.get("phase", ""), message)


def snapshot_metadata(session):
    return {"status_history": [dict(row) for row in getattr(session, "status_history", [])],
            "status_changed_at": getattr(session, "status_changed_at", None)}
