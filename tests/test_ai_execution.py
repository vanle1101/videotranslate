"""Fault injection for real admission/retry/cache logic, with no network calls."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from core.ai_execution import (
    AICircuitOpenError, AIExecutionCancelledError, AIExecutionLayer,
    AIExecutionPolicy, AIQueueFullError, AIQueueTimeoutError,
    ValidatedResponseCache, response_cache_key,
)


def valid(raw):
    data = json.loads(raw)
    if set(data) != {"text"} or not isinstance(data["text"], str) or not data["text"].strip():
        raise ValueError("Invalid schema")
    return data


IDENTITY = {"provider": "opencode", "model": "test-model", "prompt": "real source",
            "system": "translate", "schema": "translation-v1", "config": {"max_tokens": 100}}


def test_cache_key_exact_and_order_independent():
    assert response_cache_key(IDENTITY) == response_cache_key(dict(reversed(list(IDENTITY.items()))))
    for field in ("provider", "model", "prompt", "system", "schema", "config"):
        assert response_cache_key(IDENTITY) != response_cache_key({**IDENTITY, field: "changed"})


def test_persistent_valid_cache_skips_provider_after_restart(tmp_path):
    layer = AIExecutionLayer(cache_root=tmp_path)
    result = layer.execute(lambda _: '{"text":"Xin chào"}', identity=IDENTITY, validate=valid)
    assert result == '{"text":"Xin chào"}'
    restarted = AIExecutionLayer(cache_root=tmp_path)
    assert restarted.execute(lambda _: pytest.fail("No duplicate request"), identity=IDENTITY, validate=valid) == result
    assert restarted.snapshot()["requests"] == 0 and restarted.snapshot()["cache_hits"] == 1
    stored = next(tmp_path.glob("*.json")).read_text(encoding="utf-8")
    assert "real source" not in stored and "test-model" not in stored


@pytest.mark.parametrize("result", ["", "not-json", '{"text":null}', '{"wrong":"value"}'])
def test_invalid_output_is_never_persisted_or_reported_success(tmp_path, result):
    layer = AIExecutionLayer(cache_root=tmp_path)
    with pytest.raises(ValueError):
        layer.execute(lambda _: result, identity=IDENTITY, validate=valid)
    assert not list(tmp_path.glob("*.json"))
    assert layer.snapshot()["active_requests"] == 0


def test_without_explicit_schema_validation_transport_text_is_not_cached(tmp_path):
    layer = AIExecutionLayer(cache_root=tmp_path)
    calls = []
    for _ in range(2):
        assert layer.execute(lambda _: calls.append(1) or "raw unvalidated", identity=IDENTITY) == "raw unvalidated"
    assert calls == [1, 1] and not list(tmp_path.glob("*.json"))


def test_force_rerun_and_changed_identity_bypass_cache(tmp_path):
    layer = AIExecutionLayer(cache_root=tmp_path)
    calls = []
    operation = lambda _: calls.append(1) or '{"text":"valid"}'
    for identity, cache in ((IDENTITY, True), (IDENTITY, False), ({**IDENTITY, "prompt": "changed"}, True)):
        layer.execute(operation, identity=identity, validate=valid, use_cache=cache)
    assert len(calls) == 3


@pytest.mark.parametrize("damage", ["corrupt", "expired", "wrong-version", "wrong-key", "invalid-response"])
def test_corrupt_or_stale_cached_result_is_revalidated_and_replaced(tmp_path, damage):
    policy = AIExecutionPolicy()
    cache = ValidatedResponseCache(tmp_path, policy)
    key = response_cache_key(IDENTITY)
    cache.put(key, '{"text":"old"}', valid)
    path = tmp_path / (key + ".json")
    data = json.loads(path.read_text(encoding="utf-8"))
    if damage == "corrupt":
        path.write_text("{broken", encoding="utf-8")
    else:
        if damage == "expired": data["created"] -= policy.cache_ttl + 1
        elif damage == "wrong-version": data["version"] = -1
        elif damage == "wrong-key": data["key"] = "different"
        else: data["response"] = '{"text":false}'
        path.write_text(json.dumps(data), encoding="utf-8")
    layer = AIExecutionLayer(cache_root=tmp_path)
    assert layer.execute(lambda _: '{"text":"new"}', identity=IDENTITY, validate=valid) == '{"text":"new"}'
    assert layer.snapshot()["requests"] == 1


def test_cache_is_bounded_and_preserves_unowned_files(tmp_path):
    cache = ValidatedResponseCache(tmp_path, AIExecutionPolicy(cache_entries=2))
    user = tmp_path / "user.json"
    user.write_text("keep")
    keys = [response_cache_key({"id": i}) for i in range(4)]
    for key in keys:
        assert cache.put(key, '{"text":"okay"}', valid)
        time.sleep(.01)
    assert len(list(tmp_path.glob("*.json"))) == 3 and user.read_text() == "keep"
    assert cache.get(keys[-1], valid) is not None and cache.get(keys[0], valid) is None


def test_disk_failure_preserves_actual_success(tmp_path, monkeypatch, caplog):
    layer = AIExecutionLayer(cache_root=tmp_path)
    monkeypatch.setattr(Path, "replace", lambda *args: (_ for _ in ()).throw(OSError("disk full secret")))
    assert layer.execute(lambda _: '{"text":"real"}', identity=IDENTITY, validate=valid) == '{"text":"real"}'
    assert not list(tmp_path.iterdir()) and "secret" not in caplog.text


@pytest.mark.parametrize("concurrency", [1, 2, 3])
def test_concurrency_fifo_and_single_flight_validated_cache(tmp_path, concurrency):
    layer = AIExecutionLayer(AIExecutionPolicy(queue_timeout=2, concurrency=concurrency), cache_root=tmp_path)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def work(_):
        calls.append(1)
        entered.set()
        assert release.wait(2)
        return '{"text":"one real request"}'
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(layer.execute, work, identity=IDENTITY, validate=valid)
        assert entered.wait(1)
        second = pool.submit(layer.execute, work, identity=IDENTITY, validate=valid)
        third = pool.submit(layer.execute, work, identity=IDENTITY, validate=valid)
        deadline = time.monotonic() + 1
        while layer.snapshot()["queued_requests"] < 2 and time.monotonic() < deadline:
            time.sleep(.01)
        assert layer.snapshot()["active_requests"] == 1
        assert layer.snapshot()["queued_requests"] == 2
        release.set()
        assert first.result() == second.result() == third.result()
    assert calls == [1] and layer.snapshot()["cache_hits"] == 2


def test_concurrency_limit_multiple_independent_requests():
    layer = AIExecutionLayer(AIExecutionPolicy(concurrency=2, queue_timeout=2))
    barrier = threading.Barrier(2)
    release = threading.Event()
    peaks = []
    def work(_):
        peaks.append(layer.snapshot()["active_requests"])
        barrier.wait(1)
        assert release.wait(2)
        return "real output"
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(layer.execute, work, identity={"id": i}) for i in range(2)]
        deadline = time.monotonic() + 1
        while len(peaks) < 2 and time.monotonic() < deadline: time.sleep(.01)
        release.set()
        assert [job.result() for job in jobs] == ["real output"] * 2
    assert max(peaks) == 2 and layer.snapshot()["active_requests"] == 0


def test_queue_timeout_full_and_cancellation_do_not_start_provider():
    layer = AIExecutionLayer(AIExecutionPolicy(queue_limit=1, queue_timeout=.2))
    entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    def long(_):
        entered.set()
        assert release.wait(2)
        return "real"
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(layer.execute, long, identity={"id": 1})
        assert entered.wait(1)
        queued = pool.submit(layer.execute, lambda _: pytest.fail("Queued provider must not start"),
                             identity={"id": 2}, cancel_check=cancelled.is_set)
        deadline = time.monotonic() + 1
        while layer.snapshot()["queued_requests"] < 1 and time.monotonic() < deadline: time.sleep(.01)
        with pytest.raises(AIQueueFullError):
            layer.execute(lambda _: pytest.fail("Full queue"), identity={"id": 3})
        cancelled.set()
        with pytest.raises(AIExecutionCancelledError): queued.result(timeout=1)
        with pytest.raises(AIQueueTimeoutError):
            layer.execute(lambda _: pytest.fail("Expired queue"), identity={"id": 4})
        release.set()
        assert first.result() == "real"
    assert layer.snapshot()["queued_requests"] == layer.snapshot()["active_requests"] == 0


def test_retry_backoff_jitter_is_bounded_and_failures_not_cached(tmp_path):
    policy = AIExecutionPolicy(retry_jitter=.25)
    layer = AIExecutionLayer(policy, cache_root=tmp_path)
    attempts, waits = [], []
    def work(attempt):
        attempts.append(attempt)
        if attempt < 3: raise TimeoutError("private prompt")
        return '{"text":"recovered"}'
    assert layer.execute(work, identity=IDENTITY, validate=valid, max_retries=2,
                         retryable=lambda error: isinstance(error, TimeoutError), wait_retry=waits.append) == '{"text":"recovered"}'
    assert attempts == [1, 2, 3] and .75 <= waits[0] <= 1.25 and 1.5 <= waits[1] <= 2.5
    assert layer.snapshot()["retry_requests"] == 2 and layer.snapshot()["consecutive_failures"] == 0


def test_permanent_error_is_not_retried_or_counted_as_outage():
    layer = AIExecutionLayer()
    with pytest.raises(ValueError):
        layer.execute(lambda _: (_ for _ in ()).throw(ValueError("auth refused")),
                      identity=IDENTITY, max_retries=2, retryable=lambda error: isinstance(error, TimeoutError))
    assert layer.snapshot()["requests"] == 1 and layer.snapshot()["consecutive_failures"] == 0


def test_circuit_opens_and_only_one_half_open_probe_recovers():
    layer = AIExecutionLayer(AIExecutionPolicy(circuit_failures=2, circuit_cooldown=.1))
    for _ in range(2):
        with pytest.raises(TimeoutError):
            layer.execute(lambda _: (_ for _ in ()).throw(TimeoutError()), identity=IDENTITY,
                          retryable=lambda error: isinstance(error, TimeoutError))
    with pytest.raises(AICircuitOpenError) as caught:
        layer.execute(lambda _: pytest.fail("Circuit must prevent request"), identity=IDENTITY)
    assert 0 < caught.value.retry_after <= .101
    time.sleep(.12)
    assert layer.execute(lambda _: "actual recovery", identity=IDENTITY) == "actual recovery"
    assert layer.snapshot()["consecutive_failures"] == 0
    assert layer.snapshot()["circuit_retry_after"] == 0


def test_circuit_opens_inside_retry_without_stalling_for_cooldown():
    layer = AIExecutionLayer(AIExecutionPolicy(circuit_failures=1, circuit_cooldown=30))
    started = time.monotonic()
    with pytest.raises(AICircuitOpenError):
        layer.execute(lambda _: (_ for _ in ()).throw(TimeoutError()), identity=IDENTITY,
                      max_retries=2, retryable=lambda error: isinstance(error, TimeoutError))
    assert time.monotonic() - started < 1 and layer.snapshot()["requests"] == 1


def test_cancelled_completion_is_not_published_or_cached(tmp_path):
    cancelled = threading.Event()
    layer = AIExecutionLayer(cache_root=tmp_path)
    def complete(_):
        cancelled.set()
        return '{"text":"late"}'
    with pytest.raises(AIExecutionCancelledError):
        layer.execute(complete, identity=IDENTITY, validate=valid, cancel_check=cancelled.is_set)
    assert not list(tmp_path.glob("*.json")) and layer.snapshot()["active_requests"] == 0


def test_cancel_backoff_does_not_retry():
    layer = AIExecutionLayer()
    cancelled = threading.Event()
    with pytest.raises(AIExecutionCancelledError):
        layer.execute(lambda _: (_ for _ in ()).throw(TimeoutError()), identity=IDENTITY,
                      max_retries=2, retryable=lambda error: isinstance(error, TimeoutError),
                      wait_retry=lambda _: cancelled.set(), cancel_check=cancelled.is_set)
    assert layer.snapshot()["requests"] == 1 and layer.snapshot()["active_requests"] == 0
