"""Offline RAM admission: measured budgets, bounded waiting and ownership."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.cli_resources import CLIResourceAdmission, ResourceWaitTimeout, MIB


def gate(memory=lambda: (1000, 800), rss=lambda process: 0):
    return CLIResourceAdmission(memory=memory, rss=rss, process_bytes=200,
                                reserve_bytes=100, reserve_fraction=.1, poll_interval=.01)


def test_startup_reservations_prevent_concurrent_overcommit():
    admission = gate()
    release = threading.Event()
    lock, admitted = threading.Lock(), []
    def worker(index):
        with admission.admit(timeout=.5):
            with lock:
                admitted.append(index)
            release.wait(.3)
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(worker, i) for i in range(6)]
        deadline = time.monotonic() + .15
        while len(admitted) < 3 and time.monotonic() < deadline:
            time.sleep(.005)
        assert len(admitted) == 3  # (800 free - 100 headroom) // 200 reserved
        release.set()
        for f in futures:
            f.result()
    assert len(admitted) == 6
    assert not admission._leases and not admission._queue


def test_resident_memory_not_double_counted_and_peak_updates_estimate():
    available = [800]
    admission = gate(memory=lambda: (1000, available[0]), rss=lambda p: p or 0)
    with admission.admit() as lease:
        lease.process = 200
        available[0] = 600  # OS has already deducted this child's RSS.
        with admission.admit() as second:
            assert second.estimate == 230  # Observed 200 plus 15% margin.
            assert len(admission._leases) == 2
    assert not admission._leases


def test_large_memory_allows_more_than_five_real_transport_reservations():
    from contextlib import ExitStack
    admission = gate(memory=lambda: (10000, 9000))
    with ExitStack() as stack:
        for _ in range(30):
            stack.enter_context(admission.admit(timeout=.02))
        assert len(admission._leases) == 30
    assert not admission._leases


@pytest.mark.parametrize("snapshot", [None, (0, 0), (1000, 1001), (1000, -1), (True, 1)])
def test_unknown_memory_waits_bounded_and_never_spawns(snapshot, caplog):
    admission = gate(memory=lambda: snapshot)
    entered = False
    with caplog.at_level("INFO", logger="ai"), pytest.raises(ResourceWaitTimeout):
        with admission.admit(timeout=.02):
            entered = True
    assert not entered and not admission._leases and not admission._queue
    assert "memory_unknown" in caplog.text and "CLI_RESOURCE_TIMEOUT" in caplog.text


def test_low_memory_waits_then_admits_when_headroom_returns(caplog):
    memory = [100]
    admission = gate(memory=lambda: (1000, memory[0]))
    entered = threading.Event()
    def worker():
        with admission.admit(timeout=.5):
            entered.set()
    with caplog.at_level("INFO", logger="ai"), ThreadPoolExecutor(max_workers=1) as pool:
        f = pool.submit(worker)
        time.sleep(.03)
        assert not entered.is_set()
        memory[0] = 800
        f.result(timeout=1)
    assert entered.is_set() and "CLI_RESOURCE_WAIT" in caplog.text


def test_cancelled_wait_releases_queue_and_exception_releases_reservation():
    cancel = threading.Event()
    admission = gate(memory=lambda: (1000, 100))
    class Cancelled(Exception):
        pass
    def check():
        if cancel.is_set():
            raise Cancelled()
    def wait():
        with admission.admit(check_cancelled=check, timeout=1):
            pytest.fail("Low RAM cannot admit")
    with ThreadPoolExecutor(max_workers=1) as pool:
        f = pool.submit(wait)
        time.sleep(.03)
        cancel.set()
        with pytest.raises(Cancelled):
            f.result(timeout=.2)
    assert not admission._leases and not admission._queue


def test_exception_releases_reservation():
    admission = gate()
    with pytest.raises(ValueError):
        with admission.admit():
            raise ValueError("spawn failed")
    assert not admission._leases and not admission._queue


def test_unreaped_child_keeps_reservation_until_exit(monkeypatch):
    import core.cli_resources as resource
    monkeypatch.setattr(resource, "owned_process", lambda pid: None)
    child = Mock(pid=123)
    child.poll.return_value = None
    admission = gate()
    with admission.admit(run_id="owned") as lease:
        lease.attach(child)
    assert len(admission._leases) == 1
    assert admission.snapshot("owned")["cli_processes"] == 1
    child.poll.return_value = 0
    assert admission.snapshot("owned")["cli_processes"] == 0
    assert not admission._leases


def test_resource_snapshot_keeps_selected_concurrency_and_scopes_waiter():
    from core.ai_execution import AIExecutionLayer, AIExecutionPolicy
    admission = gate(memory=lambda: None)
    layer = AIExecutionLayer(AIExecutionPolicy(concurrency=20))
    layer.resource_snapshot = admission.snapshot
    layer.set_run_concurrency("video", 50)
    def worker():
        with pytest.raises(ResourceWaitTimeout, match="Chưa đo được RAM"):
            with admission.admit(run_id="video", timeout=.12):
                pytest.fail("Unknown RAM cannot admit")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker)
        time.sleep(.03)
        snapshot = layer.snapshot("video")
        assert snapshot["selected_concurrency"] == 50
        assert snapshot["resource_waiting_requests"] == 1
        assert snapshot["admission_reason"] == "memory_unknown"
        assert layer.snapshot("other")["resource_waiting_requests"] == 0
        future.result()
    assert layer.snapshot("video")["resource_waiting_requests"] == 0


def test_fifo_wait_with_sufficient_memory_reports_order_not_ram():
    admission = gate()
    older = (object(), "older")
    admission._queue.append(older)
    def worker():
        with pytest.raises(ResourceWaitTimeout, match="chờ lượt"):
            with admission.admit(run_id="newer", timeout=.12):
                pytest.fail("Must preserve FIFO")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker)
        time.sleep(.03)
        assert admission.snapshot("newer")["resource_wait_reason"] == "resource_fifo"
        future.result()
    assert admission._queue == [older]
    admission._queue.clear()


@pytest.mark.parametrize("failure", ["prompt", "key", "executable", "managed"])
def test_invalid_configuration_never_waits_for_memory(monkeypatch, failure):
    from core.engines.translation import opencode_client as oc
    client = oc.OpenCodeZenClient(api_key="fixture")
    monkeypatch.setattr(oc, "find_opencode_executable", lambda: None if failure == "executable" else "fixture.exe")
    monkeypatch.setattr(oc, "_managed_configuration_present", lambda: failure == "managed")
    admission = Mock()
    admission.admit.side_effect = AssertionError("Configuration must fail before resource admission")
    monkeypatch.setattr(oc, "opencode_resources", admission)
    if failure == "key":
        client._api_key = None
    with pytest.raises(oc.OpenCodeClientError):
        client._translate("" if failure == "prompt" else "hello", "model", "", 100,
                          SimpleNamespace(cancel_check=None, run_id="offline"), 1.)
    admission.admit.assert_not_called()


def test_transport_wait_preserves_provider_timeout_and_timeout_is_queue_error(monkeypatch):
    from core.engines.translation import opencode_client as oc
    from core.ai_execution import AIQueueTimeoutError
    client = oc.OpenCodeZenClient(api_key="fixture")
    monkeypatch.setattr(oc, "find_opencode_executable", lambda: "fixture.exe")
    monkeypatch.setattr(oc, "_managed_configuration_present", lambda: False)
    context = SimpleNamespace(cancel_check=None, run_id="offline-resource")
    events = []
    class Waiting:
        @contextmanager
        def admit(self, **kwargs):
            events.append("wait")
            yield "lease"
            events.append("released")
    monkeypatch.setattr(oc, "opencode_resources", Waiting())
    operation = Mock(return_value="result")
    monkeypatch.setattr(client, "_translate_admitted", operation)
    assert client._translate("text", "model", "system", 100, context, 17.) == "result"
    assert operation.call_args.args[-2:] == (17., "lease")
    assert events == ["wait", "released"]
    class Exhausted:
        @contextmanager
        def admit(self, **kwargs):
            raise ResourceWaitTimeout("RAM admission timed out")
            yield
    monkeypatch.setattr(oc, "opencode_resources", Exhausted())
    operation.reset_mock()
    with pytest.raises(AIQueueTimeoutError):
        client._translate("text", "model", "system", 100, context, 17.)
    operation.assert_not_called()


def test_local_pressure_does_not_open_provider_circuit_or_spin_retries(monkeypatch, tmp_path):
    from core.engines.translation import opencode_client as oc
    from core.ai_execution import AIExecutionLayer, AIExecutionPolicy
    layer = AIExecutionLayer(AIExecutionPolicy(retry_jitter=0), cache_root=tmp_path)
    client = oc.OpenCodeZenClient(api_key="fixture", execution_layer=layer, max_retries=3)
    monkeypatch.setattr(oc, "find_opencode_executable", lambda: "fixture.exe")
    monkeypatch.setattr(oc, "_managed_configuration_present", lambda: False)
    class Exhausted:
        @contextmanager
        def admit(self, **kwargs):
            raise ResourceWaitTimeout("RAM admission timed out")
            yield
    monkeypatch.setattr(oc, "opencode_resources", Exhausted())
    spawn = Mock(side_effect=AssertionError("Must not spawn under resource pressure"))
    monkeypatch.setattr(oc.subprocess, "Popen", spawn)
    with pytest.raises(oc.OpenCodeRequestError) as caught:
        client.translate("offline-resource-test", use_cache=False)
    assert caught.value.code == "provider_queue_timeout" and caught.value.retryable
    assert layer.snapshot()["consecutive_failures"] == 0
    assert layer.snapshot()["retry_requests"] == 0
    assert layer.snapshot()["active_requests"] == 0
    spawn.assert_not_called()
