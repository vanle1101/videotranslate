"""Review transport recovery without promoting semantic uncertainty to success.

Failure descriptors originate only from typed local exceptions. They are runtime
results, not provider-authored JSON. The normal retry ledger persists categories,
scope and hashes; no exception text or raw responses enter checkpoints.
"""
from copy import deepcopy
import hashlib
import json
import math

from core.streaming.retry_schedule import transient_failure, schedule_retry, finish_retry


def failure_descriptor(error, rows):
    code = transient_failure(error)
    if code is None or not code.startswith("provider_"):
        return None
    ids = sorted({row["id"] for row in rows if type(row.get("id")) is int})
    if not ids:
        return None
    result = {"segment_ids": ids, "code": code}
    delay = getattr(error, "retry_after", None)
    if type(delay) in (int, float) and math.isfinite(delay) and delay >= 0:
        result["retry_after"] = delay
    return result


def retain_failure(result, error, rows):
    descriptor = failure_descriptor(error, rows)
    if descriptor is not None:
        result.setdefault("retry_failures", []).append(descriptor)
    return descriptor


def annotate_failure(error, rows):
    descriptor = failure_descriptor(error, rows)
    if descriptor is not None:
        error.review_retry_failure = descriptor


def review_input_hashes(session):
    """Capture before review. Include full source context, config and glossary.

    Vietnamese drafts of OTHER rows are deliberately excluded: source context
    supplies relationships, whereas each target's own revision/draft is relevant.
    """
    from config import settings
    from core.streaming.glossary import glossary_snapshot
    rows = sorted(session.segments.values(), key=lambda row: (row.start, row.id))
    context = [{key: getattr(row, key, None) for key in (
        "id", "start", "end", "asr_text", "text_zh", "speaker_id", "speaker_confirmation",
        "source_language", "utterance_id")} for row in rows]
    common = {"context": context, "screens": session.screen_texts,
              "source": getattr(session, "_source_file_signature", None),
              "language": session.source_language, "glossary": glossary_snapshot(session),
              "provider": settings.LLM_PROVIDER, "model": settings.OPENCODE_MODEL, "version": 1}
    common_hash = hashlib.sha256(json.dumps(common, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    result = {}
    for row in rows:
        identity = {"common": common_hash, "target": {key: getattr(row, key, None) for key in
            ("id", "revision", "literal_vi", "natural_vi", "final_vi", "confirmed_silence")}}
        result[row.id] = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    return result


class _TransportFailure(RuntimeError):
    def __init__(self, descriptor):
        self.code = descriptor["code"]
        self.retry_after = descriptor.get("retry_after")


def schedule_review_failures(session, descriptors, expected_hashes, *, now=None):
    """Schedule only unchanged target rows. Caller persists immediately afterward.

    Do this before applying review output. Never call on an untrusted provider
    payload; descriptors come from retain_failure/annotate_failure above.
    """
    current = review_input_hashes(session)
    scheduled = []
    visited = set()
    for descriptor in descriptors:
        for sid in descriptor["segment_ids"]:
            if sid in visited or sid not in expected_hashes or current.get(sid) != expected_hashes[sid]:
                continue
            visited.add(sid)
            row = session.segments.get(sid)
            if row is None or (row.verification or {}).get("status") == "manual":
                continue
            record = schedule_retry(session, "review", str(sid), _TransportFailure(descriptor),
                                    expected_hashes[sid], now=now)
            if record:
                scheduled.append(record)
    return scheduled


def prune_review_retries(session):
    """Drop deleted/edited/manual targets; never approve or alter their content."""
    current = review_input_hashes(session)
    for record in list(getattr(session, "_retry_schedule", [])):
        if record["stage"] != "review":
            continue
        sid = int(record["scope_id"])
        row = session.segments.get(sid)
        if (row is None or current.get(sid) != record["input_hash"]
                or (row.verification or {}).get("status") == "manual"):
            finish_retry(session, "review", record["scope_id"], record["input_hash"])
    return deepcopy([row for row in getattr(session, "_retry_schedule", []) if row["stage"] == "review"])
