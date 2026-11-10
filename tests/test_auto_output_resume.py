import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core.streaming import retry_lifecycle as lifecycle
from core.streaming.pipeline import StreamingPipelineSession, active_streaming_sessions
from core.streaming.session_store import _read
from test_session_store import persisted


@pytest.fixture
def ready_output(persisted, monkeypatch):
    persisted.run_intent = "running"
    persisted.auto_export_result = True
    persisted.review_summary = {"status": "completed"}
    persisted._persistence_enabled = True
    monkeypatch.setattr(StreamingPipelineSession, "retry_failed_synthesis", AsyncMock())
    monkeypatch.setattr(StreamingPipelineSession, "_update_ready", AsyncMock())
    monkeypatch.setattr("core.streaming.nonvisual_translation.translate_nonvisual_rows", AsyncMock())
    monkeypatch.setattr(StreamingPipelineSession, "_release_runtime", lambda self: None)
    persisted.persist()
    return persisted


@pytest.fixture
def pending_review(ready_output, monkeypatch):
    monkeypatch.setattr(lifecycle.settings, "LLM_PROVIDER", "opencode")
    ready_output.review_summary = {}
    ready_output.automatic_output = {"status": "pending", "policy": "strict_media", "reason": "review_pending"}
    ready_output.persist()
    return ready_output


@pytest.mark.parametrize("provider", ["opencode", "ollama"])
@pytest.mark.parametrize("status", [None, "running", "incomplete"])
def test_explicit_pending_review_resumes_without_retranslation(pending_review, monkeypatch, status, provider):
    monkeypatch.setattr(lifecycle.settings, "LLM_PROVIDER", provider)
    pending_review.review_summary = {} if status is None else {"status": status}
    pending_review.persist()
    calls, events, review_options = [], [], []

    async def review(self, **kwargs):
        calls.append(self.task_id)
        review_options.append(kwargs)
        self.review_summary = {"status": "completed"}

    monkeypatch.setattr(StreamingPipelineSession, "_review_translations", review)

    async def run():
        owners = await lifecycle.resume_interrupted_sessions(
            lambda task_id: lambda event, data: events.append(event))
        assert len(owners) == 1
        await asyncio.gather(*owners)
        current = active_streaming_sessions[pending_review.task_id]
        if current.review_task is not None:
            await current.review_task
        assert current.review_summary["status"] == "completed"
        assert current.run_intent == "running"

    asyncio.run(run())
    assert calls == [pending_review.task_id]
    assert review_options[0].get("fast_local", False) is (provider == "ollama")
    assert events.count("finished") == 1
    StreamingPipelineSession.retry_failed_synthesis.assert_not_awaited()


@pytest.mark.parametrize("change", ["idle", "paused", "stopped", "failed", "blocked", "missing_marker", "wrong_provider", "missing_audio"])
def test_pending_review_requires_exact_safe_resume_contract(pending_review, monkeypatch, change):
    if change in {"idle", "paused", "stopped"}:
        pending_review.run_intent = change
    elif change == "failed":
        pending_review.review_summary = {"status": "failed"}
    elif change == "blocked":
        pending_review.automatic_output["status"] = "blocked"
    elif change == "missing_marker":
        pending_review.automatic_output = None
    elif change == "wrong_provider":
        monkeypatch.setattr(lifecycle.settings, "LLM_PROVIDER", "openai")
    else:
        pending_review.segments[0].audio_path = None
    assert not lifecycle.has_pending_auto_review(pending_review)


def test_pending_review_without_callback_stays_pending(pending_review, monkeypatch):
    review = AsyncMock()
    monkeypatch.setattr(StreamingPipelineSession, "start_automatic_review", review)
    assert asyncio.run(lifecycle.resume_interrupted_sessions()) == []
    review.assert_not_awaited()
    assert _read(pending_review.task_id)["session"]["run_intent"] == "running"


@pytest.mark.parametrize("application_shutdown", [True, False])
def test_automatic_review_cancellation_persists_resume_or_stop(pending_review, monkeypatch, application_shutdown):
    async def run():
        entered = asyncio.Event()
        async def review(**kwargs):
            entered.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(pending_review, "_review_translations", review)
        await pending_review.start_automatic_review()
        owner = pending_review.review_task
        await asyncio.wait_for(entered.wait(), 2)
        if application_shutdown:
            pending_review.shutdown()
        else:
            owner.cancel()
        await asyncio.wait_for(asyncio.gather(owner, return_exceptions=True), 2)
        saved = _read(pending_review.task_id)["session"]
        assert saved["run_intent"] == ("running" if application_shutdown else "stopped")
        assert saved["review_summary"]["status"] == "incomplete"
        assert saved["automatic_output"]["status"] == ("pending" if application_shutdown else "cancelled")
        assert saved["automatic_output"]["reason"] == ("review_pending" if application_shutdown else "user_cancelled")
    asyncio.run(run())


@pytest.mark.parametrize("fails", [True, False])
def test_unsuccessful_automatic_review_is_not_restarted(pending_review, monkeypatch, fails):
    async def review(**kwargs):
        if fails:
            raise ValueError("review unavailable")
        pending_review.review_summary = {"status": "incomplete"}
    monkeypatch.setattr(pending_review, "_review_translations", review)
    async def run():
        await pending_review.start_automatic_review()
        await pending_review.review_task
        assert pending_review.run_intent == "idle"
        assert pending_review.automatic_output["reason"] == "review_incomplete"
        assert await lifecycle.resume_interrupted_sessions(lambda task_id: AsyncMock()) == []
    asyncio.run(run())


def test_restart_dispatches_finished_without_websocket_or_retranslation(ready_output):
    events = []

    async def run():
        owners = await lifecycle.resume_interrupted_sessions(
            lambda task_id: lambda event, data: events.append((task_id, event, data)))
        assert len(owners) == 1
        await asyncio.gather(*owners)
        await lifecycle.resume_interrupted_sessions(lambda task_id: lambda event, data: events.append(event))

    asyncio.run(run())
    assert len(events) == 1
    assert events[0][:2] == (ready_output.task_id, "finished")
    StreamingPipelineSession.retry_failed_synthesis.assert_not_awaited()
    assert _read(ready_output.task_id)["session"]["run_intent"] == "idle"


def test_complete_source_gap_draft_resumes_after_restart_without_retranslation(ready_output):
    session = _mark_complete_source_gap_draft(ready_output)
    session.review_summary = {"status": "incomplete"}
    session.automatic_output = None
    session.persist()
    events = []

    async def run():
        owners = await lifecycle.resume_interrupted_sessions(
            lambda task_id: lambda event, data: events.append((task_id, event)))
        assert len(owners) == 1
        await asyncio.gather(*owners)

    asyncio.run(run())

    assert events == [(session.task_id, "finished")]
    StreamingPipelineSession.retry_failed_synthesis.assert_not_awaited()


def test_idle_source_gap_draft_resumes_after_restart_without_retranslation(ready_output):
    session = _mark_complete_source_gap_draft(ready_output)
    session.run_intent = "idle"
    session.review_summary = {"status": "incomplete"}
    session.automatic_output = None
    session.persist()
    events = []

    async def run():
        owners = await lifecycle.resume_interrupted_sessions(
            lambda task_id: lambda event, data: events.append((task_id, event)))
        assert len(owners) == 1
        await asyncio.gather(*owners)

    asyncio.run(run())

    assert events == [(session.task_id, "finished")]
    StreamingPipelineSession.retry_failed_synthesis.assert_not_awaited()


def test_idle_incomplete_source_gap_remains_inert(ready_output):
    session = _mark_complete_source_gap_draft(ready_output)
    session.run_intent = "idle"
    session._source_scanned_seconds -= 1
    session._source_coverage_intervals[-1]["end"] -= 1
    session.persist()
    callback = AsyncMock()

    assert asyncio.run(lifecycle.resume_interrupted_sessions(lambda _task: callback)) == []
    callback.assert_not_awaited()


def _mark_complete_source_gap_draft(session):
    session._chunked_source_started = True
    session._visual_incremental_started = True
    session._visual_prepass_complete = False
    session._visual_completed_seconds = 0.8
    session._source_prepared_seconds = session.total_duration
    session._source_scanned_seconds = session.total_duration
    session._source_missing_speech_spans = [[0.8, 0.928]]
    session._source_failure_code = "source_speech_unrecognized"
    session._source_failure_start = 0.8
    session._source_coverage_intervals = [
        {"start": 0.0, "end": 0.8, "state": "prepared", "missing_speech_spans": [], "error_code": None},
        {"start": 0.8, "end": 0.928, "state": "unresolved",
         "missing_speech_spans": [[0.8, 0.928]], "error_code": "source_speech_unrecognized"},
        {"start": 0.928, "end": 2.0, "state": "prepared", "missing_speech_spans": [], "error_code": None},
    ]
    session._chunk_jobs = []
    return session


@pytest.mark.parametrize("damage", ["unscanned_tail", "incomplete_chunk", "wrong_gap_code", "visual_incomplete"])
def test_source_gap_draft_resume_requires_complete_source_and_media_evidence(ready_output, damage):
    session = _mark_complete_source_gap_draft(ready_output)
    if damage == "unscanned_tail":
        session._source_scanned_seconds = session.total_duration - 1
    elif damage == "incomplete_chunk":
        session._chunk_jobs = [{"state": "FAILED"}]
    elif damage == "wrong_gap_code":
        session._source_coverage_intervals[1]["error_code"] = "source_missing"
    else:
        session.visual_translation = True
        session._visual_completed_seconds = session.total_duration - 1

    assert not lifecycle.has_pending_auto_output(session)


def test_no_backend_callback_preserves_pending_intent(ready_output):
    assert asyncio.run(lifecycle.resume_interrupted_sessions()) == []
    assert _read(ready_output.task_id)["session"]["run_intent"] == "running"
    StreamingPipelineSession.retry_failed_synthesis.assert_not_awaited()


@pytest.mark.parametrize("intent", ["idle", "paused", "stopped"])
def test_historical_idle_paused_stopped_projects_never_dispatch(ready_output, intent):
    ready_output.run_intent = intent
    ready_output.persist()
    callback = AsyncMock()
    assert asyncio.run(lifecycle.resume_interrupted_sessions(lambda task_id: callback)) == []
    callback.assert_not_awaited()
    assert ready_output.task_id not in active_streaming_sessions


@pytest.mark.parametrize("status", [None, "running", "incomplete", "failed"])
def test_uncompleted_review_cannot_resume_export(ready_output, status):
    ready_output.review_summary = {} if status is None else {"status": status}
    ready_output.persist()
    callback = AsyncMock()
    assert asyncio.run(lifecycle.resume_interrupted_sessions(lambda task_id: callback)) == []
    callback.assert_not_awaited()
    StreamingPipelineSession.retry_failed_synthesis.assert_not_awaited()


@pytest.mark.parametrize("field,value", [
    ("run_intent", "idle"), ("is_stopped", True), ("is_paused", True),
    ("error", "render blocked"), ("auto_export_result", False),
    ("output_filename", "published.mp4"), ("output_video_url", "/api/outputs/published.mp4"),
    ("_restored_source_missing", True), ("_source_failure_code", "source_missing"),
    ("translation_mode", "preview"), ("_source_prepared_seconds", 1.),
])
def test_only_complete_uncancelled_media_retains_intent(ready_output, field, value):
    ready_output._chunked_source_started = True
    ready_output._visual_prepass_complete = True
    ready_output._source_prepared_seconds = ready_output.total_duration
    setattr(ready_output, field, value)
    assert not lifecycle.has_pending_auto_output(ready_output)


def test_previous_publication_is_not_reexported_even_if_output_is_missing(ready_output):
    ready_output.output_filename = "published.mp4"
    ready_output.persist()
    callback = AsyncMock()
    assert asyncio.run(lifecycle.resume_interrupted_sessions(lambda task_id: callback)) == []
    callback.assert_not_awaited()
    StreamingPipelineSession.retry_failed_synthesis.assert_not_awaited()


def test_missing_audio_or_failed_row_never_becomes_export_ready(ready_output):
    row = ready_output.segments[0]
    row.status = "FAILED"
    assert not lifecycle.has_pending_auto_output(ready_output)
    row.status = "READY"
    row.audio_path = str(Path(row.audio_path).with_name("absent.wav"))
    assert not lifecycle.has_pending_auto_output(ready_output)


def test_reviewed_editorial_draft_remains_truthful_and_renderable(ready_output):
    ready_output.segments[0].needs_review = True
    assert lifecycle.has_pending_auto_output(ready_output)
    assert ready_output._final_output_metadata()["output_is_draft"] is True


@pytest.mark.parametrize("status", ["blocked", "failed", "completed", "cancelled"])
def test_terminal_automatic_output_never_retries_after_restart(ready_output, status):
    ready_output.automatic_output = {"status": status, "policy": "strict_media", "reason": "test_state"}
    ready_output.persist()
    callback = AsyncMock()
    assert asyncio.run(lifecycle.resume_interrupted_sessions(lambda task_id: callback)) == []
    callback.assert_not_awaited()


def test_exhausted_persisted_export_budget_is_inert(ready_output):
    ready_output._auto_export_attempts = ("a" * 64, 2)
    ready_output.persist()
    callback = AsyncMock()
    assert asyncio.run(lifecycle.resume_interrupted_sessions(lambda task_id: callback)) == []
    callback.assert_not_awaited()
    assert _read(ready_output.task_id)["session"]["run_intent"] == "idle"


@pytest.mark.parametrize("intent", ["paused", "stopped"])
def test_stop_or_pause_between_restore_and_dispatch_is_respected(ready_output, intent):
    callback = AsyncMock()

    async def run():
        owners = await lifecycle.resume_interrupted_sessions(lambda task_id: callback)
        current = active_streaming_sessions[ready_output.task_id]
        current.run_intent = intent
        current.is_stopped = intent == "stopped"
        current.is_paused = intent == "paused"
        await asyncio.gather(*owners)
        assert current.run_intent == intent

    asyncio.run(run())
    callback.assert_not_awaited()


def test_restored_export_owner_keeps_intent_until_backend_finishes(ready_output):
    async def run():
        release = asyncio.Event()

        async def callback(event, data):
            current = active_streaming_sessions[ready_output.task_id]
            current.auto_export_task = asyncio.create_task(release.wait())

        owners = await lifecycle.resume_interrupted_sessions(lambda task_id: callback)
        await asyncio.gather(*owners)
        current = active_streaming_sessions[ready_output.task_id]
        assert current.run_intent == "running"
        assert current.start_task is None
        release.set()
        await current.auto_export_task

    asyncio.run(run())
    assert _read(ready_output.task_id)["session"]["run_intent"] == "running"


def test_callback_failure_is_not_retried_forever(ready_output):
    callback = AsyncMock(side_effect=ValueError("invalid export"))

    async def run():
        owners = await lifecycle.resume_interrupted_sessions(lambda task_id: callback)
        await asyncio.gather(*owners)
        active_streaming_sessions.clear()
        assert await lifecycle.resume_interrupted_sessions(lambda task_id: callback) == []

    asyncio.run(run())
    callback.assert_awaited_once()
    assert _read(ready_output.task_id)["session"]["run_intent"] == "idle"


@pytest.mark.parametrize("provider", ["opencode", "ollama"])
def test_pipeline_checkpoints_first_automatic_review_before_finished(ready_output, monkeypatch, provider):
    monkeypatch.setattr(lifecycle.settings, "LLM_PROVIDER", provider)
    ready_output.review_summary = {}
    observed = []
    async def callback(event, payload):
        if event == "finished":
            observed.append(_read(ready_output.task_id)["session"])
    ready_output.event_callback = callback
    asyncio.run(ready_output._worker_loop())
    assert len(observed) == 1
    assert observed[0]["run_intent"] == "running"
    assert observed[0]["automatic_output"] == {
        "status": "pending", "policy": "strict_media", "reason": "review_pending"}
    assert not observed[0]["review_summary"]


@pytest.mark.parametrize("change", ["idle", "no_autoexport", "failed_review", "blocked", "wrong_provider"])
def test_live_completion_does_not_invent_first_review_intent(ready_output, monkeypatch, change):
    monkeypatch.setattr(lifecycle.settings, "LLM_PROVIDER", "opencode")
    ready_output.review_summary = {}
    if change == "idle":
        ready_output.run_intent = "idle"
    elif change == "no_autoexport":
        ready_output.auto_export_result = False
    elif change == "failed_review":
        ready_output.review_summary = {"status": "failed"}
    elif change == "blocked":
        ready_output.automatic_output = {"status": "blocked", "policy": "strict_media", "reason": "review_incomplete"}
    else:
        monkeypatch.setattr(lifecycle.settings, "LLM_PROVIDER", "openai")
    assert not lifecycle.prepare_auto_review(ready_output)


def test_pipeline_checkpoints_pending_output_before_finished(ready_output, monkeypatch):
    observed = []

    async def callback(event, payload):
        if event == "finished":
            observed.append(_read(ready_output.task_id)["session"]["run_intent"])

    ready_output.event_callback = callback
    monkeypatch.setattr(ready_output, "_recover_transient_speech_after_source", AsyncMock())
    monkeypatch.setattr(ready_output, "_recover_pacing_after_source", AsyncMock())
    asyncio.run(ready_output._worker_loop())
    assert observed == ["running"]


@pytest.mark.parametrize("intent", ["paused", "stopped"])
def test_pipeline_completion_does_not_erase_stop_or_pause(ready_output, intent):
    ready_output.run_intent = intent
    ready_output.is_paused = intent == "paused"
    ready_output.is_stopped = intent == "stopped"
    asyncio.run(ready_output._worker_loop())
    assert ready_output.run_intent == intent


@pytest.mark.parametrize("application_shutdown", [True, False])
def test_export_owner_shutdown_cancels_once_and_waits_for_drain(ready_output, application_shutdown):
    async def run():
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def export():
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        owner = asyncio.create_task(export())
        ready_output.export_task = ready_output.auto_export_task = owner
        active_streaming_sessions[ready_output.task_id] = ready_output
        await entered.wait()
        if application_shutdown:
            ready_output.shutdown()
        else:
            ready_output.stop()
        assert not owner.done()
        assert active_streaming_sessions[ready_output.task_id] is ready_output
        assert ready_output.run_intent == ("running" if application_shutdown else "stopped")
        ready_output.stop(_application_shutdown=application_shutdown)
        assert ready_output._stop_cancelled_owners == {owner}
        await asyncio.wait_for(asyncio.gather(owner, return_exceptions=True), timeout=2)
        assert cancelled.is_set()
        assert owner.cancelled()
        assert _read(ready_output.task_id)["session"]["run_intent"] == ready_output.run_intent

    asyncio.run(run())


@pytest.mark.parametrize("application_shutdown", [True, False])
def test_dispatch_cancellation_preserves_only_shutdown_intent(ready_output, application_shutdown):
    async def run():
        entered = asyncio.Event()

        async def callback(event, data):
            entered.set()
            await asyncio.Event().wait()

        owners = await lifecycle.resume_interrupted_sessions(lambda task_id: callback)
        await entered.wait()
        current = active_streaming_sessions[ready_output.task_id]
        if application_shutdown:
            current.shutdown()
        else:
            owners[0].cancel()
        await asyncio.gather(*owners, return_exceptions=True)
        assert current.run_intent == ("running" if application_shutdown else "stopped")

    asyncio.run(run())
