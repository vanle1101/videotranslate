"""Retry telemetry reflects scheduled work rather than a failure-stage label."""
import asyncio

import pytest

from core.streaming.pipeline import SegmentItem
from core.streaming.recovery import reserve, scheduled_segment_ids, segment_state
from core.streaming.retry_schedule import schedule_retry
from core.engines.translation.opencode_client import OpenCodeRequestError
from test_transcript_edit import session
from test_preview_chunked_pipeline import preview


def test_unknown_translation_failure_is_failed_in_snapshot_event_and_counts(session):
    row = session.segments[0]
    row.status, row.failed_stage = "FAILED", "TRANSLATING"
    assert row.to_dict()["processing_state"] == "FAILED"
    assert session.segment_snapshot(row)["processing_state"] == "FAILED"
    asyncio.run(session.emit("segment_update", row.to_dict()))
    assert session.events[-1][1]["processing_state"] == "FAILED"
    progress = session.get_progress()
    assert progress["segment_states"]["FAILED"] == 1
    assert progress["segment_states"]["RETRY_PENDING"] == progress["retry_pending_count"] == 0


def test_source_retry_scope_updates_only_missing_owned_rows(session):
    row = session.segments[0]
    row.status, row.failed_stage = "FAILED", "TRANSLATING"
    ready = SegmentItem(1, 6, 8, 2)
    ready.status, ready.translation_provider = "READY", "opencode"
    session.segments[1] = ready
    chunk = reserve(session._chunk_jobs, 0, 9, "a" * 64)
    schedule_retry(session, "source", chunk["id"], TimeoutError(), chunk["input_hash"], now=0)
    assert scheduled_segment_ids(session.segments.values(), session._retry_schedule, session._chunk_jobs) == {0}
    assert session.segment_snapshot(row)["processing_state"] == "RETRY_PENDING"
    assert session.get_progress()["retry_chunks"] == 1
    session._retry_schedule.clear()
    assert session.segment_snapshot(row)["processing_state"] == "FAILED"
    assert session.get_progress()["retry_chunks"] == 0


def test_stale_source_hash_never_claims_pending_rows(session):
    row = session.segments[0]
    row.status, row.failed_stage = "FAILED", "TRANSLATING"
    chunk = reserve(session._chunk_jobs, 0, 9, "a" * 64)
    schedule_retry(session, "source", chunk["id"], TimeoutError(), "b" * 64, now=0)
    assert not scheduled_segment_ids([row], session._retry_schedule, session._chunk_jobs)


@pytest.mark.parametrize("stage", ["speech", "review"])
def test_row_retry_requires_pending_ledger_record(session, stage):
    row = session.segments[0]
    row.translation_provider = "opencode"
    schedule_retry(session, stage, str(row.id), TimeoutError(), "a" * 64, now=0)
    assert session.segment_snapshot(row)["processing_state"] == "RETRY_PENDING"
    session._retry_schedule[0]["state"] = "RUNNING"
    assert session.segment_snapshot(row)["processing_state"] != "RETRY_PENDING"


def test_empty_ledger_does_not_scan_project_rows():
    def rows():
        raise AssertionError("No retry work should inspect every project row")
        yield
    assert scheduled_segment_ids(rows(), []) == set()


def test_terminal_source_error_preserves_explicit_retry_without_fake_schedule(preview, monkeypatch):
    owner, *_ = preview
    def fail(*args, **kwargs):
        raise OpenCodeRequestError("Unknown failure", code="provider_failed")
    monkeypatch.setattr(owner.video_intelligence, "prepass", fail)
    async def run():
        await owner.start()
        await owner.worker_task
        assert owner.can_retry
        assert owner._chunk_jobs[0]["state"] == "FAILED"
        assert not owner._retry_schedule
        assert owner.get_progress()["retry_chunks"] == 0
        assert all(owner.segment_snapshot(row)["processing_state"] == "FAILED"
            for row in owner.segments.values() if row.failed_stage == "TRANSLATING")
    asyncio.run(run())
