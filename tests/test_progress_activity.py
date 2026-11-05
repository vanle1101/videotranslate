"""Coverage cannot imply export completion; polling cannot imply worker progress."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.streaming.progress_activity import mark_activity, progress_activity


def fixture(*, live=True):
    owner = SimpleNamespace(done=lambda: not live)
    session = SimpleNamespace(_run_started_at=100., is_running=True, start_task=owner)
    snapshot = {"status": "RUNNING", "phase": "prepare", "stage": "Preparing source",
                "total_seconds": 400., "processed_seconds": 24.,
                "source_prepared_seconds": 32., "processing_ready_seconds": 12.}
    return session, snapshot


def test_partial_source_has_media_percentage_without_inventing_sentence_denominator():
    session, snapshot = fixture()
    snapshot.update(processing_total_known=False, processing_progress_pct=None, progress_pct=None)
    result = progress_activity(session, snapshot, now=110.)
    assert result["media_translation_pct"] == 6.
    assert result["media_ready_pct"] == 3.
    assert result["source_progress_pct"] == 8.
    assert snapshot["processing_progress_pct"] is snapshot["progress_pct"] is None


def test_repeated_polls_do_not_keep_quiet_provider_looking_active():
    session, snapshot = fixture()
    assert progress_activity(session, snapshot, now=110.)["activity_state"] == "active"
    for now in range(120, 300, 10):
        result = progress_activity(session, snapshot, now=now)
    assert result["activity_state"] == "waiting"
    assert result["activity_age_seconds"] == 190.
    assert result["activity_has_worker"]
    snapshot["source_prepared_seconds"] = 64.
    result = progress_activity(session, snapshot, now=301.)
    assert result["activity_state"] == "active"
    assert result["activity_age_seconds"] == 0.


def test_missing_owner_is_distinct_from_provider_wait():
    session, snapshot = fixture(live=False)
    result = progress_activity(session, snapshot, now=110.)
    assert result["activity_state"] == "stalled"
    assert not result["activity_has_worker"]


@pytest.mark.parametrize("status,state", [("PAUSED", "paused"), ("STOPPED", "stopped"),
    ("CANCELLING", "stopping"), ("FAILED", "failed")])
def test_user_and_terminal_states_override_a_quiet_worker(status, state):
    session, snapshot = fixture()
    snapshot["status"] = status
    assert progress_activity(session, snapshot, now=900.)["activity_state"] == state


def test_source_worker_label_does_not_linger_after_preparation():
    session, snapshot = fixture()
    session._source_preparation_active = True
    session._source_preparation_stage = "background_suppress"
    result = progress_activity(session, snapshot, now=110.)
    assert result["source_activity_stage"] == "background_suppress"
    assert "nhạc nền" in result["activity_stage"]
    session._source_preparation_active = False
    result = progress_activity(session, snapshot, now=111.)
    assert result["source_activity_stage"] is None
    assert result["activity_stage"] == snapshot["stage"]


def test_resume_uses_new_run_clock_and_real_events_refresh_activity():
    session, snapshot = fixture()
    progress_activity(session, snapshot, now=110.)
    mark_activity(session, now=150.)
    assert progress_activity(session, snapshot, now=160.)["activity_age_seconds"] == 10.
    session._run_started_at = 400.
    assert progress_activity(session, snapshot, now=405.)["activity_age_seconds"] == 5.


@pytest.mark.parametrize("duration", [0, None, float("nan"), float("inf")])
def test_unknown_duration_has_no_made_up_media_percentage(duration):
    session, snapshot = fixture()
    snapshot["total_seconds"] = duration
    result = progress_activity(session, snapshot, now=110.)
    assert result["media_translation_pct"] is None


def test_full_translation_coverage_does_not_claim_final_result_done():
    session, snapshot = fixture()
    snapshot["processed_seconds"] = 400.
    result = progress_activity(session, snapshot, now=110.)
    assert result["media_translation_pct"] == 100.
    assert result["activity_state"] == "active"
    assert result["media_ready_pct"] == 3.


def test_source_failure_clears_live_preparation_owner(monkeypatch):
    from core.streaming import chunked_source
    session, _ = fixture()
    monkeypatch.setattr(chunked_source, "_prepare_interval", AsyncMock(side_effect=ValueError("failure")))
    with pytest.raises(ValueError, match="failure"):
        asyncio.run(chunked_source.prepare_interval(session, 0., 24.))
    assert not session._source_preparation_active
    assert session._source_preparation_task is None
