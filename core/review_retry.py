"""Review transport recovery without promoting semantic uncertainty to success.

Failure descriptors originate only from typed local exceptions. They are runtime
results, not provider-authored JSON. The normal retry ledger persists categories,
scope and hashes; no exception text or raw responses enter checkpoints.
"""
from copy import deepcopy
import hashlib
import json
import logging
import math

from core.streaming.retry_schedule import transient_failure, schedule_retry, finish_retry


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


class ReviewInputHashes(dict):
    """Request-local ownership, not a persisted or provider-authored verdict."""
    def __init__(self, *args, target_owners=None, configuration=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.target_owners = target_owners or {}
        self.configuration = configuration


def _incomplete(row):
    # A newly translated row has no verdict yet. A first-pass transport
    # timeout still needs recovery if another chunk published context while
    # it was running. This grants no semantic approval or result ownership.
    return (row.verification or {}).get("status") in {None, "incomplete", "pending"}


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
    result = ReviewInputHashes(configuration=_digest({key: common[key] for key in
        ("source", "language", "glossary", "provider", "model", "version")}))
    for row in rows:
        identity = {"common": common_hash, "target": {key: getattr(row, key, None) for key in
            ("id", "revision", "literal_vi", "natural_vi", "final_vi", "confirmed_silence")}}
        result[row.id] = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
        result.target_owners[row.id] = _digest({key: getattr(row, key, None) for key in (
            "id", "start", "end", "revision", "text_zh", "asr_text", "final_vi", "literal_vi",
            "natural_vi", "confirmed_silence", "speaker_id", "speaker_confirmation")})
    return result


class _TransportFailure(RuntimeError):
    def __init__(self, descriptor):
        self.code = descriptor["code"]
        self.retry_after = descriptor.get("retry_after")


def schedule_review_failures(session, descriptors, expected_hashes, *, now=None):
    """Schedule owned failures; changed context starts a fresh incomplete review.

    Do this before applying review output. Never call on an untrusted provider
    payload; descriptors come from retain_failure/annotate_failure above.
    """
    current = review_input_hashes(session)
    scheduled = []
    visited = set()
    for descriptor in descriptors:
        for sid in descriptor["segment_ids"]:
            if sid in visited or sid not in expected_hashes:
                continue
            visited.add(sid)
            row = session.segments.get(sid)
            if row is None or (row.verification or {}).get("status") == "manual":
                continue
            if current.get(sid) != expected_hashes[sid]:
                # Parallel source/screen publication can change the context
                # while a request times out. Schedule a NEW review only when
                # the incomplete target and configuration are still owned;
                # this never accepts or publishes the old response.
                if (not _incomplete(row) or not isinstance(expected_hashes, ReviewInputHashes)
                        or current.configuration != expected_hashes.configuration
                        or current.target_owners.get(sid) != expected_hashes.target_owners.get(sid)):
                    continue
            record = schedule_retry(session, "review", str(sid), _TransportFailure(descriptor),
                                    current[sid], now=now)
            if record:
                scheduled.append(record)
    return scheduled


def prune_review_retries(session):
    """Retain fresh incomplete work; never transfer RUNNING result ownership."""
    current = review_input_hashes(session)
    for record in list(getattr(session, "_retry_schedule", [])):
        if record["stage"] != "review":
            continue
        sid = int(record["scope_id"])
        row = session.segments.get(sid)
        if row is None or (row.verification or {}).get("status") == "manual":
            finish_retry(session, "review", record["scope_id"], record["input_hash"])
        elif current.get(sid) != record["input_hash"]:
            if record["state"] == "RETRY_PENDING" and _incomplete(row):
                # A pending request has not been dispatched. Rebase its input
                # identity to currently incomplete content, preserving its
                # durable cooldown/round count. RUNNING results never gain
                # ownership of changed inputs through this path.
                for owned in session._retry_schedule:
                    if (owned["stage"], owned["scope_id"], owned["input_hash"]) == (
                            "review", record["scope_id"], record["input_hash"]):
                        owned["input_hash"] = current[sid]
                        logging.getLogger("pipeline").info(
                            "REVIEW_RETRY_REBASED run_id=%s segment_id=%s state=RETRY_PENDING",
                            getattr(session, "task_id", "unscoped"), sid)
                        break
            else:
                finish_retry(session, "review", record["scope_id"], record["input_hash"])
    return deepcopy([row for row in getattr(session, "_retry_schedule", []) if row["stage"] == "review"])
