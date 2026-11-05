"""Bounded, credential-free continuation metadata for native semantic audits.

The session owns waiting, persistence, cancellation and revision-safe publish.
This module never sleeps, schedules work or rewrites a translation.
"""
from copy import deepcopy
import hashlib
import json
import math
import time

COOLDOWN_SECONDS = 300
_SOURCE_FIELDS = ("id", "start", "end", "text_zh", "source_language")
_TEXT_FIELDS = ("literal_vi", "natural_vi", "final_vi")
_KEYS = {"version", "model", "identity", "draft", "context", "attempt", "next_attempt_at", "remaining_seconds", "code", "revision"}


def _identity(row):
    value = {k: row.get(k) for k in (*_SOURCE_FIELDS, *_TEXT_FIELDS)}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode("utf-8")).hexdigest()


def transient_audit_error(error):
    """Only transport faults can schedule continuation; schemas are terminal."""
    from core.engines.translation.opencode_client import OpenCodeRequestError
    return isinstance(error, (TimeoutError, ConnectionError)) or (
        isinstance(error, OpenCodeRequestError) and error.retryable is True)


def make_pending_audit(row, draft, context, *, version, model, error, previous=None, now=None):
    stamp = time.time() if now is None else now
    previous = previous or {}
    clean_draft = {k: deepcopy(draft[k]) for k in (*_SOURCE_FIELDS[:-1], *_TEXT_FIELDS,
                                                 "needs_review", "review_reason")}
    clean_draft["source_language"] = row["source_language"]
    for key in _TEXT_FIELDS:
        clean_draft[key] = clean_draft[key].strip()
    from core.translation_batching import neighbor_context
    references = neighbor_context(list({item["id"]: {k: deepcopy(item[k]) for k in _SOURCE_FIELDS}
        for item in context if all(k in item for k in _SOURCE_FIELDS)}.values()), [row], neighbors=3)
    delay = getattr(error, "retry_after", 0)
    if type(delay) not in (int, float) or not math.isfinite(delay):
        delay = 0
    return {"version": version, "model": model, "identity": _identity(clean_draft),
            "revision": row.get("revision", 0),
            "draft": clean_draft, "context": references,
            "attempt": previous.get("attempt", 0) + 1,
            "next_attempt_at": stamp + max(COOLDOWN_SECONDS, delay),
            "remaining_seconds": 0.,
            "code": getattr(error, "code", "provider_timeout" if isinstance(error, TimeoutError)
                            else "provider_transport_error")}


def validate_pending_audit(value):
    """Return a validated deep copy for the persistence cleaner, or None."""
    from core.multilingual_translation import VERSION, _source_rows, _validate
    from core.structured_response import StructuredResponseError
    try:
        if (not isinstance(value, dict) or set(value) != _KEYS or value["version"] != VERSION
                or type(value["version"]) is not int
                or not isinstance(value["model"], str) or len(value["model"]) > 200
                or not isinstance(value["identity"], str) or len(value["identity"]) != 64
                or type(value["attempt"]) is not int or value["attempt"] < 1
                or type(value["revision"]) is not int or value["revision"] < 0
                or type(value["next_attempt_at"]) not in (int, float)
                or not math.isfinite(value["next_attempt_at"]) or value["next_attempt_at"] < 0
                or type(value["remaining_seconds"]) not in (int, float)
                or not math.isfinite(value["remaining_seconds"]) or value["remaining_seconds"] < 0
                or value["code"] not in {"provider_timeout", "provider_transport_error", "provider_failed",
                    "provider_rate_limited", "provider_server", "provider_transport", "provider_queue_full",
                    "provider_queue_timeout", "provider_circuit_open"}
                or not isinstance(value["context"], list) or len(value["context"]) > 64
                or len(json.dumps(value, ensure_ascii=False, allow_nan=False)) > 180000):
            return None
        draft = value["draft"]
        source = _source_rows([draft], "mixed")
        _validate({"segments": [draft]}, source)
        if (any(not isinstance(row, dict) or set(row) != set(_SOURCE_FIELDS) for row in value["context"])
                or len({row["id"] for row in value["context"]}) != len(value["context"])):
            return None
        for offset in range(0, len(value["context"]), 32):
            _source_rows(value["context"][offset:offset+32], "mixed")
        if _identity(draft) != value["identity"]:
            return None
        return deepcopy(value)
    except (ValueError, TypeError, KeyError, AttributeError, StructuredResponseError):
        return None


def pending_native_audit(row):
    row = row if isinstance(row, dict) else row.to_dict()
    verification = row.get("verification") or {}
    if verification.get("status") == "manual":
        return None
    semantic = row.get("multilingual_review") or verification.get("multilingual_review") or {}
    pending = validate_pending_audit(semantic.get("pending_audit"))
    try:
        return pending if (pending and _identity(row) == pending["identity"]
                           and row.get("revision", 0) == pending["revision"]) else None
    except (ValueError, TypeError):
        return None


def audit_existing_native_rows(rows, *, context=(), cancel_check=None, client=None):
    """Audit a pending saved draft without changing its words or playable audio."""
    from core.streaming.native_recovery import review_native_audits
    return review_native_audits(rows, context=context, cancel_check=cancel_check, client=client)


def bind_published_audit(segment, reviewed):
    """Rebind only an explicitly accepted, identical draft at publication."""
    marker = pending_native_audit(reviewed)
    current = segment.to_dict()
    if (marker is None or (segment.verification or {}).get("status") == "manual"
            or _identity(current) != marker["identity"]):
        return
    semantic = (segment.verification or {}).get("multilingual_review")
    if isinstance(semantic, dict) and semantic.get("pending_audit") == marker:
        semantic["pending_audit"]["revision"] = segment.revision
