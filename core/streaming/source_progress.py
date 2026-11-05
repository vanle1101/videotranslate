"""Source islands shared by preparation, scheduling and narration.

Scanning a packet does not certify it: explicit failed VAD windows remain
occupied holes, including when successful source exists on both sides.
"""
from core.streaming.source_coverage import (
    validate_intervals, verified_runs, verified_prefix, scanned_frontier,
    record_interval, validate_interval_verification, VERIFIED_NON_SPEECH,
)
import hashlib
from pathlib import Path
import time


def packet_intervals(start, end, unresolved=()):
    records, cursor = [], start
    for left, right in unresolved:
        if not start <= left < right <= end or left < cursor:
            raise ValueError("Invalid unresolved source window")
        if cursor < left:
            records.append(dict(start=cursor, end=left, state="prepared"))
        records.append(dict(start=left, end=right, state="unresolved",
                            missing_speech_spans=[[left, right]],
                            error_code="source_speech_unrecognized"))
        cursor = right
    if cursor < end:
        records.append(dict(start=cursor, end=end, state="prepared"))
    return validate_intervals(records, total_duration=end)


def commit_packet(session, record):
    """Append a measured packet without changing prior source or row IDs."""
    prior = list(getattr(session, "_source_coverage_intervals", []))
    legacy = session._source_prepared_seconds
    if not prior and legacy > 0:
        prior = packet_intervals(0., legacy)
    coverage = record.get("source_intervals") or packet_intervals(record["start"], record["end"])
    if (not coverage or coverage[0]["start"] != record["start"]
            or coverage[-1]["end"] != record["end"]
            or any(a["end"] != b["start"] for a, b in zip(coverage, coverage[1:]))):
        raise ValueError("Source checkpoint does not cover its attempted packet")
    ledger = validate_intervals([*prior, *coverage], total_duration=session.total_duration)
    session._source_coverage_intervals = ledger
    session._source_scanned_seconds = scanned_frontier(ledger, total_duration=session.total_duration)
    session._source_prepared_seconds = verified_prefix(ledger, total_duration=session.total_duration)
    # Legacy diagnoses can extend beyond the newly committed packet. Only
    # retire the portion actually remeasured by this packet.
    remaining = []
    for left, right in getattr(session, "_source_missing_speech_spans", []):
        if left < record["start"]:
            remaining.append([left, min(right, record["start"])])
        if right > record["end"]:
            remaining.append([max(left, record["end"]), right])
    from core.streaming.chunked_source import merge_source_missing_spans
    session._source_missing_speech_spans = merge_source_missing_spans([
        *remaining, *[[row["start"], row["end"]] for row in ledger if row["state"] == "unresolved"]],
        total_duration=session.total_duration)
    if session._source_missing_speech_spans:
        session._source_failure_code = "source_speech_unrecognized"
        session._source_failure_start = session._source_missing_speech_spans[0][0]
    else:
        session._source_failure_code = session._source_failure_start = None


def verify_source_interval(session, *, start, end, classification, reason, evidence,
                           verified_by="user", verified_at=None):
    """Record a bounded classification for one exact unresolved source interval.

    An assistant may record SPEECH/UNCERTAIN diagnostics, but only a
    user-reviewed NON_SPEECH verdict advances coverage without a transcript.
    SPEECH and UNCERTAIN remain unresolved until speech has been transcribed
    and its source packet is committed by the normal ASR path.
    Evidence artifacts are hashed from ``workspace/audit`` before mutation.
    Call ``session.persist()`` after this function returns successfully.
    """
    duration = session.total_duration
    ledger = list(getattr(session, "_source_coverage_intervals", []))
    current = next((row for row in ledger
                    if row["start"] == start and row["end"] == end), None)
    if current is None or current["state"] != "unresolved":
        raise ValueError("Manual source review must target one exact unresolved interval")
    if verified_at is None:
        verified_at = time.time()
    proof = validate_interval_verification({
        "classification": classification, "reason": reason, "verified_by": verified_by,
        "verified_at": verified_at, "evidence": evidence,
    }, start=start, end=end)
    workspace = (Path(__file__).resolve().parents[2] / "workspace").resolve()
    audit_root = (workspace / "audit").resolve()
    for artifact in proof["evidence"]:
        unresolved_path = workspace / Path(*Path(artifact["path"]).parts)
        if unresolved_path.is_symlink() or any(parent.is_symlink() for parent in unresolved_path.parents
                                               if parent != workspace.parent):
            raise ValueError("Source review evidence cannot be a symbolic link")
        path = unresolved_path.resolve()
        if not path.is_relative_to(audit_root) or not path.is_file():
            raise ValueError("Source review evidence must exist inside workspace/audit")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != artifact["sha256"]:
            raise ValueError("Source review evidence changed after it was inspected")

    non_speech = classification == "NON_SPEECH"
    updated = record_interval(ledger, start=start, end=end,
        state=VERIFIED_NON_SPEECH if non_speech else "unresolved",
        total_duration=duration,
        missing_speech_spans=() if non_speech else current["missing_speech_spans"],
        error_code=None if non_speech else current["error_code"],
        verification=proof)
    session._source_coverage_intervals = updated
    session._source_prepared_seconds = verified_prefix(updated, total_duration=duration)
    session._source_scanned_seconds = scanned_frontier(updated, total_duration=duration)
    from core.streaming.chunked_source import merge_source_missing_spans
    remaining = [span for row in updated if row["state"] == "unresolved"
                 for span in row["missing_speech_spans"]]
    if not non_speech:
        remaining.extend(span for span in getattr(session, "_source_missing_speech_spans", [])
                         if span != [start, end])
    session._source_missing_speech_spans = merge_source_missing_spans(
        remaining, total_duration=duration)
    if session._source_missing_speech_spans:
        session._source_failure_code = "source_speech_unrecognized"
        session._source_failure_start = session._source_missing_speech_spans[0][0]
    else:
        session._source_failure_code = session._source_failure_start = None
    return proof


def source_runs(session):
    if not getattr(session, "_chunked_source_started", False):
        return [(0., session.total_duration)] if session.total_duration > 0 else []
    return verified_runs(getattr(session, "_source_coverage_intervals", []),
                         total_duration=session.total_duration,
                         legacy_prefix=session._source_prepared_seconds)


def containing_run(session, start, end=None):
    end = start if end is None else end
    return next(((left, right) for left, right in source_runs(session)
                 if left <= start < right and end <= right), None)


def next_run(session, cursor):
    return next(((max(left, cursor), right) for left, right in source_runs(session)
                 if right > cursor), None)


def scan_frontier(session):
    return max(getattr(session, "_source_scanned_seconds", 0.), session._source_prepared_seconds)
