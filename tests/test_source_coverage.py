"""A scan can pass uncertain source without turning it into verified silence."""
from copy import deepcopy
import json
import math

import pytest

from core.streaming.source_coverage import (
    MAX_SOURCE_INTERVALS, SourceCoverageError, record_interval, scanned_frontier,
    source_complete, validate_intervals, verified_prefix, verified_runs,
)


def packet(start, end, state="prepared", spans=(), error=None):
    return {"start": start, "end": end, "state": state,
            "missing_speech_spans": list(spans), "error_code": error}


def test_failed_packet_does_not_block_scanning_or_certify_its_other_seconds():
    ledger = [packet(0, 32), packet(32, 64, "unresolved", [[46.07, 46.89]], "source_gap"),
              packet(64, 96), packet(96, 128)]
    assert verified_prefix(ledger, total_duration=128) == 32
    assert scanned_frontier(ledger, total_duration=128) == 128
    assert verified_runs(ledger, total_duration=128) == [(0, 32), (64, 128)]
    assert not source_complete(ledger, total_duration=128)
    assert not source_complete(ledger, total_duration=128, legacy_prefix=128)


def test_resolving_exact_failed_packet_joins_runs_and_completes_without_changing_neighbors():
    ledger = [packet(0, 32), packet(32, 64, "unresolved", [[46.07, 46.89]], "source_gap"),
              packet(64, 100)]
    before = deepcopy(ledger)
    result = record_interval(ledger, start=32, end=64, state="prepared", total_duration=100)
    assert ledger == before
    assert result[0] == before[0] and result[2] == before[2]
    assert result[1]["missing_speech_spans"] == [] and result[1]["error_code"] is None
    assert verified_runs(result, total_duration=100) == [(0, 100)]
    assert verified_prefix(result, total_duration=100) == 100
    assert source_complete(result, total_duration=100)


def test_repeated_commit_is_idempotent_sorted_detached_and_json_roundtrippable():
    ledger = [packet(64, 96), packet(0, 32)]
    before = deepcopy(ledger)
    arguments = dict(start=32, end=64, state="unresolved", total_duration=96,
                     missing_speech_spans=[[46, 47]], error_code="source_gap")
    result = record_interval(ledger, **arguments)
    assert record_interval(result, **arguments) == result
    assert validate_intervals(json.loads(json.dumps(result)), total_duration=96) == result
    assert [record["start"] for record in result] == [0, 32, 64]
    result[1]["missing_speech_spans"][0][0] = 45
    result[0]["end"] = 31
    assert ledger == before and arguments["missing_speech_spans"] == [[46, 47]]


@pytest.mark.parametrize("start,end", [(1, 32), (0, 31), (0, 32.000001), (31, 64), (0, 96)])
def test_different_packet_cannot_replace_overlapping_evidence(start, end):
    ledger = [packet(0, 32), packet(64, 96)]
    before = deepcopy(ledger)
    with pytest.raises(SourceCoverageError, match="overlap"):
        record_interval(ledger, start=start, end=end, state="unresolved", total_duration=96)
    assert ledger == before


@pytest.mark.parametrize("gap", [1, 0.001, 0.000000001])
def test_tiny_missing_range_never_rounds_into_coverage(gap):
    ledger = [packet(0, 32), packet(32 + gap, 64)]
    assert verified_prefix(ledger, total_duration=64) == 32
    assert scanned_frontier(ledger, total_duration=64) == 32
    assert verified_runs(ledger, total_duration=64) == [(0, 32), (32 + gap, 64)]
    assert not source_complete(ledger, total_duration=64)
    assert not source_complete(ledger, total_duration=64, legacy_prefix=64)


def test_legacy_prefix_fills_only_before_first_explicit_record():
    ledger = [packet(32, 64, "unresolved", [[46, 47]]), packet(64, 100)]
    assert verified_prefix(ledger, total_duration=100, legacy_prefix=32) == 32
    assert scanned_frontier(ledger, total_duration=100, legacy_prefix=32) == 100
    assert verified_runs(ledger, total_duration=100, legacy_prefix=100) == [(0, 32), (64, 100)]
    assert verified_prefix(ledger, total_duration=100, legacy_prefix=20) == 20
    assert scanned_frontier(ledger, total_duration=100, legacy_prefix=20) == 20
    assert source_complete([], total_duration=100, legacy_prefix=100)
    assert not source_complete([], total_duration=100, legacy_prefix=32)


def test_unknown_initial_gap_is_not_scanned_by_later_success():
    ledger = [packet(32, 64)]
    assert verified_prefix(ledger, total_duration=64) == 0
    assert scanned_frontier(ledger, total_duration=64) == 0
    assert verified_runs(ledger, total_duration=64) == [(32, 64)]
    assert not source_complete(ledger, total_duration=64)


@pytest.mark.parametrize("duration", [0, 1])
def test_empty_source_never_implies_completion(duration):
    assert validate_intervals([], total_duration=duration) == []
    assert verified_prefix([], total_duration=duration) == 0
    assert scanned_frontier([], total_duration=duration) == 0
    assert verified_runs([], total_duration=duration) == []
    assert not source_complete([], total_duration=duration)


@pytest.mark.parametrize("value", [None, True, False, "1", math.inf, -math.inf, math.nan, -1, 10 ** 1000])
@pytest.mark.parametrize("field", ["start", "end", "total_duration", "legacy_prefix"])
def test_invalid_numbers_are_rejected_without_coercion(field, value):
    record, arguments = packet(0, 10), dict(total_duration=10, legacy_prefix=0)
    if field in record:
        record[field] = value
    else:
        arguments[field] = value
    with pytest.raises(SourceCoverageError):
        verified_prefix([record], **arguments)


@pytest.mark.parametrize("record", [
    {}, None, [], packet(2, 2), packet(3, 2), packet(0, 11),
    packet(0, 10, "READY"), packet(0, 10, []),
    packet(0, 10, spans=[[1, 2]]), packet(0, 10, error="old_error"),
    packet(2, 10, "unresolved", [[1, 3]]),
    packet(0, 10, "unresolved", [[1, 11]]),
    packet(0, 10, "unresolved", [[1, 3], [2, 4]]),
    packet(0, 10, "unresolved", [[1, 3], [1, 3]]),
    packet(0, 10, "unresolved", [[1]]),
    packet(0, 10, "unresolved", [[1, 2, 3]]),
    packet(0, 10, "unresolved", [[False, 1]]),
    packet(0, 10, "unresolved", error=1), packet(0, 10, "unresolved", error=" "),
])
def test_malformed_record_is_rejected(record):
    with pytest.raises(SourceCoverageError):
        validate_intervals([record], total_duration=10)


@pytest.mark.parametrize("records", [None, {}, "", [packet(0, 8), packet(7, 10)],
                                     [packet(0, 10), packet(0, 10)]])
def test_ledger_container_and_overlap_are_validated(records):
    with pytest.raises(SourceCoverageError):
        validate_intervals(records, total_duration=10)


def test_legacy_prefix_cannot_exceed_known_source_duration():
    with pytest.raises(SourceCoverageError, match="legacy_prefix"):
        source_complete([], total_duration=10, legacy_prefix=11)


def test_adjacent_diagnostics_keep_exact_source_seconds():
    ledger = [packet(32, 64, "unresolved", [[46.89, 47], [46.07, 46.89]])]
    normalized = validate_intervals(ledger, total_duration=64)
    assert normalized[0]["missing_speech_spans"] == [[46.07, 46.89], [46.89, 47]]
    assert normalized[0]["start"] == 32 and normalized[0]["end"] == 64


def test_bounded_ledger_accepts_limit_and_allows_exact_replacement_at_limit():
    ledger = [packet(index, index + 1) for index in range(MAX_SOURCE_INTERVALS)]
    result = record_interval(ledger, start=0, end=1, state="unresolved",
                             total_duration=MAX_SOURCE_INTERVALS)
    assert len(result) == MAX_SOURCE_INTERVALS
    assert scanned_frontier(result, total_duration=MAX_SOURCE_INTERVALS) == MAX_SOURCE_INTERVALS
    assert not source_complete(result, total_duration=MAX_SOURCE_INTERVALS)
    with pytest.raises(SourceCoverageError, match="bounded"):
        record_interval(result, start=MAX_SOURCE_INTERVALS, end=MAX_SOURCE_INTERVALS + 1,
                        state="prepared", total_duration=MAX_SOURCE_INTERVALS + 1)
