import hashlib
import json
import threading
from types import SimpleNamespace

import pytest

from core.streaming.source_coverage import (
    SourceCoverageError,
    replace_unresolved_interval,
    validate_source_gap_repair_history,
)
from core.streaming import source_repair


def _gap_diagnostic():
    return {
        "timing_origin": "input_audio",
        "sample_rate": 16000,
        "window": [.18, 6.252],
        "original_regions": [[.48, 2.56], [2.976, 3.104], [3.584, 5.952]],
        "gaps": [{"span": [2.976, 3.104]}],
        "omitted": {"original_regions": 0},
    }


def _candidate(*, includes_gap=True):
    words = [
        {"word": "说", "start": .60, "end": .90, "probability": .91},
        {"word": "不", "start": 2.99, "end": 3.08 if includes_gap else 2.95,
         "probability": .88},
        {"word": "好", "start": 4.10, "end": 4.45, "probability": .90},
    ]
    return [{"start": .50, "end": 5.90, "text_zh": "说不好", "words": words}]


def _session():
    calls, persist_threads = [], []

    class Recognizer:
        def transcribe_focused(self, path, *, language, timeout_seconds):
            calls.append((language, timeout_seconds))
            return _candidate(includes_gap=language == "zh")

    async def run_blocking(function, *args, **kwargs):
        import asyncio
        return await asyncio.to_thread(function, *args, **kwargs)

    return SimpleNamespace(
        task_id="repair-test", cache_dir=None, faster_whisper=Recognizer(),
        _source_gap_repair_history=[], _source_coverage_intervals=[],
        total_duration=300.048254, segments={},
        _persist_if_enabled=lambda: persist_threads.append(threading.get_ident()),
        _run_blocking=run_blocking,
    ), calls, persist_threads


@pytest.mark.parametrize("replacements", [
    [{"start": .2, "end": .4, "state": "prepared"}],
    [{"start": .1, "end": .3, "state": "prepared"},
     {"start": .31, "end": .5, "state": "unresolved", "missing_speech_spans": [[.31, .5]]}],
])
def test_source_repair_must_replace_exact_unresolved_interval(replacements):
    ledger = [
        {"start": 0., "end": .1, "state": "prepared"},
        {"start": .1, "end": .5, "state": "unresolved",
         "missing_speech_spans": [[.2, .3]], "error_code": "source_speech_unrecognized"},
        {"start": .5, "end": 1., "state": "prepared"},
    ]
    with pytest.raises(SourceCoverageError):
        replace_unresolved_interval(ledger, start=.1, end=.5,
            replacements=replacements, total_duration=1.)


def test_source_repair_replaces_exact_gap_without_touching_neighbors():
    ledger = [
        {"start": 0., "end": .1, "state": "prepared"},
        {"start": .1, "end": .5, "state": "unresolved",
         "missing_speech_spans": [[.2, .3]], "error_code": "source_speech_unrecognized"},
        {"start": .5, "end": 1., "state": "prepared"},
    ]
    updated = replace_unresolved_interval(ledger, start=.1, end=.5,
        replacements=[{"start": .1, "end": .5, "state": "prepared"}], total_duration=1.)
    assert [(row["start"], row["end"], row["state"]) for row in updated] == [
        (0., .1, "prepared"), (.1, .5, "prepared"), (.5, 1., "prepared")]
    assert ledger[1]["state"] == "unresolved"


def test_focused_recovery_requires_high_confidence_word_inside_measured_gap():
    packet = {"start": 0., "source_gap_diagnostics": [_gap_diagnostic()]}
    accepted = source_repair._candidate_rows(_candidate(), (0., 7.752), (.18, 6.252), "zh", .999)
    resolved, regions, gaps, reason = source_repair._validate_coverage(packet, (.18, 6.252), accepted)
    assert resolved
    assert len(regions) == 3
    assert gaps == [[2.976, 3.104]]
    assert "phủ toàn bộ" in reason

    unproven = source_repair._candidate_rows(_candidate(includes_gap=False),
        (0., 7.752), (.18, 6.252), "zh", .999)
    resolved, _, _, reason = source_repair._validate_coverage(packet, (.18, 6.252), unproven)
    assert not resolved
    assert "chưa tạo từ có độ tin cậy" in reason


@pytest.mark.parametrize("includes_gap,expected", [(True, "SPEECH"), (False, "UNCERTAIN")])
def test_checkpointed_repair_is_bounded_and_idempotent(tmp_path, monkeypatch, includes_gap, expected):
    workspace = tmp_path / "workspace"
    cache = workspace / "cache" / "repair-test" / "source_preparation"
    cache.mkdir(parents=True)
    monkeypatch.setattr(source_repair.settings, "WORKSPACE_DIR", workspace)
    session, calls, persist_threads = _session()
    session.cache_dir = cache.parent
    interval = {"start": .18, "end": 6.252, "state": "unresolved",
                "missing_speech_spans": [[.18, 6.252]], "error_code": "source_speech_unrecognized"}
    packet = {"start": 0., "end": 56., "source_intervals": [interval],
        "source_language": {"language": "zh", "probability": .999},
        "source_gap_diagnostics": [_gap_diagnostic()],
        "raw_audio": {"path": str(cache / "source.wav")}, "rows": [],
        "source_gap_repairs": []}
    clip_writes, commits, save_threads = [], [], []

    def find_packet(*_args):
        return packet

    def write_clip(_raw, target, _start, _end, output):
        output.write_bytes(b"contextual-original-audio")
        clip_writes.append(target)
        return 0., 7.752

    def save(_session, value):
        assert value is packet
        save_threads.append(threading.get_ident())

    def prepare_ledger(_session, value, target, rows):
        return {"ledger": [target], "new_segments": [], "prepared_seconds": target[1],
                "scanned_seconds": target[1]}

    def apply_ledger(owner, update):
        commits.append((update["ledger"], update["new_segments"]))
        owner._source_coverage_intervals = update["ledger"]
        owner._persist_if_enabled()

    class Recognizer:
        def transcribe_focused(self, *_args, **_kwargs):
            calls.append(("zh", 90.0))
            return _candidate(includes_gap=includes_gap)

    session.faster_whisper = Recognizer()
    monkeypatch.setattr(source_repair, "_find_packet", find_packet)
    monkeypatch.setattr(source_repair, "_write_context_clip", write_clip)
    monkeypatch.setattr("core.streaming.chunked_source._save", save)
    monkeypatch.setattr(source_repair, "_prepare_source_ledger_update", prepare_ledger)
    monkeypatch.setattr(source_repair, "_apply_source_ledger_update", apply_ledger)

    import asyncio
    first = asyncio.run(source_repair.repair_unresolved_source_interval(session, interval))
    assert first is (expected == "SPEECH")
    assert packet["source_gap_repairs"][0]["classification"] == expected
    assert len(clip_writes) == 1
    evidence = workspace / packet["source_gap_repairs"][0]["audio_path"]
    assert hashlib.sha256(evidence.read_bytes()).hexdigest() == packet["source_gap_repairs"][0]["audio_sha256"]
    assert validate_source_gap_repair_history(session._source_gap_repair_history,
        total_duration=300.048254)
    from core.streaming.session_store import _clean_source_gap_repair_history
    assert _clean_source_gap_repair_history(session._source_gap_repair_history,
        300.048254) == session._source_gap_repair_history
    assert len(commits) == (1 if expected == "SPEECH" else 0)
    if expected == "SPEECH":
        assert save_threads and all(thread != threading.get_ident() for thread in save_threads)
        assert persist_threads and all(thread == threading.get_ident() for thread in persist_threads)

    # A second Continue reuses a successful repair, or retains the uncertain
    # classification. Neither path starts the same ASR request again.
    second = asyncio.run(source_repair.repair_unresolved_source_interval(session, interval))
    assert second is first
    assert len(calls) == 1
    assert len(clip_writes) == 1


def test_legacy_gap_summary_recovers_only_matching_packet_evidence(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    task_id = "repair-test"
    packet_dir = workspace / "cache" / task_id / "source_preparation"
    packet_dir.mkdir(parents=True)
    monkeypatch.setattr(source_repair.settings, "WORKSPACE_DIR", workspace)
    clip = packet_dir / "repair_context.wav"
    clip.write_bytes(b"original contextual audio evidence")
    session = SimpleNamespace(task_id=task_id)
    proof = source_repair._repair_proof(session, (.18, 6.252), (0., 7.752), clip,
        [], [[.48, 2.56]], resolved=False, reason="Transcript does not cover measured VAD.")
    summary_keys = {"start", "end", "status", "attempts", "reason", "updated_at"}
    summary = {key: proof[key] for key in summary_keys}
    packet_path = packet_dir / "000000000000.json"
    packet_path.write_text(json.dumps({"source_gap_repairs": [proof]}), encoding="utf-8")
    manifest_before = packet_path.read_bytes()

    from core.streaming.session_store import _clean_source_gap_repair_history
    restored = _clean_source_gap_repair_history([summary], 300.048254, task_id)
    assert restored[0]["classification"] == "UNCERTAIN"
    assert restored[0]["audio_sha256"] == proof["audio_sha256"]
    assert restored[0]["candidate_rows"] == []
    assert packet_path.read_bytes() == manifest_before

    with pytest.raises(ValueError, match="does not exactly match"):
        _clean_source_gap_repair_history([{**summary, "reason": "different result"}],
            300.048254, task_id)

    clip.write_bytes(b"audio evidence changed")
    with pytest.raises(ValueError, match="evidence changed"):
        _clean_source_gap_repair_history([summary], 300.048254, task_id)
