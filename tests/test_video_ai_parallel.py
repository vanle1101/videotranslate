"""Real threads, mocked provider work: project caps are independent."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import pytest

from core.ai_execution import AIExecutionLayer, AIExecutionPolicy
from core.translation_batching import sentence_batches, neighbor_context


@pytest.mark.parametrize("selected", [20, 30, 40, 50])
def test_each_video_reaches_selected_capacity_without_global_cap(selected):
    layer = AIExecutionLayer(AIExecutionPolicy(concurrency=5, queue_limit=128))
    barrier = threading.Barrier(selected * 2)
    def work(scope, index):
        def provider(attempt):
            barrier.wait(timeout=10)
            return "validated"
        return layer.execute(provider, identity={"scope": scope, "id": index},
            scope_id=scope, concurrency_limit=selected)
    with ThreadPoolExecutor(max_workers=selected * 2) as pool:
        jobs = [pool.submit(work, scope, i) for scope in ("video-a", "video-b") for i in range(selected)]
        assert [job.result(timeout=15) for job in jobs] == ["validated"] * (selected * 2)
    assert layer.snapshot()["active_requests"] == 0


def test_six_attempts_and_retry_after_without_nested_retry(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("core.ai_execution.time.monotonic", lambda: now[0])
    layer = AIExecutionLayer(AIExecutionPolicy(circuit_failures=6, retry_base=2, retry_cap=60, retry_jitter=0))
    calls, waits = [], []
    def wait(delay):
        waits.append(delay)
        now[0] += delay
    def provider(attempt):
        calls.append(attempt)
        error = TimeoutError("transient")
        error.retry_after = 10
        raise error
    with pytest.raises(TimeoutError):
        layer.execute(provider, identity={"id": 1}, max_retries=5,
            retryable=lambda error: isinstance(error, TimeoutError), wait_retry=wait)
    assert calls == [1, 2, 3, 4, 5, 6]
    assert waits == [10, 10, 10, 16, 32]


def test_sentence_batches_keep_stable_ids_and_three_neighbors():
    rows = [{"id": i, "start": i, "end": i + 1} for i in range(25)]
    batches = sentence_batches(list(reversed(rows)))
    assert list(map(len, batches)) == [8, 8, 8, 1]
    assert [row["id"] for row in neighbor_context(rows, batches[1])] == list(range(5, 19))
    with pytest.raises(ValueError, match="Duplicate"):
        sentence_batches([rows[0], rows[0]])


def test_paused_queue_does_not_start_or_expire_and_other_video_runs():
    layer = AIExecutionLayer(AIExecutionPolicy(concurrency=1, queue_timeout=.05))
    resumed = threading.Event()
    started = threading.Event()
    def provider(_):
        started.set()
        return "resumed"
    with ThreadPoolExecutor(max_workers=2) as pool:
        paused = pool.submit(layer.execute, provider, identity={"id": "paused"},
            scope_id="a", admission_check=resumed.is_set)
        time.sleep(.15)
        assert not started.is_set() and not paused.done()
        assert layer.execute(lambda _: "other video", identity={"id": "other"}, scope_id="b") == "other video"
        resumed.set()
        assert paused.result(timeout=1) == "resumed"


def test_stop_cancels_paused_admission_without_provider_call():
    from core.ai_execution import AIExecutionCancelledError
    layer = AIExecutionLayer()
    stopped = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(layer.execute, lambda _: pytest.fail("paused request launched"),
            identity={"id": "paused-stop"}, admission_check=lambda: False, cancel_check=stopped.is_set)
        time.sleep(.02)
        stopped.set()
        with pytest.raises(AIExecutionCancelledError):
            pending.result(timeout=1)
    assert layer.snapshot()["queued_requests"] == 0


def test_retry_pauses_after_backoff_until_resume():
    layer = AIExecutionLayer(AIExecutionPolicy(circuit_failures=6))
    resumed, backoff = threading.Event(), threading.Event()
    resumed.set()
    attempts = []
    def provider(attempt):
        attempts.append(attempt)
        if attempt == 1:
            raise TimeoutError()
        return "done"
    def wait(_):
        resumed.clear()
        backoff.set()
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(layer.execute, provider, identity={"id": "retry-pause"},
            max_retries=5, retryable=lambda error: isinstance(error, TimeoutError),
            wait_retry=wait, admission_check=resumed.is_set)
        assert backoff.wait(1)
        time.sleep(.15)
        assert attempts == [1]
        resumed.set()
        assert result.result(timeout=1) == "done"
    assert attempts == [1, 2]
