"""Restart/cooldown ownership uses persisted intent, never inferred success."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from config import settings
from core.streaming import retry_lifecycle as lifecycle
from core.streaming.pipeline import active_streaming_sessions
from test_initial_transient_speech_retry import session, install_failure, transient
from test_session_store import persisted


def install_projects(tmp_path, monkeypatch, intents, retry=()):
    projects = tmp_path / "workspace" / "projects"
    projects.mkdir(parents=True)
    monkeypatch.setattr(lifecycle.settings, "BASE_DIR", tmp_path)
    restored = []
    sessions = {}
    for key, intent in intents.items():
        (projects / (key + ".json")).write_text("{}")
        event = asyncio.Event()
        event.set()
        sessions[key] = SimpleNamespace(task_id=key, is_stopped=True, is_running=False, is_paused=False,
            can_retry=True, run_intent=intent, retry_state=deepcopy(list(retry)), pause_event=event,
            start_task=None, _chunked_source_identity="source1", _persist_if_enabled=Mock(),
            retry_failed_synthesis=AsyncMock())
    monkeypatch.setattr("core.streaming.session_store._read", lambda key: {"session": {"run_intent": intents[key]}})
    def restore(key, callback=None):
        restored.append(key)
        return sessions[key]
    monkeypatch.setattr("core.streaming.session_store.restore_saved_session", restore)
    return sessions, restored


def test_only_explicit_running_projects_resume(tmp_path, monkeypatch):
    sessions, restored = install_projects(tmp_path, monkeypatch,
        {"running": "running", "paused": "paused", "stopped": "stopped", "idle": "idle", "legacy": None})
    async def run():
        owners = await lifecycle.resume_interrupted_sessions()
        await asyncio.gather(*owners)
    asyncio.run(run())
    assert restored == ["running"]
    sessions["running"].retry_failed_synthesis.assert_awaited_once()
    for key in ("paused", "stopped", "idle", "legacy"):
        sessions[key].retry_failed_synthesis.assert_not_awaited()


def test_restart_waits_saved_cooldown_and_stop_interrupts_while_paused(tmp_path, monkeypatch):
    records = [{"label": "TTS 38", "attempt": 7, "next_attempt_at": 500., "source_version": "source1"}]
    sessions, _ = install_projects(tmp_path, monkeypatch, {"run": "running"}, records)
    current, now, sleeps = sessions["run"], [100.], []
    current.is_paused = True
    current.pause_event.clear()
    monkeypatch.setattr(lifecycle.time, "time", lambda: now[0])
    async def sleep(delay):
        sleeps.append(delay)
        now[0] += delay
        if len(sleeps) == 3:
            current.is_stopped = True
            current.run_intent = "stopped"
    monkeypatch.setattr(lifecycle.asyncio, "sleep", sleep)
    async def run():
        owners = await lifecycle.resume_interrupted_sessions()
        await asyncio.gather(*owners)
    asyncio.run(run())
    assert sleeps == [.25] * 3
    current.retry_failed_synthesis.assert_not_awaited()
    assert current.retry_state[0]["next_attempt_at"] == 500.75
    assert current.start_task is None and not current.is_running


def test_expired_and_stale_source_cooldowns_do_not_hold_restart(tmp_path, monkeypatch):
    records = [{"label": "old source", "attempt": 5, "next_attempt_at": 1e12, "source_version": "old"}]
    sessions, _ = install_projects(tmp_path, monkeypatch, {"run": "running"}, records)
    async def run():
        owners = await lifecycle.resume_interrupted_sessions()
        await asyncio.gather(*owners)
    asyncio.run(run())
    sessions["run"].retry_failed_synthesis.assert_awaited_once()
    assert not sessions["run"].retry_state


def test_durable_speech_retry_exceeds_old_limit_without_redoing_healthy_rows(session, monkeypatch):
    calls = install_failure(session, monkeypatch)
    session._persistence_enabled = True
    monkeypatch.setattr(session, "_persist_if_enabled", lambda: None)
    monkeypatch.setattr(session, "persist", lambda: None)
    waits, attempts = [], []
    async def wait(label, attempt, delay):
        waits.append((attempt, delay))
    async def speech(row):
        attempts.append(row.id)
        row.status = "TTS"
        if len(attempts) < 8:
            raise transient()
        row.status = "READY"
    monkeypatch.setattr(session, "_wait_automatic_recovery", wait)
    monkeypatch.setattr(session, "_synthesize_segment", speech)
    for row in session.segments.values():
        session.queue.put_nowait((row.start, row.id))
    asyncio.run(session._worker_loop())
    assert calls == [("process", 0), ("process", 1), ("process", 2)]
    assert attempts == [0] * 8
    assert all(delay >= 300. for _, delay in waits)
    assert session.segments[1].status == session.segments[2].status == "READY"


def test_durable_local_ollama_failure_uses_short_backoff(session, monkeypatch):
    from core.engines.translation.ollama_client import OllamaClientError
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    session._persistence_enabled = True

    delay = session._recovery_delay(1, OllamaClientError("local busy", retryable=True))

    assert 2.0 <= delay <= 3.0


def test_durable_local_ollama_recovery_exhausts_after_bounded_attempts(session):
    from core.engines.translation.ollama_client import OllamaClientError
    session._persistence_enabled = True
    error = OllamaClientError("local service unavailable", retryable=True, code="provider_transport")

    delays = [session._recovery_delay(attempt, error) for attempt in range(1, 5)]

    assert all(delay is not None and delay > 0 for delay in delays[:3])
    assert delays[3] is None


def test_application_shutdown_preserves_intent_but_user_stop_clears_it(session, monkeypatch):
    monkeypatch.setattr(session, "_persist_if_enabled", lambda: None)
    session.run_intent = "running"
    session.retry_state = [{"label": "TTS", "attempt": 2, "next_attempt_at": 500., "source_version": None}]
    session.shutdown()
    assert session.run_intent == "running" and session.retry_state
    session.stop()
    assert session.run_intent == "stopped" and not session.retry_state


def test_pipeline_cooldown_saved_before_wait_and_retained_on_cancel(session, monkeypatch):
    session._persistence_enabled = True
    session.run_intent = "running"
    saved = []
    monkeypatch.setattr(session, "_persist_if_enabled", lambda: saved.append(deepcopy(session.retry_state)))
    async def cancel_on_wait(phase, *args, **kwargs):
        if phase == "recovery_wait":
            raise asyncio.CancelledError
    monkeypatch.setattr(session, "report_progress", cancel_on_wait)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(session._wait_automatic_recovery("TTS 38", 19, 2.))
    assert len(saved) == 1 and saved[0][0]["attempt"] == 19
    assert saved[0][0]["label"] == "TTS 38"
    assert saved[0][0]["next_attempt_at"] > 300.
    assert session.retry_state == saved[0]
    assert session._automatic_recovery_wait is None


def test_paused_remaining_and_running_intent_survive_real_project_roundtrip(persisted):
    from core.streaming.session_store import restore_saved_session
    persisted.run_intent = "running"
    persisted.retry_state = [{"label": "TTS 38", "attempt": 9, "next_attempt_at": 500.,
                              "source_version": "source-revision", "remaining_seconds": 222.}]
    persisted.persist()
    actual = restore_saved_session(persisted.task_id)
    assert actual.run_intent == "running"
    assert actual.retry_state == persisted.retry_state


def test_pause_and_resume_reanchor_durable_remaining(session, monkeypatch):
    session.retry_state = [{"label": "TTS", "attempt": 2, "next_attempt_at": 400., "source_version": None}]
    session._automatic_recovery_waits = {object(): {"label": "TTS", "remaining": 240.}}
    monkeypatch.setattr(lifecycle.time, "time", lambda: 100.)
    session.pause()
    assert session.retry_state[0]["remaining_seconds"] == 240.
    assert session.run_intent == "paused"
    session._automatic_recovery_waits = {}
    monkeypatch.setattr(lifecycle.time, "time", lambda: 1000.)
    session.resume()
    assert session.retry_state[0]["next_attempt_at"] == 1240.
    assert session.run_intent == "running"


def test_pending_transport_checkpoint_exists_before_wait_and_keeps_sibling(session, monkeypatch):
    session._persistence_enabled = True
    session.run_intent = "running"
    session._chunked_source_identity = "source1"
    session._source_failure_code = "source_speech_unrecognized"
    session.retry_state = [{"label": "unrelated TTS", "attempt": 3, "next_attempt_at": 900.,
                            "source_version": "source1"}]
    saved = []
    monkeypatch.setattr(session, "_persist_if_enabled", lambda: saved.append(deepcopy(session.retry_state)))
    marker = lifecycle.checkpoint_pending_retry(session, "translation 0-24", 1, 300., now=100.)
    assert marker == {"label": "translation 0-24", "attempt": 1, "next_attempt_at": 400.,
                      "source_version": "source1"}
    assert saved == [session.retry_state]
    assert session._source_failure_code == "source_speech_unrecognized"
    lifecycle.clear_pending_retry(session, "translation 0-24")
    assert session.retry_state == [{"label": "unrelated TTS", "attempt": 3, "next_attempt_at": 900.,
                                   "source_version": "source1"}]
    assert len(saved) == 2


def test_later_shorter_pending_retry_cannot_shorten_provider_deadline(session, monkeypatch):
    session._persistence_enabled = True
    session.run_intent = "running"
    monkeypatch.setattr(session, "_persist_if_enabled", lambda: None)
    lifecycle.checkpoint_pending_retry(session, "translation 0-24", 6, 900., now=100.)
    marker = lifecycle.checkpoint_pending_retry(session, "translation 0-24", 1, 300., now=200.)
    assert marker["next_attempt_at"] == 1000.
    assert marker["attempt"] == 6
    assert len(session.retry_state) == 1


def test_pending_retry_from_changed_source_replaces_stale_deadline(session, monkeypatch):
    session._persistence_enabled = True
    session.run_intent = "running"
    session._chunked_source_identity = "old"
    monkeypatch.setattr(session, "_persist_if_enabled", lambda: None)
    lifecycle.checkpoint_pending_retry(session, "translation 0-24", 20, 900., now=100.)
    session._chunked_source_identity = "new"
    marker = lifecycle.checkpoint_pending_retry(session, "translation 0-24", 1, 300., now=200.)
    assert marker == {"label": "translation 0-24", "attempt": 1, "next_attempt_at": 500.,
                      "source_version": "new"}


def test_pause_freezes_pending_cooldown_even_before_wait_owner_exists(session, monkeypatch):
    session._persistence_enabled = True
    session.run_intent = "running"
    monkeypatch.setattr(session, "_persist_if_enabled", lambda: None)
    lifecycle.checkpoint_pending_retry(session, "translation 0-24", 1, 900., now=100.)
    monkeypatch.setattr(lifecycle.time, "time", lambda: 110.)
    session.pause()
    marker = lifecycle.checkpoint_pending_retry(session, "translation 0-24", 2, 300., now=2000.)
    assert marker["remaining_seconds"] == 890.
    assert marker["next_attempt_at"] == 2890.
    monkeypatch.setattr(lifecycle.time, "time", lambda: 3000.)
    session.resume()
    assert session.retry_state[0]["next_attempt_at"] == 3890.


@pytest.mark.parametrize("durable,stopped,intent", [
    (False, False, "running"), (True, True, "running"), (True, False, "stopped"),
])
def test_stopped_or_ephemeral_owner_cannot_create_pending_checkpoint(session, monkeypatch, durable, stopped, intent):
    session._persistence_enabled = durable
    session.is_stopped = stopped
    session.run_intent = intent
    persist = Mock()
    monkeypatch.setattr(session, "_persist_if_enabled", persist)
    assert lifecycle.checkpoint_pending_retry(session, "translation 0-24", 1, 300., now=100.) is None
    assert not session.retry_state
    persist.assert_not_called()


def test_pending_cooldown_survives_shutdown_before_wait_is_started(session, monkeypatch):
    session._persistence_enabled = True
    session.run_intent = "running"
    monkeypatch.setattr(session, "_persist_if_enabled", lambda: None)
    lifecycle.checkpoint_pending_retry(session, "translation 0-24", 1, 300., now=100.)
    session.shutdown()
    assert session.run_intent == "running"
    assert len(session.retry_state) == 1
    assert session.retry_state[0]["label"] == "translation 0-24"
    session.stop()
    assert session.run_intent == "stopped" and not session.retry_state


def test_restart_waits_pending_translation_checkpoint_with_source_still_blocked(tmp_path, monkeypatch):
    records = [{"label": "translation 0-24", "attempt": 2, "next_attempt_at": 100.75,
                "source_version": "source1"}]
    sessions, _ = install_projects(tmp_path, monkeypatch, {"run": "running"}, records)
    current, now, sleeps = sessions["run"], [100.], []
    current._source_failure_code = "source_speech_unrecognized"
    current._source_prepared_seconds = 32.
    current._source_missing_speech_spans = [[46.07, 46.89]]
    monkeypatch.setattr(lifecycle.time, "time", lambda: now[0])
    async def sleep(delay):
        sleeps.append(delay)
        now[0] += delay
        current.retry_failed_synthesis.assert_not_awaited()
    monkeypatch.setattr(lifecycle.asyncio, "sleep", sleep)
    async def run():
        await asyncio.gather(*await lifecycle.resume_interrupted_sessions())
    asyncio.run(run())
    assert sleeps == [.25] * 3
    current.retry_failed_synthesis.assert_awaited_once()
    assert current._source_missing_speech_spans == [[46.07, 46.89]]
    assert not current.retry_state
