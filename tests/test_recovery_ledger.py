"""Fault/state contracts, not real provider acceptance."""
from copy import deepcopy
import time

import pytest

from core.streaming.recovery import (reserve, transition, contiguous_coverage,
                                     recover_interrupted, validate_records, input_hash)
from core.streaming.pipeline import SegmentItem


def test_crash_preserves_completed_intervals_and_recovers_only_running():
    records = []
    for start in (0, 60, 120):
        record = reserve(records, start, start + 60, "a" * 64)
        transition(record, "RUNNING")
        if start != 60:
            transition(record, "COMPLETED")
    checkpoint = deepcopy(records)
    recover_interrupted(checkpoint)
    validate_records(checkpoint, 180)
    assert [row["state"] for row in checkpoint] == ["COMPLETED", "RETRY_PENDING", "COMPLETED"]
    assert contiguous_coverage(checkpoint) == 60
    assert checkpoint[0] == records[0] and checkpoint[2] == records[2]
    transition(checkpoint[1], "RUNNING")
    transition(checkpoint[1], "COMPLETED")
    assert contiguous_coverage(checkpoint) == 180 and checkpoint[1]["attempts"] == 2


def test_configuration_change_invalidates_exact_interval_only():
    records = []
    source = SegmentItem(3, 0, 1, 1)
    source.asr_text = "你好"
    signature = input_hash([source], {"model": "A", "revision": 1})
    first = reserve(records, 0, 60, signature)
    transition(first, "RUNNING")
    transition(first, "COMPLETED")
    other = reserve(records, 60, 120, "b" * 64)
    snapshot = deepcopy(other)
    assert reserve(records, 0, 60, signature)["state"] == "COMPLETED"
    revised = reserve(records, 0, 60, input_hash([source], {"model": "B", "revision": 1}))
    assert revised["state"] == "PENDING" and revised["attempts"] == 0
    assert records[1] == snapshot and len(records) == 2


@pytest.mark.parametrize("mutation", [
    lambda row: row.update(state="success"),
    lambda row: row.update(attempts=True),
    lambda row: row.update(start=float("nan")),
    lambda row: row.update(input_hash="missing"),
    lambda row: row.update(end=61),
    lambda row: row.update(error_code="x" * 81),
    lambda row: row.update(untrusted_provider_field=True),
])
def test_corrupt_checkpoint_cannot_become_resume_success(mutation):
    records = []
    row = reserve(records, 0, 60, "a" * 64)
    mutation(row)
    with pytest.raises(ValueError):
        validate_records(records, 60)


@pytest.mark.parametrize("hours", [.0833333333, .5, 1, 4])
def test_simulated_manifest_workload_without_media_or_api(hours, record_property):
    started = time.perf_counter()
    count = round(hours * 3600 / 60)
    records = []
    for index in range(count):
        row = reserve(records, index * 60, (index + 1) * 60, "a" * 64)
        transition(row, "RUNNING")
        transition(row, "COMPLETED")
    validate_records(records, count * 60)
    assert contiguous_coverage(records) == count * 60
    record_property("simulated_workload_seconds", count * 60)
    record_property("ledger_elapsed_seconds", time.perf_counter() - started)
