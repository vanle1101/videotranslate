"""Durable retry waves; the project manifest remains the atomic boundary.

Transport adapters own attempts within one wave. This ledger schedules only a
subsequent wave, never retries one API call. Callers persist after every mutation
and before sleeping or dispatching work; no timers or workers live in this module.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import math
import re
import time

VERSION = 1
MIN_COOLDOWN = 300.0
INTENTS = frozenset({"active", "retry_pending", "shutdown", "user_stopped", "paused", "completed", "halted"})
AUTO_INTENTS = frozenset({"active", "retry_pending", "shutdown"})
_TRANSIENT = frozenset({"provider_timeout", "provider_transport", "provider_rate_limited", "provider_server",
                        "provider_queue_full", "provider_queue_timeout", "provider_circuit_open",
                        "tts_timeout", "tts_transport", "tts_rate_limited", "tts_empty_audio", "tts_http_error"})
_TERMINAL = frozenset({"provider_authentication", "provider_model", "provider_rejected", "provider_configuration",
                       "provider_cancelled", "invalid_response", "invalid_content", "invalid_type", "missing_field",
                       "TIMING_CONFLICT"})
_TOKEN = re.compile(r"[A-Za-z0-9_.:-]{1,160}\Z")


def transient_failure(error):
    """Return a fixed category, never classify free-form error text as evidence.

    A wrapper may preserve a typed cause. Explicit rejection/cancellation wins
    over nested transport exceptions; unknown errors require investigation.
    """
    from core.engines.tts.edge_fallback import EdgeTTSRequestError
    chain, seen = [], set()
    while isinstance(error, BaseException) and id(error) not in seen and len(chain) < 12:
        seen.add(id(error))
        chain.append(error)
        error = error.__cause__
    for item in chain:
        code = getattr(item, "code", None)
        if isinstance(item, EdgeTTSRequestError):
            if (item.status in {401, 403} or code in {"tts_tls_certificate", "tts_tls_handshake", "tts_client_outdated"}
                    or not item.retryable
                    or item.status is not None and not (type(item.status) is int and
                        (item.status in {408, 429} or 500 <= item.status < 600))):
                return None
        if isinstance(item, asyncio.CancelledError) or code in _TERMINAL:
            return None
        name = type(item).__name__
        if name in {"OpenCodeCancelledError", "OpenCodeConfigurationError", "OpenCodeModelError",
                    "OpenCodeResponseValidationError", "AIExecutionCancelledError"}:
            return None
    for item in chain:
        code = getattr(item, "code", None)
        if isinstance(item, EdgeTTSRequestError):
            if code in {"tts_timeout", "tts_transport", "tts_rate_limited", "tts_empty_audio", "tts_http_error"}:
                return code
            return None
        if code in _TRANSIENT and not code.startswith("tts_"):
            return code
        if isinstance(item, TimeoutError) or type(item).__name__ == "OpenCodeTimeoutError":
            return "provider_timeout"
        if isinstance(item, ConnectionError):
            return "provider_transport"
        if type(item).__name__ in {"AIQueueFullError", "AIQueueTimeoutError", "AICircuitOpenError"}:
            return {"AIQueueFullError": "provider_queue_full", "AIQueueTimeoutError": "provider_queue_timeout",
                    "AICircuitOpenError": "provider_circuit_open"}[type(item).__name__]
    return None


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def validate_schedule(records):
    if not isinstance(records, list) or len(records) > 50000:
        raise ValueError("Lịch thử lại đã lưu không hợp lệ.")
    keys = set()
    required = {"version", "stage", "scope_id", "input_hash", "rounds", "next_attempt_at", "state", "error_code", "updated_at"}
    for row in records:
        if (not isinstance(row, dict) or set(row) != required or type(row["version"]) is not int
                or row["version"] != VERSION
                or any(not isinstance(row[k], str) or not _TOKEN.fullmatch(row[k]) for k in ("stage", "scope_id"))
                or not isinstance(row["input_hash"], str) or not re.fullmatch(r"[a-f0-9]{64}", row["input_hash"])
                or type(row["rounds"]) is not int or row["rounds"] < 1
                or row["state"] not in {"RETRY_PENDING", "RUNNING"}
                or row["error_code"] not in _TRANSIENT | {"interrupted"}
                or not _number(row["next_attempt_at"]) or not _number(row["updated_at"])
                or row["state"] == "RETRY_PENDING" and row["next_attempt_at"] < row["updated_at"] + MIN_COOLDOWN):
            raise ValueError("Checkpoint đợt thử lại sai cấu trúc hoặc thời gian chờ.")
        identity = (row["stage"], row["scope_id"])
        if identity in keys:
            raise ValueError("Checkpoint đợt thử lại bị trùng phạm vi.")
        keys.add(identity)
    return records


def restart_intent(fields, saved_state):
    intent = fields.get("_restart_intent")
    if intent is None:
        # Older STOPPED manifests cannot distinguish a click from shutdown.
        # Never infer consent to restart those records automatically.
        return {"RUNNING": "active", "STOPPED": "user_stopped", "READY": "completed"}.get(saved_state, "halted")
    if not isinstance(intent, str) or intent not in INTENTS:
        raise ValueError("Ý định khôi phục dự án không hợp lệ.")
    return intent


def mark_intent(session, intent):
    if intent not in INTENTS:
        raise ValueError("Unknown restart intent")
    session._restart_intent = intent


def can_auto_resume(session):
    return getattr(session, "_restart_intent", "halted") in AUTO_INTENTS


def schedule_retry(session, stage, scope_id, error, input_hash, *, now=None, cooldown=MIN_COOLDOWN):
    """After an exhausted transient wave, return new record or None if terminal.

    Same-stage/scope records replace atomically in memory. A changed source hash
    starts its own round count; successful siblings and their media are untouched.
    Explicit Pause/Stop never becomes active merely because a late error arrives.
    """
    code = transient_failure(error)
    if code is None:
        return None
    timestamp = time.time() if now is None else now
    if not _number(timestamp) or not _number(cooldown):
        raise ValueError("Invalid retry clock/cooldown")
    retry_after = getattr(error, "retry_after", None)
    delay = max(MIN_COOLDOWN, cooldown, retry_after if _number(retry_after) else 0)
    records = deepcopy(getattr(session, "_retry_schedule", []))
    validate_schedule(records)
    previous = next((row for row in records if row["stage"] == stage and row["scope_id"] == scope_id), None)
    record = dict(version=VERSION, stage=stage, scope_id=scope_id, input_hash=input_hash,
                  rounds=previous["rounds"] + 1 if previous and previous["input_hash"] == input_hash else 1,
                  next_attempt_at=timestamp + delay, state="RETRY_PENDING", error_code=code, updated_at=timestamp)
    if previous:
        records.remove(previous)
    records.append(record)
    validate_schedule(records)
    session._retry_schedule = records
    if getattr(session, "_restart_intent", "active") not in {"user_stopped", "paused", "completed"}:
        mark_intent(session, "retry_pending")
    return deepcopy(record)


def due_entries(session, *, now=None):
    if not can_auto_resume(session) or getattr(session, "is_stopped", False):
        return []
    timestamp = time.time() if now is None else now
    records = validate_schedule(getattr(session, "_retry_schedule", []))
    return [deepcopy(row) for row in records if row["state"] == "RETRY_PENDING" and row["next_attempt_at"] <= timestamp]


def begin_retry(session, stage, scope_id, input_hash, *, now=None):
    """Claim an eligible wave before dispatch. False means wait/stopped/stale."""
    timestamp = time.time() if now is None else now
    eligible = next((row for row in due_entries(session, now=timestamp)
                     if (row["stage"], row["scope_id"], row["input_hash"]) == (stage, scope_id, input_hash)), None)
    if eligible is None:
        return False
    records = deepcopy(session._retry_schedule)
    for row in records:
        if row["stage"] == stage and row["scope_id"] == scope_id:
            row.update(state="RUNNING", updated_at=timestamp)
    session._retry_schedule = records
    mark_intent(session, "active")
    return True


def finish_retry(session, stage, scope_id, input_hash):
    """Remove only the exact succeeded source revision, never a newer edit."""
    session._retry_schedule = [row for row in getattr(session, "_retry_schedule", [])
        if (row["stage"], row["scope_id"], row["input_hash"]) != (stage, scope_id, input_hash)]


def recover_schedule(records, *, now=None):
    """Interrupted requests become pending, with a fresh full quiet interval."""
    result = deepcopy(validate_schedule(records))
    timestamp = time.time() if now is None else now
    for row in result:
        if row["state"] == "RUNNING":
            row.update(state="RETRY_PENDING", error_code="interrupted", updated_at=timestamp,
                       next_attempt_at=timestamp + MIN_COOLDOWN)
    return validate_schedule(result)


async def wait_until_due(session, *, cancel_check=None, poll_interval=.25):
    """Cancellable wait only; return due work, never execute it or hide errors."""
    if not .01 <= poll_interval <= 1:
        raise ValueError("Polling interval must be between .01 and 1 second")
    while True:
        if (not can_auto_resume(session) or getattr(session, "is_stopped", False)
                or cancel_check and cancel_check()):
            raise asyncio.CancelledError
        due = due_entries(session)
        if due or not getattr(session, "_retry_schedule", []):
            return due
        await asyncio.sleep(poll_interval)
