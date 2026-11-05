"""Human-reviewed source-gap evidence is durable and never implied by VAD."""
from copy import deepcopy
import hashlib
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.streaming import source_progress
from core.streaming.source_coverage import (
    SourceCoverageError, record_interval, source_complete,
)
from test_session_store import persisted


def artifact(path, kind, start, end):
    return {"kind": kind, "path": path, "sha256": "a" * 64,
            "start": start, "end": end}


def proof(classification="NON_SPEECH"):
    return {"classification": classification,
        "reason": "User listened to contextual original audio and confirmed the classification.",
        "verified_by": "user", "verified_at": time.time(),
        "evidence": [artifact("audit/review/context.wav", "audio_clip", 0.8, 1.5)]}


def test_verified_non_speech_requires_user_reason_and_contextual_audio():
    result = record_interval([], start=1., end=1.2, state="verified_non_speech",
        total_duration=2., verification=proof())
    assert result[0]["verification"]["classification"] == "NON_SPEECH"
    assert result[0]["state"] == "verified_non_speech"
    assert source_complete([{"start": 0., "end": 1., "state": "prepared"},
        *result, {"start": 1.2, "end": 2., "state": "prepared"}], total_duration=2.)


@pytest.mark.parametrize("verification", [None, proof("SPEECH"), proof("UNCERTAIN"),
    {**proof(), "reason": " "}, {**proof(), "verified_by": "codex"},
    {**proof(), "verified_by": "assistant"},
    {**proof(), "evidence": [artifact("outside.wav", "audio_clip", 0.8, 1.5)]},
    {**proof(), "evidence": [artifact("audit/review/too_short.wav", "audio_clip", 1.02, 1.18)]}])
def test_non_speech_state_rejects_missing_or_inconsistent_evidence(verification):
    with pytest.raises(SourceCoverageError):
        record_interval([], start=1., end=1.2, state="verified_non_speech",
                        total_duration=2., verification=verification)


@pytest.mark.parametrize("classification", ["SPEECH", "UNCERTAIN"])
def test_speech_or_uncertain_verdict_retains_unresolved_coverage(classification):
    ledger = [{"start": 0., "end": 1., "state": "prepared"},
        {"start": 1., "end": 1.2, "state": "unresolved",
         "missing_speech_spans": [[1., 1.2]], "error_code": "source_speech_unrecognized"},
        {"start": 1.2, "end": 2., "state": "prepared"}]
    reviewed = record_interval(ledger, start=1., end=1.2, state="unresolved",
        total_duration=2., missing_speech_spans=[[1., 1.2]],
        error_code="source_speech_unrecognized", verification=proof(classification))
    assert not source_complete(reviewed, total_duration=2.)
    assert reviewed[1]["missing_speech_spans"] == [[1., 1.2]]
    assert reviewed[1]["verification"]["classification"] == classification


def test_assistant_uncertain_classification_is_recordable_but_not_complete():
    value = proof("UNCERTAIN")
    value["verified_by"] = "assistant"
    reviewed = record_interval([], start=1., end=1.2, state="unresolved",
        total_duration=2., missing_speech_spans=[[1., 1.2]],
        error_code="source_speech_unrecognized", verification=value)
    assert reviewed[0]["verification"]["verified_by"] == "assistant"
    assert not source_complete(reviewed, total_duration=2.)


def test_checkpoint_roundtrip_keeps_interval_verdict_and_does_not_fabricate_mp4(persisted):
    from core.streaming import session_store as store

    workspace = Path(store.settings.WORKSPACE_DIR)
    evidence_path = workspace / "audit" / "manual-review" / "context.wav"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_bytes(b"reviewed audio")
    digest = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    session = persisted
    session.total_duration = 2.
    session._chunked_source_started = True
    session._source_coverage_intervals = [
        {"start": 0., "end": 1., "state": "prepared"},
        {"start": 1., "end": 1.2, "state": "verified_non_speech",
         "missing_speech_spans": [], "error_code": None,
         "verification": {"classification": "NON_SPEECH", "reason": "User confirmed no dialogue.",
             "verified_by": "user", "verified_at": time.time(), "evidence": [
                 {"kind": "audio_clip", "path": "audit/manual-review/context.wav",
                  "sha256": digest, "start": .8, "end": 1.5}]}},
        {"start": 1.2, "end": 2., "state": "prepared"},
    ]
    session._source_prepared_seconds = 2.
    session._source_scanned_seconds = 2.
    session._source_missing_speech_spans = []
    session._source_failure_code = session._source_failure_start = None
    session.persist()

    data = store._read(session.task_id)
    interval = data["session"]["_source_coverage_intervals"][1]
    assert interval["verification"]["reason"] == "User confirmed no dialogue."
    assert interval["verification"]["evidence"][0]["sha256"] == digest
    assert store._availability(data)["source_coverage_complete"]
    assert not store._availability(data)["output_filename"]


def test_interval_verifier_hashes_evidence_and_only_non_speech_advances_coverage(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    evidence_path = workspace / "audit" / "context.wav"
    evidence_path.parent.mkdir(parents=True)
    evidence_path.write_bytes(b"small contextual clip")
    digest = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    fake_source_file = tmp_path / "core" / "streaming" / "source_progress.py"
    monkeypatch.setattr(source_progress, "__file__", str(fake_source_file))
    session = SimpleNamespace(total_duration=2., _source_prepared_seconds=1.,
        _source_scanned_seconds=2., _source_coverage_intervals=[
            {"start": 0., "end": 1., "state": "prepared"},
            {"start": 1., "end": 1.2, "state": "unresolved",
             "missing_speech_spans": [[1., 1.2]], "error_code": "source_speech_unrecognized"},
            {"start": 1.2, "end": 2., "state": "prepared"}],
        _source_missing_speech_spans=[[1., 1.2]],
        _source_failure_code="source_speech_unrecognized", _source_failure_start=1.)
    evidence = [{**artifact("audit/context.wav", "audio_clip", .8, 1.5), "sha256": digest}]
    proof_result = source_progress.verify_source_interval(session, start=1., end=1.2,
        classification="UNCERTAIN", reason="The brief sound is not clear enough to classify.",
        evidence=evidence)
    assert proof_result["classification"] == "UNCERTAIN"
    assert not source_complete(session._source_coverage_intervals, total_duration=2.)
    assert session._source_missing_speech_spans == [[1., 1.2]]

    session_before_stale_check = deepcopy(session._source_coverage_intervals)
    evidence_path.write_bytes(b"changed after review")
    with pytest.raises(ValueError, match="changed"):
        source_progress.verify_source_interval(session, start=1., end=1.2,
            classification="NON_SPEECH", reason="Reject stale evidence.", evidence=evidence)
    assert session._source_coverage_intervals == session_before_stale_check
    evidence_path.write_bytes(b"small contextual clip")

    source_progress.verify_source_interval(session, start=1., end=1.2,
        classification="NON_SPEECH", reason="I listened and confirmed this contains no dialogue.",
        evidence=evidence)
    assert source_complete(session._source_coverage_intervals, total_duration=2.)
    assert session._source_missing_speech_spans == []
    assert session._source_failure_code is None

