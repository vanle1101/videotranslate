"""Durable run intent. Only explicitly running projects resume at app startup."""
import asyncio
import logging
import math
import time

from config import settings


def checkpoint_pending_retry(session, label, attempt, delay, *, now=None):
    """Save a provider cooldown as soon as its burst fails, before other work.

    A producer can continue admitting independent work before it actually waits.
    The cooldown must survive shutdown during that interval as well as shutdown
    during the eventual wait. The caller owns transient-error classification and
    applies its retry policy to ``delay``; this helper never retries source errors.
    """
    if (not getattr(session, "_persistence_enabled", False)
            or getattr(session, "is_stopped", False)
            or getattr(session, "run_intent", "idle") == "stopped"):
        return None
    now = time.time() if now is None else now
    version = getattr(session, "_chunked_source_identity", None)
    records = getattr(session, "retry_state", [])
    old = next((row for row in records if row["label"] == label
                and row["source_version"] == version), None)
    row = {"label": label, "attempt": attempt, "next_attempt_at": now + delay,
           "source_version": version}
    if old is not None:
        row["attempt"] = max(attempt, old["attempt"])
        row["next_attempt_at"] = max(row["next_attempt_at"], old["next_attempt_at"])
        if getattr(session, "is_paused", False) and "remaining_seconds" in old:
            row["next_attempt_at"] = max(row["next_attempt_at"], now + old["remaining_seconds"])
    if getattr(session, "is_paused", False):
        row["remaining_seconds"] = max(0., row["next_attempt_at"] - now)
    updated = [item for item in records if item["label"] != label] + [row]
    validate_retry_state(getattr(session, "run_intent", "idle"), updated)
    session.retry_state = updated
    session._persist_if_enabled()
    return row


def clear_pending_retry(session, label):
    """Retire only the admitted/completed interval's owned cooldown."""
    records = getattr(session, "retry_state", [])
    updated = [row for row in records if row["label"] != label]
    if updated != records:
        session.retry_state = updated
        session._persist_if_enabled()


def validate_retry_state(intent, records):
    if intent not in {"idle", "running", "paused", "stopped"}:
        raise ValueError("Ý định tiếp tục dự án không hợp lệ.")
    if not isinstance(records, list) or len(records) > 1000:
        raise ValueError("Tiến độ thử lại không hợp lệ.")
    labels = set()
    for row in records:
        if (not isinstance(row, dict)
                or set(row) - {"remaining_seconds"} != {"label", "attempt", "next_attempt_at", "source_version"}
                or not isinstance(row["label"], str) or not 0 < len(row["label"]) <= 500
                or row["label"] in labels
                or type(row["attempt"]) is not int or row["attempt"] < 1
                or type(row["next_attempt_at"]) not in (int, float)
                or not math.isfinite(row["next_attempt_at"]) or row["next_attempt_at"] < 0
                or (row["source_version"] is not None and not isinstance(row["source_version"], str))):
            raise ValueError("Checkpoint thử lại không hợp lệ.")
        if "remaining_seconds" in row and (type(row["remaining_seconds"]) not in (int, float)
                or not math.isfinite(row["remaining_seconds"]) or row["remaining_seconds"] < 0):
            raise ValueError("Thời gian chờ thử lại không hợp lệ.")
        labels.add(row["label"])


def sync_retry_deadlines(session, *, now=None, resuming=False):
    """Snapshot active remaining delays before Pause/Stop/Shutdown checkpoints."""
    now = time.time() if now is None else now
    from core.streaming.native_audit_lifecycle import sync_deadlines
    sync_deadlines(session, now=now, resuming=resuming)
    waits = {value["label"]: value for value in getattr(session, "_automatic_recovery_waits", {}).values()}
    for row in getattr(session, "retry_state", []):
        active = waits.get(row["label"])
        if active is not None:
            remaining = max(0., active["remaining"])
        elif resuming and "remaining_seconds" in row:
            remaining = row["remaining_seconds"]
        else:
            remaining = max(0., row["next_attempt_at"] - now)
        row.update(remaining_seconds=remaining, next_attempt_at=now + remaining)


def _auto_output_media_ready(session):
    if (getattr(session, "run_intent", "idle") != "running"
            or getattr(session, "auto_export_result", False) is not True
            or any(getattr(session, field, False) for field in (
                "is_stopped", "is_paused", "is_running", "is_editing", "error", "_stop_draining",
                "_restored_source_missing", "_persistence_capacity_failed", "_source_failure_code",
                "output_filename", "output_video_url"))
            or getattr(session, "translation_mode", "full") != "full"
            or not getattr(session, "segments", None)
            or (getattr(session, "automatic_output", {}) or {}).get("status") in {
                "skipped", "blocked", "failed", "completed", "cancelled"}):
        return False
    if any(owner is not None and not owner.done() for owner in (
            getattr(session, "review_task", None), getattr(session, "export_task", None),
            getattr(session, "auto_export_task", None))):
        return False
    if (getattr(session, "_chunked_source_started", False)
            or getattr(session, "_visual_incremental_started", False)) and not getattr(
                session, "_visual_prepass_complete", False):
        return False
    if (getattr(session, "_visual_incremental_started", False)
            and getattr(session, "_visual_completed_seconds", 0) + .001 < session.total_duration):
        return False
    from core.streaming.native_audit_lifecycle import has_pending
    if has_pending(session) or any(row.status not in {"READY", "PLAYED"} for row in session.segments.values()):
        return False
    source = getattr(session, "video_path", None)
    if source is None or not source.is_file():
        return False
    gate = session._final_output_metadata()
    return gate.get("media_complete") is True and gate.get("final_output_blocked") is False


def has_pending_auto_output(session):
    return ((getattr(session, "review_summary", {}) or {}).get("status") == "completed"
            and _auto_output_media_ready(session))


def prepare_auto_review(session):
    if ((getattr(session, "review_summary", {}) or {}).get("status") is None
            and settings.LLM_PROVIDER in {"opencode", "ollama"}
            and callable(getattr(session, "start_automatic_review", None))
            and _auto_output_media_ready(session)):
        session.automatic_output = {"status": "pending", "policy": "strict_media", "reason": "review_pending"}
        return True
    return False


def has_pending_auto_review(session):
    return (getattr(session, "automatic_output", None) == {
                "status": "pending", "policy": "strict_media", "reason": "review_pending"}
            and (getattr(session, "review_summary", {}) or {}).get("status") in {None, "incomplete"}
            and settings.LLM_PROVIDER in {"opencode", "ollama"}
            and callable(getattr(session, "start_automatic_review", None))
            and _auto_output_media_ready(session))


async def _resume_auto_output(session):
    try:
        if has_pending_auto_review(session):
            await session.start_automatic_review()
        elif has_pending_auto_output(session):
            await session.emit("finished", session.get_telemetry())
    except asyncio.CancelledError:
        if not getattr(session, "_application_shutdown", False) and session.run_intent == "running":
            session.run_intent = "stopped"
            session.is_stopped = True
        raise
    except Exception as error:
        logging.getLogger("errors").error("RESUME_OUTPUT_FAILED run_id=%s error_type=%s",
                                            session.task_id, type(error).__name__)
    finally:
        if session.start_task is asyncio.current_task():
            session.start_task = None
        if (session.run_intent == "running" and not session.is_stopped
                and not session.is_paused and not any(
                    owner is not None and not owner.done() for owner in (
                        getattr(session, "export_task", None), getattr(session, "auto_export_task", None),
                        getattr(session, "review_task", None)))):
            session.run_intent = "idle"
        session._persist_if_enabled()


async def resume_interrupted_sessions(event_callback_factory=None):
    """Return live resume owners; legacy, completed, paused and stopped are inert.

    Saved manifests are read through the same validated compressed/plain reader
    as History. Restoration rechecks actual source and WAVs before any request.
    """
    from core.streaming.session_store import _read, restore_saved_session
    from core.streaming.pipeline import active_streaming_sessions
    owners = []
    directory = settings.BASE_DIR / "workspace" / "projects"
    for path in sorted(directory.glob("*.json")):
        if path.stem in active_streaming_sessions:
            continue
        try:
            data = await asyncio.to_thread(_read, path.stem)
            if data["session"].get("run_intent") != "running":
                continue
            callback = event_callback_factory(path.stem) if event_callback_factory else None
            session = await asyncio.to_thread(restore_saved_session, path.stem, callback)
            if session.run_intent != "running":
                continue
            session.is_stopped = False
            from core.streaming.native_audit_lifecycle import has_pending, ensure_native_audit_retry
            if not session.can_retry:
                if has_pending(session):
                    owner = ensure_native_audit_retry(session)
                    if owner is not None:
                        owners.append(owner)
                    continue
                attempts = getattr(session, "_auto_export_attempts", None)
                if (not data["session"].get("output_filename")
                        and (attempts is None or attempts[1] < 2)
                        and (has_pending_auto_output(session) or has_pending_auto_review(session))):
                    if callback is not None:
                        owner = asyncio.create_task(_resume_auto_output(session),
                                                    name=f"resume-output-{session.task_id}")
                        session.start_task = owner
                        owners.append(owner)
                    continue
                if session.run_intent == "running" and not session.is_paused:
                    session.run_intent = "idle"
                    session._persist_if_enabled()
                continue

            async def resume(current=session):
                try:
                    # Wait all saved cooldowns before admitting a new request.
                    # Overwaiting one ready interval is preferable to issuing a
                    # pending interval before its provider cooldown has elapsed.
                    version = getattr(current, "_chunked_source_identity", None)
                    pending = [r for r in current.retry_state if r["source_version"] in {None, version}]
                    deadline = max((r["next_attempt_at"] for r in pending), default=0.)
                    current.is_running = True
                    while time.time() < deadline:
                        if current.is_stopped:
                            return
                        # Poll pause instead of waiting only on an Event: Stop
                        # must terminate even if a custom shutdown leaves it
                        # cleared, and pause must not consume the cooldown.
                        if current.is_paused or not current.pause_event.is_set():
                            paused_at = time.time()
                            await asyncio.sleep(.25)
                            shift = max(0., time.time() - paused_at)
                            deadline += shift
                            for row in pending:
                                row["next_attempt_at"] += shift
                            continue
                        await asyncio.sleep(min(.25, max(0., deadline - time.time())))
                    while not current.pause_event.is_set():
                        if current.is_stopped:
                            return
                        await asyncio.sleep(.25)
                    if current.is_stopped:
                        return
                    current.is_running = False
                    current.start_task = None
                    current.retry_state = []
                    await current.retry_failed_synthesis()
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    current.is_running = False
                    current.error = str(error)
                    current._persist_if_enabled()
                    logging.getLogger("errors").error("RESUME_FAILED run_id=%s error_type=%s",
                                                       current.task_id, type(error).__name__)
                finally:
                    # A dispatched retry installs its own worker/start owner.
                    # Only retire this owner when dispatch never happened.
                    if current.start_task is asyncio.current_task():
                        current.start_task = None
                        current.is_running = False

            owner = asyncio.create_task(resume(), name=f"resume-{session.task_id}")
            session.start_task = owner
            owners.append(owner)
        except (OSError, ValueError, TypeError) as error:
            logging.getLogger("errors").warning("RESUME_PROJECT_UNAVAILABLE run_id=%s error_type=%s",
                                                path.stem, type(error).__name__)
    return owners
