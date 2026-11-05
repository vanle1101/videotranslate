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


def replace_unresolved_interval(intervals, *, start, end, replacements, total_duration):
    """Replace one exact unresolved packet with a measured, gap-free partition.

    Repairs may prove some speech windows while leaving a smaller window
    unresolved. Requiring an exact tiling prevents a repair from erasing source
    time, overlapping a neighboring packet, or treating an unmeasured tail as
    silence.
    """
    records = validate_intervals(intervals, total_duration=total_duration)
    target = next((row for row in records
                   if row["start"] == start and row["end"] == end), None)
    if target is None or target["state"] != "unresolved":
        raise SourceCoverageError("source repair must replace one exact unresolved interval")
    parts = validate_intervals(replacements, total_duration=total_duration)
    if (not parts or parts[0]["start"] != start or parts[-1]["end"] != end
            or any(left["end"] != right["start"] for left, right in zip(parts, parts[1:]))):
        raise SourceCoverageError("source repair parts must exactly tile the unresolved interval")
    return validate_intervals([
        *(row for row in records if row is not target), *parts
    ], total_duration=total_duration)


def validate_source_gap_repair_history(value, *, total_duration):
    """Validate bounded ASR repair attempts kept alongside a project checkpoint."""
    if not isinstance(value, (list, tuple)) or len(value) > 256:
        raise SourceCoverageError("source gap repair history must be a bounded list")
    result = []
    for row in value:
        if not isinstance(row, Mapping) or set(row) != {
                "start", "end", "classification", "status", "attempts", "reason",
                "context_start", "context_end", "audio_path", "audio_sha256",
                "candidate_rows", "vad_regions", "updated_at"}:
            raise SourceCoverageError("source gap repair record has an invalid shape")
        start, end = _range(row["start"], row["end"], duration=total_duration,
                            label="source gap repair")
        classification, status = row["classification"], row["status"]
        if ((classification, status) not in {("SPEECH", "resolved"), ("UNCERTAIN", "uncertain")}
                or type(row["attempts"]) is not int or not 1 <= row["attempts"] <= 2):
            raise SourceCoverageError("source gap repair classification is invalid")
        reason = row["reason"]
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000 or "\x00" in reason:
            raise SourceCoverageError("source gap repair reason is invalid")
        context_start, context_end = row["context_start"], row["context_end"]
        if (type(context_start) not in (int, float) or type(context_end) not in (int, float)
                or not math.isfinite(context_start) or not math.isfinite(context_end)
                or context_start < 0 or context_start > start or context_end < end
                or context_end > total_duration):
            raise SourceCoverageError("source gap repair context timing is invalid")
        path, digest = row["audio_path"], row["audio_sha256"]
        if (not isinstance(path, str) or len(path) > 240 or "\\" in path
                or PurePosixPath(path).is_absolute()
                or PurePosixPath(path).parts[:1] != ("cache",)
                or any(part in {"", ".", ".."} for part in PurePosixPath(path).parts)
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise SourceCoverageError("source gap repair audio evidence path or digest is invalid")
        updated = row["updated_at"]
        if type(updated) not in (int, float) or not math.isfinite(updated) or updated <= 0:
            raise SourceCoverageError("source gap repair timestamp is invalid")
        candidates = row["candidate_rows"]
        if not isinstance(candidates, (list, tuple)) or len(candidates) > 64:
            raise SourceCoverageError("source gap repair candidates exceed limit")
        clean_candidates = []
        for candidate in candidates:
            if not isinstance(candidate, Mapping) or set(candidate) != {"start", "end", "text_zh", "words"}:
                raise SourceCoverageError("source gap repair candidate has an invalid shape")
            left, right = candidate["start"], candidate["end"]
            text = candidate["text_zh"]
            words = candidate["words"]
            if (type(left) not in (int, float) or type(right) not in (int, float)
                    or not math.isfinite(left) or not math.isfinite(right)
                    or not start - .001 <= left < right <= end + .001
                    or not isinstance(text, str) or not text.strip() or len(text) > 2000
                    or not isinstance(words, (list, tuple)) or len(words) > 512):
                raise SourceCoverageError("source gap repair candidate is invalid")
            clean_words = []
            for word in words:
                if not isinstance(word, Mapping) or set(word) != {"word", "start", "end", "probability"}:
                    raise SourceCoverageError("source gap repair word has an invalid shape")
                token, word_start, word_end, probability = (
                    word["word"], word["start"], word["end"], word["probability"])
                if (not isinstance(token, str) or len(token) > 200
                        or type(word_start) not in (int, float) or type(word_end) not in (int, float)
                        or not math.isfinite(word_start) or not math.isfinite(word_end)
                        or not left - .001 <= word_start <= word_end <= right + .001
                        or probability is not None and (type(probability) not in (int, float)
                            or not math.isfinite(probability) or not 0 <= probability <= 1)):
                    raise SourceCoverageError("source gap repair word timing is invalid")
                clean_words.append({"word": token, "start": float(word_start), "end": float(word_end),
                                    "probability": probability})
            clean_candidates.append({"start": float(left), "end": float(right),
                                     "text_zh": text.strip(), "words": clean_words})
        regions = row["vad_regions"]
        if not isinstance(regions, (list, tuple)) or len(regions) > 256:
            raise SourceCoverageError("source gap repair VAD evidence exceeds limit")
        clean_regions = []
        for region in regions:
            if (not isinstance(region, (list, tuple)) or len(region) != 2
                    or type(region[0]) not in (int, float) or type(region[1]) not in (int, float)
                    or not math.isfinite(region[0]) or not math.isfinite(region[1])
                    or not start <= region[0] < region[1] <= end):
                raise SourceCoverageError("source gap repair VAD timing is invalid")
            clean_regions.append([float(region[0]), float(region[1])])
        result.append({"start": start, "end": end, "classification": classification,
            "status": status, "attempts": row["attempts"], "reason": reason.strip(),
            "context_start": float(context_start), "context_end": float(context_end),
            "audio_path": path, "audio_sha256": digest, "candidate_rows": clean_candidates,
            "vad_regions": clean_regions, "updated_at": float(updated)})
    result.sort(key=lambda item: (item["start"], item["end"]))
    if len({(item["start"], item["end"]) for item in result}) != len(result):
        raise SourceCoverageError("source gap repair intervals must be unique")
    return result


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
