"""Clock/contract tests only; injected measurements are not real benchmarks."""
from types import SimpleNamespace

import pytest

from config import settings
from core.streaming import progress_eta as eta


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "WORKSPACE_DIR", tmp_path)
    return SimpleNamespace(translation_mode="full", _run_started_at=100., _run_ready_baseline=0,
        is_paused=False, is_stopped=False, is_running=True, error=None, output_filename="",
        caption_output_outdated=False, review_summary={"status": "running"}, _visual_prepass_complete=False,
        _reviewing_segment_ids=[], video_size=(1080, 1920), caption_style={}, total_duration=300,
        auto_export_result=True)


def state(**updates):
    return dict(status="RUNNING", processing_total_known=True, processing_completed_segments=3,
        processing_total_segments=9, segment_states={}, retry_chunks=0, **updates)


def test_unknown_source_and_no_samples_never_fabricate_eta(session):
    snapshot = state()
    snapshot["processing_total_known"] = False
    assert eta.processing_eta(session, snapshot, now=130)["eta_seconds"] is None
    snapshot.update(processing_total_known=True, processing_completed_segments=2)
    assert eta.processing_eta(session, snapshot, now=130)["eta_seconds"] is None


def test_scoped_estimate_does_not_claim_review_or_export_complete(session):
    result = eta.processing_eta(session, state(), now=130)
    assert result == {"eta_seconds": 60, "eta_scope": "speech", "eta_reason": "review_pending"}
    session.review_summary = {"status": "completed"}
    session._visual_prepass_complete = True
    assert eta.processing_eta(session, state(), now=130)["eta_scope"] == "speech"


def test_final_includes_validated_comparable_export_cost(session):
    profile = eta.export_profile(session.video_size, session.caption_style)
    eta.record_validated_export(profile, 60, 12)
    session.review_summary = {"status": "completed"}
    session._visual_prepass_complete = True
    result = eta.processing_eta(session, state(), now=130)
    assert result["eta_scope"] == "final" and result["eta_seconds"] == 120
    session.video_size = (3840, 2160)
    assert eta.processing_eta(session, state(), now=130)["eta_scope"] == "speech"
    session.video_size = (1080, 1920)
    session.review_summary["incomplete"] = 1
    assert eta.processing_eta(session, state(), now=130)["eta_scope"] == "speech"


def test_pause_time_excluded_and_resume_run_resets_baseline(session):
    eta.pause_eta(session, now=110)
    session.is_paused = True
    assert eta.processing_eta(session, state(), now=300)["eta_seconds"] is None
    session._processing_progress_metadata = lambda: state()
    eta.resume_eta(session, now=310)
    session.is_paused = False
    # In-flight TTS can finish during Pause. All work ready at Resume becomes
    # the new baseline, rather than being credited against unpaused seconds.
    assert eta.processing_eta(session, state(), now=330)["eta_seconds"] is None
    snapshot = state()
    snapshot["processing_completed_segments"] = 6
    assert eta.processing_eta(session, snapshot, now=340)["eta_seconds"] == 30
    session._run_started_at = 400
    session._run_ready_baseline = 3
    assert eta.processing_eta(session, state(), now=410)["eta_seconds"] is None
    snapshot = state()
    snapshot["processing_completed_segments"] = 6
    assert eta.processing_eta(session, snapshot, now=430)["eta_seconds"] == 30


@pytest.mark.parametrize("fault", ["paused", "stopped", "failed", "retry", "chunk", "idle"])
def test_inactive_or_failed_work_has_no_countdown(session, fault):
    snapshot = state()
    if fault == "paused":
        session.is_paused = True
    elif fault == "stopped":
        session.is_stopped = True
    elif fault == "failed":
        snapshot["segment_states"] = {"FAILED": 1}
    elif fault == "retry":
        snapshot["segment_states"] = {"RETRY_PENDING": 1}
    elif fault == "chunk":
        snapshot["retry_chunks"] = 1
    else:
        session.is_running = False
    assert eta.processing_eta(session, snapshot, now=130)["eta_seconds"] is None


def test_preview_scope_is_never_whole_video_eta(session):
    session.translation_mode = "preview"
    assert eta.processing_eta(session, state(), now=130) == {
        "eta_seconds": 60, "eta_scope": "preview", "eta_reason": "measured_processing"}


def test_ready_and_percent_do_not_imply_final_output(session):
    snapshot = state()
    snapshot.update(status="COMPLETED", processing_completed_segments=9, progress_pct=100)
    assert eta.processing_eta(session, snapshot, now=130)["eta_seconds"] is None
    session.output_filename = "validated.mp4"
    assert eta.processing_eta(session, snapshot, now=130)["eta_seconds"] == 0
    session.caption_output_outdated = True
    assert eta.processing_eta(session, snapshot, now=130)["eta_seconds"] is None


def test_export_estimate_never_reaches_zero_before_validation():
    assert eta.export_eta(60, 30)["eta_seconds"] == 30
    assert eta.export_eta(1, .96)["eta_seconds"] == .1
    assert eta.export_eta(60, 60)["eta_seconds"] is None
    assert eta.export_eta(60, 90)["eta_reason"] == "estimate_exceeded"
    assert eta.export_eta(None, 90)["eta_seconds"] is None
    for status in ["FAILED", "CANCELLED", "CANCELLING"]:
        assert eta.export_eta(60, 30, status)["eta_seconds"] is None
    assert eta.export_eta(60, 90, "COMPLETED")["eta_seconds"] == 0


def test_durable_samples_are_bounded_and_invalid_values_are_ignored(session):
    for value in [None, 0, -1, float("nan"), float("inf"), True]:
        eta.record_validated_export("p", value, 10)
        eta.record_validated_export("p", 10, value)
    assert not eta._database().exists()
    for i in range(140):
        eta.record_validated_export("p", 10, i + 1)
    import sqlite3
    with sqlite3.connect(eta._database()) as db:
        assert db.execute("SELECT count(*) FROM samples").fetchone()[0] == 128
    assert eta.estimated_export_seconds("p", 10) == 138
    assert eta.estimated_export_seconds("other-profile", 10) is None


def test_corrupt_measurement_cache_does_not_break_progress(session):
    eta._database().parent.mkdir(parents=True)
    eta._database().write_bytes(b"broken")
    assert eta.estimated_export_seconds("p", 60) is None
    eta.record_validated_export("p", 60, 30)


def test_export_poll_recomputes_eta_and_clears_on_cancellation(session, monkeypatch):
    import asyncio
    import main
    monkeypatch.setattr(main, "get_streaming_session", lambda _: None)
    monkeypatch.setattr(main.time, "monotonic", lambda: 130.)
    task = {"status": "RUNNING", "_eta_started_at": 100., "_eta_expected_seconds": 60.}
    monkeypatch.setattr(main, "active_export_tasks", {"export_measured": task})
    snapshot = asyncio.run(main.get_export_hq_status("measured"))
    assert snapshot["eta_seconds"] == 30 and snapshot["eta_scope"] == "final"
    task["status"] = "CANCELLING"
    assert asyncio.run(main.get_export_hq_status("measured"))["eta_seconds"] is None


def test_media_prefix_estimates_before_full_source_recognition(session):
    snapshot = state()
    snapshot.update(processing_total_known=False, processing_ready_seconds=30.)
    result = eta.processing_eta(session, snapshot, now=160)
    assert result["eta_seconds"] == 540 and result["eta_scope"] == "speech"
    snapshot["processing_ready_seconds"] = 0
    assert eta.processing_eta(session, snapshot, now=160)["eta_seconds"] is None
    snapshot["processing_ready_seconds"] = 2
    assert eta.processing_eta(session, snapshot, now=160)["eta_seconds"] is None


def test_resume_media_prefix_does_not_credit_old_or_paused_output(session):
    snapshot = state()
    snapshot.update(processing_total_known=False, processing_ready_seconds=60.)
    eta.reset_eta(session, now=100, snapshot=snapshot)
    assert eta.processing_eta(session, snapshot, now=160)["eta_seconds"] is None
    snapshot.update(processing_completed_segments=6, processing_ready_seconds=90.)
    assert eta.processing_eta(session, snapshot, now=160)["eta_seconds"] == 420
    eta.pause_eta(session, now=160)
    snapshot.update(processing_completed_segments=7, processing_ready_seconds=100.)
    session._processing_progress_metadata = lambda: snapshot
    eta.resume_eta(session, now=260)
    assert eta.processing_eta(session, snapshot, now=270)["eta_seconds"] is None


def test_measurement_cache_avoids_per_poll_sqlite_reads_and_refreshes(session, monkeypatch):
    import sqlite3
    eta.record_validated_export("p", 10, 20)
    assert eta.estimated_export_seconds("p", 30) == 60
    original = sqlite3.connect
    def forbidden(*args, **kwargs):
        raise AssertionError("unchanged measurements queried SQLite during UI poll")
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    for _ in range(10):
        assert eta.estimated_export_seconds("p", 30) == 60
    monkeypatch.setattr(sqlite3, "connect", original)
    eta.record_validated_export("p", 10, 40)
    assert eta.estimated_export_seconds("p", 30) == 90
