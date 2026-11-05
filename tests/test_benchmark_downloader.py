"""Benchmark accounting checks without HTTP traffic or media subprocesses."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import benchmark_downloader as benchmark


@pytest.fixture
def mocked_benchmark(monkeypatch):
    clock = SimpleNamespace(now=100.0)
    servers = []

    class FakeServer:
        def __init__(self, *_args, **_kwargs):
            self.state = SimpleNamespace(requests=0)
            self.closed = False
            servers.append(self)

        def __enter__(self):
            clock.now += 25.0  # fixture setup is outside the worker clock
            return self

        def close(self):
            self.closed = True
            clock.now += 50.0  # shutdown is outside the worker clock

    def unexpected_request(*_args, **_kwargs):
        raise AssertionError("Accounting tests must not use the network")

    monkeypatch.setattr(benchmark, "LocalVideoServer", FakeServer)
    monkeypatch.setattr(benchmark, "make_local_opener", lambda *_args: unexpected_request)
    monkeypatch.setattr(benchmark.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(benchmark.run_once, "disable_ranges", False, raising=False)
    return clock, servers


@pytest.mark.parametrize("include_worker_metrics", [True, False])
def test_successful_parallel_connections_are_not_retries(
    monkeypatch, tmp_path, mocked_benchmark, include_worker_metrics,
):
    clock, servers = mocked_benchmark
    payload = b"exact benchmark payload"

    def download(_info, prefix, send):
        servers[-1].state.requests = 9  # initial GET plus successful ranges
        output = Path(prefix + ".mp4")
        output.write_bytes(payload)
        send({"downloaded_bytes": len(payload), "retry_count": 0,
              "strategy": "parallel-range", "connections": 4})
        clock.now += 2.0
        result = {"file_path": str(output)}
        if include_worker_metrics:
            result["download_metrics"] = {"retry_count": 0, "elapsed_seconds": 1.5,
                                          "strategy": "parallel-range", "connections": 4}
        return result

    monkeypatch.setattr(benchmark.worker, "download_resolved_video", download)
    result = benchmark.run_once(payload, "normal", tmp_path)
    assert result["success"] and result["integrity"]
    assert result["http_requests"] == 9
    assert result["retry_count"] == 0
    assert result["peak_connections"] == 4
    assert result["strategy_history"] == ["parallel-range"]
    assert servers[-1].closed


def test_duration_stops_before_sha_comparison_and_server_shutdown(
    monkeypatch, tmp_path, mocked_benchmark,
):
    clock, servers = mocked_benchmark
    payload = b"integrity checked after worker returns"
    original_sha256 = benchmark.hashlib.sha256
    original_read_bytes = Path.read_bytes

    def download(_info, prefix, send):
        output = Path(prefix + ".mp4")
        output.write_bytes(payload)
        send({"downloaded_bytes": len(payload)})
        clock.now += 2.5
        return {"file_path": str(output), "download_metrics": {"elapsed_seconds": 2.0,
                                                              "retry_count": 0}}

    def slow_read(path):
        clock.now += 3.0
        return original_read_bytes(path)

    def slow_sha256(data):
        clock.now += 7.0
        return original_sha256(data)

    monkeypatch.setattr(benchmark.worker, "download_resolved_video", download)
    monkeypatch.setattr(Path, "read_bytes", slow_read)
    monkeypatch.setattr(benchmark.hashlib, "sha256", slow_sha256)
    result = benchmark.run_once(payload, "normal", tmp_path)
    assert result["success"] and result["integrity"]
    assert result["duration_seconds"] == 2.5
    assert result["download_seconds"] == 2.0
    assert result["hash_check_seconds"] == 17.0
    assert result["average_mbps"] == pytest.approx(len(payload) / 2.5 / 1024**2)
    assert clock.now == 194.5  # setup 25 + worker 2.5 + SHA/read 17 + shutdown 50
    assert servers[-1].closed


@pytest.mark.parametrize(
    "events, expected_retries",
    [
        ([{"kind": "diagnostic", "attempt": 1}], 0),
        ([{"kind": "diagnostic", "attempt": 1},
          {"kind": "diagnostic", "attempt": 3},
          {"kind": "diagnostic", "attempt": 5}], 4),
        ([{"kind": "progress", "retry_count": 6},
          {"kind": "diagnostic", "attempt": 5}], 6),
    ],
)
def test_failure_counts_attempt_retries_instead_of_requests(
    monkeypatch, tmp_path, mocked_benchmark, events, expected_retries,
):
    clock, servers = mocked_benchmark

    def fail_download(_info, _prefix, send):
        servers[-1].state.requests = 12
        for event in events:
            send({"downloaded_bytes": 123, **event})
        clock.now += 4.0
        raise benchmark.worker.MediaDownloadError("Synthetic failure")

    monkeypatch.setattr(benchmark.worker, "download_resolved_video", fail_download)
    result = benchmark.run_once(b"payload", "http500", tmp_path)
    assert not result["success"] and not result["integrity"]
    assert result["http_requests"] == 12
    assert result["retry_count"] == expected_retries
    assert result["bytes"] == 123
    assert result["duration_seconds"] == 4.0
    assert result["hash_check_seconds"] is None
    assert result["error"] == "MediaDownloadError: Synthetic failure"
    assert servers[-1].closed
