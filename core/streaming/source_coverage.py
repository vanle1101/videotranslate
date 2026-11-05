"""Durable source coverage, separate from how far the source scan has run.

An unresolved packet advances the scan cursor, but never proves that its
unrecognized portions contain no speech. Keep the entire packet unresolved;
``missing_speech_spans`` are diagnostics inside it, not permission to accept the
rest. These records describe original media seconds and do not identify,
renumber, or change transcript rows.

All helpers return detached values and leave the supplied ledger untouched.
Adjacent records may share a boundary; neither rounding nor a timing tolerance
is used to join a gap or replace a different packet.
"""
from collections.abc import Mapping
import math
from pathlib import PurePosixPath
import re


MAX_SOURCE_INTERVALS = 50_000
MAX_MISSING_SPEECH_SPANS = 50_000
VERIFIED_NON_SPEECH = "verified_non_speech"
SOURCE_INTERVAL_STATES = frozenset({"prepared", "unresolved", VERIFIED_NON_SPEECH})
SOURCE_VERIFICATION_CLASSES = frozenset({"SPEECH", "NON_SPEECH", "UNCERTAIN"})


class SourceCoverageError(ValueError):
    """A ledger or update cannot safely represent original source coverage."""


def validate_interval_verification(value, *, start, end):
    """Validate user-reviewed evidence attached to one exact source interval.

    Merely attaching a VAD diagnostic never changes coverage. A NON_SPEECH
    verdict is durable only with an explicit user reason and a contextual audio
    artifact whose recorded range covers the entire interval.
    """
    if not isinstance(value, Mapping) or set(value) != {
            "classification", "reason", "verified_by", "verified_at", "evidence"}:
        raise SourceCoverageError("source interval verification has an invalid shape")
    classification = value["classification"]
    if classification not in SOURCE_VERIFICATION_CLASSES:
        raise SourceCoverageError("source interval verification classification is invalid")
    reason = value["reason"]
    if (not isinstance(reason, str) or not reason.strip() or len(reason) > 2000
            or "\x00" in reason):
        raise SourceCoverageError("source interval verification requires a bounded reason")
    verified_by = value["verified_by"]
    if verified_by not in {"user", "assistant"}:
        raise SourceCoverageError("source interval verification authority is invalid")
    if value["classification"] == "NON_SPEECH" and verified_by != "user":
        raise SourceCoverageError("NON_SPEECH coverage requires an explicit user verdict")
    verified_at = value["verified_at"]
    if type(verified_at) not in (int, float) or not math.isfinite(verified_at) or verified_at <= 0:
        raise SourceCoverageError("source interval verification timestamp is invalid")
    evidence = value["evidence"]
    if not isinstance(evidence, (list, tuple)) or not 1 <= len(evidence) <= 8:
        raise SourceCoverageError("source interval verification requires bounded evidence")
    clean_evidence = []
    has_contextual_audio = False
    for artifact in evidence:
        if not isinstance(artifact, Mapping) or set(artifact) != {
                "kind", "path", "sha256", "start", "end"}:
            raise SourceCoverageError("source interval evidence artifact has an invalid shape")
        kind, path, digest = artifact["kind"], artifact["path"], artifact["sha256"]
        if kind not in {"audio_clip", "acoustic_metrics", "asr_candidate"}:
            raise SourceCoverageError("source interval evidence kind is invalid")
        if (not isinstance(path, str) or len(path) > 240 or "\\" in path
                or PurePosixPath(path).is_absolute()
                or PurePosixPath(path).parts[:1] != ("audit",)
                or any(part in {"", ".", ".."} for part in PurePosixPath(path).parts)):
            raise SourceCoverageError("source interval evidence must be a workspace audit artifact")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise SourceCoverageError("source interval evidence digest is invalid")
        evidence_start, evidence_end = artifact["start"], artifact["end"]
        if (type(evidence_start) not in (int, float) or type(evidence_end) not in (int, float)
                or not math.isfinite(evidence_start) or not math.isfinite(evidence_end)
                or evidence_start < 0 or evidence_start >= evidence_end):
            raise SourceCoverageError("source interval evidence timing is invalid")
        if kind == "audio_clip" and evidence_start <= start and evidence_end >= end:
            has_contextual_audio = True
        clean_evidence.append({"kind": kind, "path": path, "sha256": digest,
                               "start": float(evidence_start), "end": float(evidence_end)})
    if not has_contextual_audio:
        raise SourceCoverageError("source interval verdict requires contextual audio covering the interval")
    return {"classification": classification, "reason": reason.strip(), "verified_by": verified_by,
            "verified_at": float(verified_at), "evidence": clean_evidence}


def _seconds(value, label):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid:
        raise SourceCoverageError(f"{label} must be finite nonnegative seconds")
    return float(value)


def _range(start, end, *, duration, label):
    start, end = _seconds(start, f"{label}.start"), _seconds(end, f"{label}.end")
    if start >= end or end > duration:
        raise SourceCoverageError(f"{label} must satisfy 0 <= start < end <= total_duration")
    return start, end


def _record(value, *, duration):
    if not isinstance(value, Mapping):
        raise SourceCoverageError("source interval must be an object")
    start, end = _range(value.get("start"), value.get("end"),
                        duration=duration, label="source interval")
    state = value.get("state")
    if not isinstance(state, str) or state not in SOURCE_INTERVAL_STATES:
        raise SourceCoverageError("source interval state must be prepared or unresolved")
    error_code = value.get("error_code")
    if error_code is not None and (not isinstance(error_code, str) or not error_code.strip()):
        raise SourceCoverageError("error_code must be a nonempty string or null")
    raw_spans = value.get("missing_speech_spans", [])
    if not isinstance(raw_spans, (list, tuple)) or len(raw_spans) > MAX_MISSING_SPEECH_SPANS:
        raise SourceCoverageError("missing_speech_spans must be a bounded list")
    spans = []
    for span in raw_spans:
        if not isinstance(span, (list, tuple)) or len(span) != 2:
            raise SourceCoverageError("missing speech span must have two endpoints")
        left, right = _range(*span, duration=end, label="missing speech span")
        if left < start:
            raise SourceCoverageError("missing speech span must lie within its source interval")
        spans.append([left, right])
    spans.sort()
    if any(right[0] < left[1] for left, right in zip(spans, spans[1:])):
        raise SourceCoverageError("missing speech spans must not overlap")
    if state == "prepared" and (spans or error_code is not None):
        raise SourceCoverageError("prepared intervals cannot retain unresolved diagnostics")
    verification = value.get("verification")
    if verification is not None:
        verification = validate_interval_verification(verification, start=start, end=end)
    if state == VERIFIED_NON_SPEECH:
        if (spans or error_code is not None or verification is None
                or verification["classification"] != "NON_SPEECH"):
            raise SourceCoverageError("verified non-speech intervals require matching evidence and no missing span")
    elif verification is not None and verification["classification"] == "NON_SPEECH":
        raise SourceCoverageError("NON_SPEECH verification must resolve its exact source interval")
    result = {"start": start, "end": end, "state": state,
              "missing_speech_spans": spans, "error_code": error_code}
    if verification is not None:
        result["verification"] = verification
    return result


def validate_intervals(intervals, *, total_duration):
    """Validate and copy up to 50,000 disjoint records in chronological order.

    Empty ledgers and zero-duration unopened media are valid inputs. Positive
    source completion still requires a known, positive total duration.
    """
    duration = _seconds(total_duration, "total_duration")
    if not isinstance(intervals, (list, tuple)) or len(intervals) > MAX_SOURCE_INTERVALS:
        raise SourceCoverageError("source intervals must be a bounded list")
    records = [_record(value, duration=duration) for value in intervals]
    records.sort(key=lambda value: (value["start"], value["end"]))
    if any(right["start"] < left["end"] for left, right in zip(records, records[1:])):
        raise SourceCoverageError("source intervals must not overlap")
    if sum(len(value["missing_speech_spans"]) for value in records) > MAX_MISSING_SPEECH_SPANS:
        raise SourceCoverageError("source ledger has too many missing speech spans")
    return records


def record_interval(intervals, *, start, end, state, total_duration,
                    missing_speech_spans=(), error_code=None, verification=None):
    """Commit one packet, or replace exactly that packet after another attempt.

    An exact repeated commit is idempotent. Partial overlap, containment, or a
    near-matching timestamp raises instead of silently discarding other source
    evidence. Success should replace an unresolved record using its original
    start/end, clearing diagnostics through the default arguments.
    """
    records = validate_intervals(intervals, total_duration=total_duration)
    incoming = _record({"start": start, "end": end, "state": state,
                        "missing_speech_spans": missing_speech_spans,
                        "error_code": error_code, "verification": verification}, duration=total_duration)
    replaced = False
    for index, existing in enumerate(records):
        if incoming["start"] == existing["start"] and incoming["end"] == existing["end"]:
            records[index] = incoming
            replaced = True
            break
        if incoming["start"] < existing["end"] and existing["start"] < incoming["end"]:
            raise SourceCoverageError("a source update must not overlap a different packet")
    if not replaced:
        records.append(incoming)
    return validate_intervals(records, total_duration=total_duration)


def _with_legacy_prefix(intervals, *, total_duration, legacy_prefix):
    records = validate_intervals(intervals, total_duration=total_duration)
    prefix = _seconds(legacy_prefix, "legacy_prefix")
    if prefix > total_duration:
        raise SourceCoverageError("legacy_prefix must not exceed total_duration")
    # A legacy manifest knows only a contiguous prefix. Use it before the first
    # ledger record; explicit unresolved packets and later holes always win.
    prefix = min(prefix, records[0]["start"]) if records else prefix
    if prefix > 0:
        records.insert(0, {"start": 0., "end": prefix, "state": "prepared",
                           "missing_speech_spans": [], "error_code": None})
    return records


def verified_prefix(intervals, *, total_duration, legacy_prefix=0.):
    """End of the contiguous, verified source prefix starting at zero."""
    end = 0.
    for record in _with_legacy_prefix(intervals, total_duration=total_duration,
                                      legacy_prefix=legacy_prefix):
        if record["start"] != end or record["state"] not in {"prepared", VERIFIED_NON_SPEECH}:
            break
        end = record["end"]
    return end


def scanned_frontier(intervals, *, total_duration, legacy_prefix=0.):
    """End of contiguous attempted packets, including unresolved source."""
    end = 0.
    for record in _with_legacy_prefix(intervals, total_duration=total_duration,
                                      legacy_prefix=legacy_prefix):
        if record["start"] != end:
            break
        end = record["end"]
    return end


def verified_runs(intervals, *, total_duration, legacy_prefix=0.):
    """Return prepared ``(start, end)`` runs, merging exactly adjacent packets."""
    runs = []
    for record in _with_legacy_prefix(intervals, total_duration=total_duration,
                                      legacy_prefix=legacy_prefix):
        if record["state"] not in {"prepared", VERIFIED_NON_SPEECH}:
            continue
        start, end = record["start"], record["end"]
        if runs and runs[-1][1] == start:
            runs[-1] = (runs[-1][0], end)
        else:
            runs.append((start, end))
    return runs


def source_complete(intervals, *, total_duration, legacy_prefix=0.):
    """True only when the whole positive-duration source has been verified."""
    end = verified_prefix(intervals, total_duration=total_duration,
                          legacy_prefix=legacy_prefix)
    return bool(total_duration > 0 and end == total_duration)
