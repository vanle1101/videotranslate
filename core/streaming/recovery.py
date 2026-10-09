"""Durable interval ownership; attempted coverage is never completed coverage.

The project manifest is the atomic transaction boundary. No worker objects or
provider-authored metadata are serialized in this ledger.
"""
from __future__ import annotations

import hashlib
import json
import math
import time

STATES = frozenset({"PENDING", "RUNNING", "COMPLETED", "RETRY_PENDING", "REVIEW_REQUIRED", "FAILED"})
VERSION = 1


def interval_id(start, end):
    return f"{round(start * 1000):012d}-{round(end * 1000):012d}"


def input_hash(rows, configuration):
    payload = [{"id": row.id, "start": row.start, "end": row.end,
                "source": row.asr_text or row.text_zh} for row in rows]
    return hashlib.sha256(json.dumps([VERSION, payload, configuration], ensure_ascii=False,
                                    sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()


def validate_records(records, duration):
    if not isinstance(records, list) or len(records) > 50000:
        raise ValueError("Checkpoint đoạn xử lý không hợp lệ.")
    previous, seen = 0.0, set()
    for record in records:
        if not isinstance(record, dict) or set(record) != {
                "id", "start", "end", "state", "attempts", "input_hash", "error_code", "updated_at", "version"}:
            raise ValueError("Checkpoint đoạn xử lý thiếu metadata.")
        start, end = record["start"], record["end"]
        if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end, record["updated_at"]))
                or not previous <= start < end <= duration + .05
                or record["id"] != interval_id(start, end) or record["id"] in seen
                or record["state"] not in STATES or record["version"] != VERSION
                or type(record["attempts"]) is not int or record["attempts"] < 0
                or not isinstance(record["input_hash"], str) or len(record["input_hash"]) != 64
                or not isinstance(record["error_code"], str) or len(record["error_code"]) > 80):
            raise ValueError("Checkpoint đoạn xử lý sai trạng thái hoặc mốc thời gian.")
        previous = end
        seen.add(record["id"])
    return records


def reserve(records, start, end, signature):
    key = interval_id(start, end)
    existing = next((row for row in records if row["id"] == key), None)
    if existing:
        if existing["input_hash"] != signature:
            existing.update(state="PENDING", input_hash=signature, attempts=0, error_code="")
        return existing
    row = dict(id=key, start=start, end=end, state="PENDING", attempts=0,
               input_hash=signature, error_code="", updated_at=time.time(), version=VERSION)
    records.append(row)
    records.sort(key=lambda item: item["start"])
    return row


def transition(record, state, error_code=""):
    if state not in STATES:
        raise ValueError("Unknown interval state")
    if state == "RUNNING":
        record["attempts"] += 1
    record.update(state=state, error_code=error_code[:80], updated_at=time.time())


def contiguous_coverage(records, legacy_prefix=0.0):
    cursor = legacy_prefix
    for record in records:
        if record["end"] <= cursor + .001:
            continue
        if record["start"] > cursor + .001 or record["state"] != "COMPLETED":
            break
        cursor = record["end"]
    return cursor


def recover_interrupted(records):
    for record in records:
        if record["state"] == "RUNNING":
            transition(record, "RETRY_PENDING", "interrupted")


def segment_state(row):
    """Content uncertainty is separate from validated media readiness."""
    if row.status in {"READY", "PLAYED"}:
        return "REVIEW_REQUIRED" if row.needs_review else "COMPLETED"
    if row.status == "FAILED":
        if row.failed_stage == "TRANSLATING":
            return "RETRY_PENDING"
        return "FAILED"
    if row.status == "NEEDS_REVIEW":
        return "REVIEW_REQUIRED"
    return "PENDING" if row.status == "WAITING" else "RUNNING"
