"""Worker admission regressions; no application process, media or providers."""
from concurrent.futures import ThreadPoolExecutor
import asyncio
from types import SimpleNamespace
import threading
import time

import psutil
import pytest

from config import settings
from core.ai_execution import AIExecutionLayer, AIExecutionPolicy
from core.engines.translation import opencode_client as oc
from core.streaming.pipeline import StreamingPipelineSession
from core.streaming.parallel_chunks import ChunkWork, OrderedChunkRunner
from core.runtime_context import current_execution_context


def wait_queued(layer, expected):
    deadline = time.monotonic() + 3
    while layer.snapshot()["queued_requests"] != expected:
        if time.monotonic() >= deadline:
            pytest.fail("isolated callers did not enter the provider queue")
        time.sleep(.005)


def test_worker_preset_preserves_existing_clients_queue_cache_and_circuit(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(settings, "OPENCODE_CONCURRENCY", 5)
    monkeypatch.setattr(oc, "_execution_layers", {})
    old = oc.OpenCodeZenClient(api_key="isolated-key")
    layer = old.execution_layer
    cache = layer.cache
    layer._failures = 2
    layer._open_until = time.monotonic() + 30
    monkeypatch.setattr(settings, "OPENCODE_CONCURRENCY", 20)
    new = oc.OpenCodeZenClient(api_key="isolated-key")
    assert old.execution_layer is new.execution_layer is layer
    assert layer.policy.concurrency == layer.snapshot()["concurrency"] == 20
    assert layer.cache is cache
    assert layer.snapshot()["consecutive_failures"] == 2
    assert layer.snapshot()["circuit_retry_after"] > 0
    assert len(oc._execution_layers) == 1


def test_idle_resume_client_refreshes_saved_worker_preset_before_request(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(settings, "OPENCODE_CONCURRENCY", 5)
    monkeypatch.setattr(oc, "_execution_layers", {})
    old = oc.OpenCodeZenClient(api_key="isolated-key")
    layer = old.execution_layer
    monkeypatch.setattr(settings, "OPENCODE_CONCURRENCY", 20)
    observed = []
    def response(*args, **kwargs):
        observed.append(layer.snapshot()["concurrency"])
        return "isolated translation result"
    monkeypatch.setattr(old, "_translate", response)
    assert old.translate("isolated request", use_cache=False) == "isolated translation result"
    assert observed == [20] and old.execution_layer is layer
    assert len(oc._execution_layers) == 1


def test_explicit_execution_layer_keeps_its_own_test_policy(monkeypatch):
    isolated = AIExecutionLayer(AIExecutionPolicy(concurrency=1))
    client = oc.OpenCodeZenClient(api_key="isolated-key", execution_layer=isolated)
    monkeypatch.setattr(settings, "OPENCODE_CONCURRENCY", 20)
    monkeypatch.setattr(oc, "_shared_execution_layer", lambda: pytest.fail("explicit layer must remain isolated"))
    monkeypatch.setattr(client, "_translate", lambda *args: "isolated translation result")
    assert client.translate("isolated request", use_cache=False) == "isolated translation result"
    assert client.execution_layer is isolated and isolated.policy.concurrency == 1


@pytest.mark.parametrize("selected", [1, 3, 5, 10, 15, 20, 30, 40, 50])
def test_requested_capacity_is_not_capped_at_eight_on_sufficient_resources(monkeypatch, selected):
    monkeypatch.setattr(settings, "TRANSLATION_CHUNK_WORKERS", selected)
    monkeypatch.setattr(settings, "OPENCODE_CONCURRENCY", selected)
    monkeypatch.setattr("core.streaming.pipeline.os.cpu_count", lambda: 48)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(available=64 * 1024**3))
    assert StreamingPipelineSession._parallel_chunk_capacity(SimpleNamespace(ai_concurrency=selected)) == selected


@pytest.mark.parametrize("cpus,available_gib,provider,expected", [(8, 64, 20, 20), (48, 5, 20, 20), (48, 64, 5, 20), (1, 1, 20, 20)])
def test_ai_capacity_is_separate_from_native_resource_and_global_default_limits(monkeypatch, cpus, available_gib, provider, expected):
    monkeypatch.setattr(settings, "TRANSLATION_CHUNK_WORKERS", 20)
    monkeypatch.setattr(settings, "OPENCODE_CONCURRENCY", provider)
    monkeypatch.setattr("core.streaming.pipeline.os.cpu_count", lambda: cpus)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(available=available_gib * 1024**3))
    assert StreamingPipelineSession._parallel_chunk_capacity(SimpleNamespace(ai_concurrency=20)) == expected


def test_twenty_isolated_requests_can_be_admitted_together_without_duplicate_calls():
    layer = AIExecutionLayer(AIExecutionPolicy(concurrency=5, queue_timeout=5))
    layer.set_concurrency(20)
    gates = [threading.Event() for _ in range(20)]
    release = threading.Event()
    seen = []
    lock = threading.Lock()
    def call(index):
        def operation(attempt):
            with lock:
                seen.append(index)
            gates[index].set()
            assert release.wait(5)
            return str(index)
        return layer.execute(operation, identity={"request": index}, use_cache=False)
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(call, index) for index in range(20)]
        try:
            assert all(gate.wait(4) for gate in gates)
            assert layer.snapshot()["active_requests"] == 20
            assert sorted(seen) == list(range(20))
        finally:
            release.set()
        assert [future.result(timeout=5) for future in futures] == [str(index) for index in range(20)]
    assert layer.snapshot()["active_requests"] == layer.snapshot()["queued_requests"] == 0


def test_selected_twenty_reaches_pipeline_executor_and_ordered_chunk_runner(monkeypatch):
    """Exercise the real blocking bridge, not a separate test-owned thread pool."""
    monkeypatch.setattr(settings, "TRANSLATION_CHUNK_WORKERS", 20)
    monkeypatch.setattr(settings, "OPENCODE_CONCURRENCY", 20)
    monkeypatch.setattr("core.streaming.pipeline.os.cpu_count", lambda: 48)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(available=64 * 1024**3))
    session = SimpleNamespace(task_id="isolated-worker-preset", is_stopped=False, ai_concurrency=20)
    limit = StreamingPipelineSession._parallel_chunk_capacity(session)
    layer = AIExecutionLayer(AIExecutionPolicy(concurrency=limit, queue_timeout=5))
    release, entered = threading.Event(), threading.Event()
    lock, seen, threads = threading.Lock(), [], set()
    observed = []

    def native(index):
        def operation(attempt):
            with lock:
                seen.append(index)
                threads.add(threading.current_thread())
                observed.append(current_execution_context().run_id)
                if len(seen) == limit:
                    entered.set()
            assert release.wait(5)
            return index
        # Queue results are text in production; ownership IDs remain outside it.
        layer.execute(lambda attempt: str(operation(attempt)),
                      identity={"request": index}, use_cache=False)
        return index

    async def scenario():
        async with OrderedChunkRunner(limit) as runner:
            work = [ChunkWork(i, i + 1, (i,),
                    lambda i=i: StreamingPipelineSession._run_blocking(session, native, i))
                    for i in range(23)]
            async def collect():
                outputs = []
                async for outcome in runner.run(work):
                    assert outcome.error is None
                    outputs.append(outcome.value)
                return outputs
            consumer = asyncio.create_task(collect())
            try:
                deadline = time.monotonic() + 4
                while not entered.is_set() and not consumer.done() and time.monotonic() < deadline:
                    await asyncio.sleep(.005)
                assert entered.is_set(), "Pipeline executor must admit all twenty owners"
                assert sorted(seen) == list(range(20))
                assert layer.snapshot()["active_requests"] == 20
            finally:
                release.set()
                outputs = await asyncio.wait_for(consumer, 5)
            assert outputs == list(range(23))

    asyncio.run(scenario())
    assert sorted(seen) == list(range(23))
    assert observed == [session.task_id] * 23
    assert layer.snapshot()["active_requests"] == layer.snapshot()["queued_requests"] == 0
    assert all(not thread.is_alive() for thread in threads)


def test_lowering_concurrency_drains_existing_requests_before_new_admission():
    layer = AIExecutionLayer(AIExecutionPolicy(concurrency=3, queue_timeout=5))
    entered = [threading.Event() for _ in range(4)]
    release = [threading.Event() for _ in range(4)]
    def call(index):
        def operation(attempt):
            entered[index].set()
            assert release[index].wait(5)
            return str(index)
        return layer.execute(operation, identity={"request": index}, use_cache=False)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(call, index) for index in range(3)]
        try:
            assert all(event.wait(3) for event in entered[:3])
            layer.set_concurrency(1)
            futures.append(pool.submit(call, 3))
            wait_queued(layer, 1)
            release[0].set()
            assert futures[0].result(timeout=3) == "0"
            release[1].set()
            assert futures[1].result(timeout=3) == "1"
            assert not entered[3].wait(.05)
            assert layer.snapshot()["active_requests"] == 1
            release[2].set()
            assert futures[2].result(timeout=3) == "2"
            assert entered[3].wait(3)
        finally:
            for event in release:
                event.set()
        assert [future.result(timeout=3) for future in futures] == [str(index) for index in range(4)]
    assert layer.snapshot()["active_requests"] == layer.snapshot()["queued_requests"] == 0


def test_increasing_concurrency_wakes_waiting_callers_in_the_same_queue():
    layer = AIExecutionLayer(AIExecutionPolicy(concurrency=1, queue_timeout=5))
    entered = [threading.Event() for _ in range(2)]
    release = threading.Event()
    def call(index):
        def operation(attempt):
            entered[index].set()
            assert release.wait(5)
            return str(index)
        return layer.execute(operation, identity={"request": index}, use_cache=False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(call, 0)
        try:
            assert entered[0].wait(3)
            second = pool.submit(call, 1)
            wait_queued(layer, 1)
            layer.set_concurrency(20)
            assert entered[1].wait(3)
            assert layer.snapshot()["active_requests"] == 2
        finally:
            release.set()
        assert first.result(timeout=3) == "0" and second.result(timeout=3) == "1"


@pytest.mark.parametrize("invalid", [True, False, 0, -1, "20", 20.0, None])
def test_bad_concurrency_update_preserves_admission_policy(invalid):
    layer = AIExecutionLayer(AIExecutionPolicy(concurrency=5))
    with pytest.raises(ValueError):
        layer.set_concurrency(invalid)
    assert layer.policy.concurrency == 5
