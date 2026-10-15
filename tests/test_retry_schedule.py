"""Durable retry-wave fault injection; no real provider or user jobs."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from core.streaming.retry_schedule import (
    begin_retry, can_auto_resume, due_entries, finish_retry, mark_intent,
    recover_schedule, restart_intent, schedule_retry, transient_failure,
    validate_schedule, wait_until_due,
)
from core.engines.translation.opencode_client import OpenCodeRequestError, OpenCodeTimeoutError

HASH = "a" * 64


def session():
    return SimpleNamespace(_restart_intent="active", _retry_schedule=[], is_stopped=False)


@pytest.mark.parametrize("code", ["provider_transport", "provider_rate_limited", "provider_server",
                                  "provider_queue_timeout", "provider_circuit_open"])
def test_typed_transient_errors_schedule_separate_waves(code):
    owner = session()
    error = OpenCodeRequestError("secret must not enter ledger", code=code, retryable=True)
    row = schedule_retry(owner, "translate", "segment:113", error, HASH, now=100, cooldown=1)
    assert row["next_attempt_at"] == 400 and row["rounds"] == 1
    assert due_entries(owner, now=399) == []
    assert len(due_entries(owner, now=400)) == 1
    assert begin_retry(owner, "translate", "segment:113", HASH, now=400)
    assert not begin_retry(owner, "translate", "segment:113", HASH, now=400)
    assert "secret" not in str(owner._retry_schedule)
    for wave in range(2, 51):
        row = schedule_retry(owner, "translate", "segment:113", error, HASH, now=wave * 400)
        assert row["rounds"] == wave
    finish_retry(owner, "translate", "segment:113", HASH)
    assert owner._retry_schedule == []


@pytest.mark.parametrize("error", [ValueError("429"), RuntimeError("timeout"),
    OpenCodeRequestError("auth", code="provider_authentication", retryable=True), asyncio.CancelledError()])
def test_unknown_deterministic_and_cancelled_errors_do_not_auto_retry(error):
    owner = session()
    assert transient_failure(error) is None
    assert schedule_retry(owner, "translate", "s:1", error, HASH, now=0) is None
    assert owner._retry_schedule == []


def test_typed_nested_timeout_and_retry_after():
    cause = OpenCodeTimeoutError("timeout")
    wrapper = RuntimeError("stage failed")
    wrapper.__cause__ = cause
    assert transient_failure(wrapper) == "provider_timeout"
    wrapper.retry_after = 600
    assert schedule_retry(session(), "review", "all", wrapper, HASH, now=2)["next_attempt_at"] == 602
    cause.__cause__ = wrapper  # Circular chains are bounded.
    assert transient_failure(wrapper) == "provider_timeout"


def test_nested_provider_retry_after_survives_semantic_wrapper():
    cause = OpenCodeRequestError("limited", code="provider_rate_limited", retryable=True)
    cause.retry_after = 900
    wrapper = RuntimeError("pacing request failed")
    wrapper.__cause__ = cause
    cause.__cause__ = wrapper
    record = schedule_retry(session(), "speech", "47", wrapper, HASH, now=100)
    assert record["next_attempt_at"] == 1000
    assert record["error_code"] == "provider_rate_limited"


@pytest.mark.parametrize("intent", ["user_stopped", "paused", "completed"])
def test_late_failure_cannot_restart_explicit_stop_pause_or_completion(intent):
    owner = session()
    mark_intent(owner, intent)
    schedule_retry(owner, "tts", "s:1", ConnectionError(), HASH, now=1)
    assert owner._restart_intent == intent
    assert not can_auto_resume(owner) and due_entries(owner, now=1000) == []


def test_edited_source_never_claims_or_clears_a_different_revision():
    owner = session()
    schedule_retry(owner, "translate", "s:1", TimeoutError(), HASH, now=0)
    other = "b" * 64
    assert not begin_retry(owner, "translate", "s:1", other, now=500)
    finish_retry(owner, "translate", "s:1", other)
    assert len(owner._retry_schedule) == 1
    row = schedule_retry(owner, "translate", "s:1", TimeoutError(), other, now=500)
    assert row["rounds"] == 1 and row["input_hash"] == other


def test_restart_preserves_pending_deadline_and_cools_interrupted_wave():
    owner = session()
    schedule_retry(owner, "translate", "s:1", TimeoutError(), HASH, now=0)
    schedule_retry(owner, "tts", "s:2", TimeoutError(), HASH, now=50)
    assert begin_retry(owner, "translate", "s:1", HASH, now=300)
    records = recover_schedule(owner._retry_schedule, now=400)
    assert records[0]["next_attempt_at"] == 700 and records[0]["error_code"] == "interrupted"
    assert records[1]["next_attempt_at"] == 350
    assert owner._retry_schedule[0]["state"] == "RUNNING"  # detached recovery


@pytest.mark.parametrize("saved_state, expected", [("STOPPED", "user_stopped"), ("RUNNING", "active"),
                                                   ("READY", "completed"), ("FAILED", "halted")])
def test_legacy_manifest_restart_intent_is_conservative(saved_state, expected):
    assert restart_intent({}, saved_state) == expected
    assert restart_intent({"_restart_intent": "shutdown"}, "STOPPED") == "shutdown"


@pytest.mark.parametrize("field, value", [("rounds", True), ("rounds", -1), ("next_attempt_at", 299),
    ("next_attempt_at", float("nan")), ("input_hash", "bad"), ("error_code", "success"),
    ("version", True), ("scope_id", "../../outside"), ("stage", "")])
def test_corrupt_retry_ledger_rejected(field, value):
    owner = session()
    schedule_retry(owner, "translate", "s:1", TimeoutError(), HASH, now=0)
    record = deepcopy(owner._retry_schedule)
    record[0][field] = value
    with pytest.raises(ValueError):
        validate_schedule(record)


def test_wait_is_cancelled_by_pause_without_starting_work():
    async def scenario():
        owner = session()
        schedule_retry(owner, "translate", "s:1", TimeoutError(), HASH)
        waiting = asyncio.create_task(wait_until_due(owner, poll_interval=.01))
        await asyncio.sleep(.02)
        mark_intent(owner, "paused")
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(waiting, 1)
        assert owner._retry_schedule[0]["state"] == "RETRY_PENDING"
    asyncio.run(scenario())
