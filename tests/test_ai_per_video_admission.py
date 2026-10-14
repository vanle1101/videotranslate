"""Deterministic scheduler/fault injection tests; no real provider invocations."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from core.ai_execution import AIExecutionLayer, AIExecutionPolicy, AIExecutionCancelledError
from core.engines.translation.opencode_client import _provider_failure
from core.engines.translation.opencode_client import OpenCodeZenClient, OpenCodeRequestError
from core.runtime_context import execution_context


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline, "scheduler did not reach expected state"
        time.sleep(.005)


@pytest.mark.parametrize("limit", [20, 30, 40, 50])
def test_two_videos_have_independent_caps(limit):
    layer = AIExecutionLayer(AIExecutionPolicy(concurrency=1, queue_timeout=10))
    release = threading.Event()
    for run in ("a", "b"):
        layer.set_run_concurrency(run, limit)
    def request(run, index):
        return layer.execute(lambda _: release.wait(8) and "valid",
                             identity={"run": run, "index": index}, run_id=run)
    with ThreadPoolExecutor(max_workers=2 * limit + 2) as pool:
        futures = [pool.submit(request, run, index) for run in ("a", "b") for index in range(limit + 1)]
        try:
            wait_for(lambda: layer.snapshot()["active_requests"] == 2 * limit)
            wait_for(lambda: layer.snapshot()["queued_requests"] == 2)
            for run in ("a", "b"):
                state = layer.snapshot(run)
                assert state["active_requests"] == state["selected_concurrency"] == limit
                assert state["queued_requests"] == 1 and state["admission_reason"] == "video_limit"
        finally:
            release.set()
        assert all(future.result(5) == "valid" for future in futures)
    assert layer.snapshot()["active_requests"] == 0


def test_duplicate_key_does_not_block_independent_work():
    layer = AIExecutionLayer(AIExecutionPolicy(concurrency=2))
    release, independent = threading.Event(), threading.Event()
    def request(key):
        def operation(_):
            if key == "duplicate":
                assert release.wait(5)
            else:
                independent.set()
            return "valid"
        return layer.execute(operation, identity={"key": key}, run_id="a")
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(request, "duplicate")
        wait_for(lambda: layer.snapshot()["active_requests"] == 1)
        duplicate = pool.submit(request, "duplicate")
        wait_for(lambda: layer.snapshot()["queued_requests"] == 1)
        other = pool.submit(request, "different")
        try:
            assert independent.wait(2), "same-key waiter blocked unrelated request"
        finally:
            release.set()
        assert [f.result(5) for f in (first, duplicate, other)] == ["valid"] * 3


def test_queued_video_cancellation_does_not_disturb_other_video():
    layer = AIExecutionLayer()
    release, cancel = threading.Event(), threading.Event()
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(layer.execute, lambda _: release.wait(5) and "valid", identity={"key": 1}, run_id="a")
        wait_for(lambda: layer.snapshot("a")["active_requests"] == 1)
        queued = pool.submit(layer.execute, lambda _: pytest.fail("cancelled request executed"),
                             identity={"key": 2}, run_id="a", cancel_check=cancel.is_set)
        wait_for(lambda: layer.snapshot("a")["queued_requests"] == 1)
        try:
            other = pool.submit(layer.execute, lambda _: "valid", identity={"key": 3}, run_id="b")
            assert other.result(2) == "valid"
            cancel.set()
            with pytest.raises(AIExecutionCancelledError):
                queued.result(2)
            assert layer.snapshot("a")["queued_requests"] == 0
        finally:
            release.set()
        assert first.result(5) == "valid"


def test_six_attempt_transport_wave_and_retry_after():
    layer = AIExecutionLayer(AIExecutionPolicy(retry_base=2, retry_cap=60, retry_jitter=0, circuit_failures=99))
    attempts, delays = [], []
    error = _provider_failure('HTTP 429 {"retry-after":"75"}')
    def operation(attempt):
        attempts.append(attempt)
        raise error
    with pytest.raises(type(error)):
        layer.execute(operation, identity={"key": 1}, run_id="a", max_retries=20,
                      retryable=lambda e: e.retryable, wait_retry=delays.append)
    assert attempts == [1, 2, 3, 4, 5, 6]
    assert delays == [75] * 5
    assert layer.snapshot("a")["retry_requests"] == 5


def test_fair_round_robin_and_saturated_video_skipped():
    layer = AIExecutionLayer()
    a, b, c = object(), object(), object()
    layer._queue.extend([(a, "a", "a1"), (b, "b", "b1"), (c, "a", "a2")])
    layer._last_admitted_run = "a"
    assert layer._next_ticket() is b
    layer._run_active["b"] = 1
    assert layer._next_ticket() is a


@pytest.mark.parametrize("value, expected", [("3", 3), ("0", 0), ("nan", None), ("garbage", None)])
def test_retry_after_is_validated(value, expected):
    assert _provider_failure(f'HTTP 503 Retry-After: {value}').retry_after == expected


def test_unknown_cli_error_is_not_assumed_transient():
    assert not _provider_failure("unspecified CLI failure").retryable


def test_registered_video_owns_six_transport_attempts_even_with_legacy_client_retry_setting(monkeypatch):
    layer = AIExecutionLayer(AIExecutionPolicy(retry_base=2, retry_cap=60, retry_jitter=0))
    layer.set_run_concurrency("saved-video", 20)
    client = OpenCodeZenClient(api_key="fixture", max_retries=1, execution_layer=layer)
    calls, delays = [], []
    def failed(*args):
        calls.append(1)
        raise _provider_failure("HTTP 503")
    monkeypatch.setattr(client, "_translate", failed)
    monkeypatch.setattr("core.engines.translation.opencode_client._retry_delay", lambda delay, context: delays.append(delay))
    with execution_context("saved-video"), pytest.raises(OpenCodeRequestError):
        client.translate("fixture input")
    assert len(calls) == 6 and delays == [2, 4, 8, 16, 32]
    assert layer.snapshot("saved-video")["requests"] == 6
    assert layer.snapshot()["consecutive_failures"] == 1
