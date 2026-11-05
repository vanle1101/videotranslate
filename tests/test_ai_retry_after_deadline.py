"""Provider deadlines survive breaker transitions and older in-flight results."""
import threading

import pytest

from core import ai_execution
from core.ai_execution import (
    AIExecutionLayer, AIExecutionPolicy, AICircuitOpenError, AIExecutionCancelledError,
)


class RateLimited(Exception):
    retry_after = 900.0


@pytest.fixture
def clock(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(ai_execution.time, "monotonic", lambda: now[0])
    return now


def rate_limited(_):
    raise RateLimited("429")


def test_breaker_preserves_long_server_deadline_for_owner_and_siblings(clock):
    layer = AIExecutionLayer(AIExecutionPolicy(circuit_failures=1, circuit_cooldown=30))
    with pytest.raises(AICircuitOpenError) as caught:
        layer.execute(rate_limited, identity={"request": "first"}, max_retries=5,
                      retryable=lambda error: isinstance(error, RateLimited))
    assert caught.value.retry_after == 900
    assert layer.snapshot()["active_requests"] == 0
    clock[0] += 300  # Durable task cooldown alone must not admit another request.
    with pytest.raises(AICircuitOpenError) as sibling:
        layer.execute(lambda _: pytest.fail("Request before server deadline"),
                      identity={"request": "sibling"})
    assert sibling.value.retry_after == 600
    assert layer.snapshot()["requests"] == 1
    assert layer.snapshot()["queued_requests"] == 0
    clock[0] += 600
    assert layer.execute(lambda _: "recovered", identity={"request": "sibling"}) == "recovered"
    assert layer.snapshot()["circuit_retry_after"] == 0


@pytest.mark.parametrize("max_retries", [0, 5])
def test_older_inflight_success_cannot_clear_server_deadline(clock, max_retries):
    layer = AIExecutionLayer(AIExecutionPolicy(concurrency=2, circuit_failures=1))

    def older_operation(_):
        # A second admitted request receives 429 before this older one finishes.
        with pytest.raises(RateLimited if max_retries == 0 else AICircuitOpenError):
            layer.execute(rate_limited, identity={"request": "newer"}, max_retries=max_retries,
                          retryable=lambda error: isinstance(error, RateLimited))
        return "older successful response"

    assert layer.execute(older_operation, identity={"request": "older"}) == "older successful response"
    assert layer.snapshot()["consecutive_failures"] == 0
    assert layer.snapshot()["circuit_retry_after"] == 900
    with pytest.raises(AICircuitOpenError):
        layer.execute(lambda _: pytest.fail("Older success must not revoke Retry-After"), identity={"request": "next"})
    assert layer.snapshot()["active_requests"] == layer.snapshot()["queued_requests"] == 0


def test_bounded_owner_waits_server_delay_and_recovers_without_open_breaker(clock):
    layer = AIExecutionLayer(AIExecutionPolicy(circuit_failures=4))
    waits = []

    def wait(delay):
        waits.append(delay)
        with pytest.raises(AICircuitOpenError):
            layer.execute(lambda _: pytest.fail("Sibling during server wait"), identity={"request": "sibling"})
        clock[0] += delay

    def operation(attempt):
        if attempt == 1:
            raise RateLimited("429")
        return "success"

    assert layer.execute(operation, identity={"request": "owner"}, max_retries=5,
                         retryable=lambda error: isinstance(error, RateLimited), wait_retry=wait) == "success"
    assert waits == [900]
    assert layer.snapshot()["requests"] == 2
    assert layer.snapshot()["active_requests"] == layer.snapshot()["queued_requests"] == 0


def test_cancel_long_backoff_releases_owner_without_erasing_server_deadline(clock):
    layer = AIExecutionLayer(AIExecutionPolicy(circuit_failures=4))
    cancelled = threading.Event()
    with pytest.raises(AIExecutionCancelledError):
        layer.execute(rate_limited, identity={"request": "owner"}, max_retries=5,
                      retryable=lambda error: isinstance(error, RateLimited),
                      wait_retry=lambda _: cancelled.set(), cancel_check=cancelled.is_set)
    assert layer.snapshot()["requests"] == 1
    assert layer.snapshot()["active_requests"] == layer.snapshot()["queued_requests"] == 0
    assert layer.snapshot()["circuit_retry_after"] == 900
    with pytest.raises(AIExecutionCancelledError):
        layer.execute(lambda _: pytest.fail("Cancelled admission"), identity={"request": "cancelled"},
                      cancel_check=cancelled.is_set)
    with pytest.raises(AICircuitOpenError):
        layer.execute(lambda _: pytest.fail("Uncancelled sibling before deadline"), identity={"request": "sibling"})


def test_admitted_owner_respects_newer_provider_deadline(clock):
    layer = AIExecutionLayer(AIExecutionPolicy(circuit_failures=6))
    attempts = []

    def operation(attempt):
        attempts.append(attempt)
        error = TimeoutError("transient")
        error.retry_after = 10
        raise error

    def wait(delay):
        clock[0] += delay / 2
        # A request already in flight receives a longer provider wait.
        layer._failure(retry_after=900)
        clock[0] += delay / 2

    with pytest.raises(AICircuitOpenError) as caught:
        layer.execute(operation, identity={"request": "owner"}, max_retries=5,
                      retryable=lambda error: isinstance(error, TimeoutError), wait_retry=wait)
    assert attempts == [1]
    assert caught.value.retry_after == 895
    assert layer.snapshot()["active_requests"] == 0
