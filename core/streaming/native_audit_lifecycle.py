"""An independent durable owner for audits of already playable native drafts."""
import asyncio
from copy import deepcopy
import logging
import time

from core.streaming.native_audit_retry import pending_native_audit, audit_existing_native_rows


def pending_rows(session):
    return [(row, marker) for row in getattr(session, "segments", {}).values()
            if (marker := pending_native_audit(row)) is not None]


def has_pending(session):
    return bool(pending_rows(session))


def sync_deadlines(session, *, now=None, resuming=False):
    now = time.time() if now is None else now
    for row, pending in pending_rows(session):
        marker = row.verification["multilingual_review"]["pending_audit"]
        if resuming:
            if marker["remaining_seconds"] > 0:
                marker["next_attempt_at"] = now + marker["remaining_seconds"]
            marker["remaining_seconds"] = 0.
        elif not getattr(session, "is_paused", False):
            marker["remaining_seconds"] = max(0., marker["next_attempt_at"] - now)


def _source_stamp(session):
    path = session.video_path
    stat = path.stat()
    return (str(path.resolve()), stat.st_size, stat.st_mtime_ns,
            getattr(session, "_chunked_source_identity", None))


def ensure_native_audit_retry(session):
    owner = getattr(session, "native_audit_task", None)
    if owner is not None and not owner.done():
        return owner
    if (session.is_stopped or session.is_running or session.run_intent != "running"
            or not has_pending(session) or getattr(session, "_persistence_capacity_failed", False)):
        return None
    owner = asyncio.create_task(_run(session), name=f"native-audit-{session.task_id}")
    session.native_audit_task = owner
    return owner


async def _run(session):
    """Leave media status/audio untouched; only current semantic evidence commits."""
    try:
        stamp = _source_stamp(session)
        from core.streaming.source_identity import local_file_signature
        saved_signature = getattr(session, "_source_file_signature", None)
        if saved_signature and saved_signature != local_file_signature(session.video_path):
            for row, _ in pending_rows(session):
                row.verification["multilingual_review"].pop("pending_audit", None)
            return
        while not session.is_stopped and session.run_intent in {"running", "paused"}:
            rows = pending_rows(session)
            if not rows:
                break
            if session.is_paused or not session.pause_event.is_set():
                await asyncio.sleep(.25)
                continue
            # Speech/review/edit/export owners must finish before a verdict is
            # published. Waiting here never makes media ineligible for export.
            busy = session.is_running or session.is_editing or any(
                task is not None and not task.done() for task in
                (getattr(session, "review_task", None), getattr(session, "export_task", None),
                 getattr(session, "auto_export_task", None)))
            if busy:
                await asyncio.sleep(.25)
                continue
            if _source_stamp(session) != stamp:
                for item, _ in rows:
                    item.verification["multilingual_review"].pop("pending_audit", None)
                break
            row, marker = min(rows, key=lambda item: item[1]["next_attempt_at"])
            if marker.get("remaining_seconds", 0.) > 0:
                sync_deadlines(session, resuming=True)
                session._persist_if_enabled()
                continue
            delay = marker["next_attempt_at"] - time.time()
            if delay > 0:
                await asyncio.sleep(min(.25, delay))
                continue
            revision, snapshot = row.revision, deepcopy(row.to_dict())
            from config import settings
            configuration = (settings.LLM_PROVIDER, settings.OPENCODE_MODEL)
            result = await session._run_blocking(audit_existing_native_rows, [snapshot],
                cancel_check=lambda: session.is_stopped)
            await session._wait_for_edit_publication()
            while not session.is_stopped and (session.is_paused or session.is_running or any(
                    task is not None and not task.done() for task in
                    (getattr(session, "review_task", None), getattr(session, "export_task", None),
                     getattr(session, "auto_export_task", None)))):
                await asyncio.sleep(.25)
            current = pending_native_audit(row)
            if (session.is_stopped or _source_stamp(session) != stamp
                    or session.segments.get(row.id) is not row or row.revision != revision
                    or current != marker):
                continue
            if configuration != (settings.LLM_PROVIDER, settings.OPENCODE_MODEL):
                row.verification["multilingual_review"].pop("pending_audit", None)
                session._persist_if_enabled()
                continue
            reviewed = result["segments"].get(row.id)
            if reviewed is None:
                break
            # The audit-only adapter retains every Vietnamese field. Reject a
            # violated contract before it can invalidate existing speech.
            if any(reviewed.get(key) != snapshot.get(key) for key in
                   ("text_zh", "literal_vi", "natural_vi", "final_vi", "start", "end")):
                raise ValueError("Native audit attempted to change playable content")
            with session._durable_speaker_review_publication(row):
                row.verification = deepcopy(reviewed["verification"])
                row.needs_review = reviewed["needs_review"]
                row.review_reason = reviewed["review_reason"]
            await session.emit("segment_update", row.to_dict())
    except asyncio.CancelledError:
        raise
    except Exception as error:
        logging.getLogger("errors").error("NATIVE_AUDIT_RETRY_FAILED run_id=%s error_type=%s",
                                          session.task_id, type(error).__name__)
    finally:
        if not session.is_stopped and not has_pending(session) and not session.is_running:
            session.run_intent = "idle"
        session._persist_if_enabled()
        session.native_audit_task = None
        session._release_runtime()
