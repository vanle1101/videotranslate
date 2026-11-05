"""Offline evidence: diagnostic throughput uses validated bytes, never plans."""
import io
import itertools
import threading
from types import SimpleNamespace

import pytest

from scripts import diagnose_douyin as diagnostic


URL = "https://v3.example.douyinvod.com/video.mp4?sign=private"


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Diagnostic metrics tests must not contact a service")
    monkeypatch.setattr(diagnostic.resolver, "open_public_media", forbidden)
    monkeypatch.setattr(diagnostic.subprocess, "run", forbidden)
    monkeypatch.setattr(diagnostic, "dns_probe", lambda host: {"host": host})
    monkeypatch.setattr(diagnostic, "_curl_probe", lambda *_args: {"status": 206})
    monkeypatch.setattr(diagnostic, "_read_range_family", forbidden)
    monkeypatch.setattr(diagnostic, "_run_redirect_capture", lambda url, operation: (
        operation(), [], [{"host": "v3.example.douyinvod.com", "ip_versions": [4]}]))


class Response(io.BytesIO):
    def __init__(self, body=b"abcdefgh", *, status=206, headers=None, failure=False):
        super().__init__(body)
        self.status, self.url = status, URL
        self.headers = {"Content-Range": "bytes 0-7/100", "Content-Length": "8", **(headers or {})}
        self.failure, self.calls = failure, 0

    def read(self, size=-1):
        self.calls += 1
        if self.failure and self.calls > 1:
            raise TimeoutError("private upstream address")
        return super().read(min(size, 2) if self.failure else size)


def _row(start, end, *, count=None, status=206, error=None):
    result = {"start": start, "end": end, "status": status,
              "requested_bytes": end - start + 1,
              "read_bytes": end - start + 1 if count is None else count,
              "headers": {"content_range": f"bytes {start}-{end}/100000000",
                          "content_length": str(end - start + 1)}}
    if error:
        result["error_type"] = error
    return result


def _info():
    return {"formats": [{"urls": [URL], "codec": "h264", "width": 64,
                         "height": 64, "bitrate": 1, "original": True}]}


def test_complete_range_uses_actual_bytes_and_closes_response(monkeypatch):
    response = Response()
    monkeypatch.setattr(diagnostic.resolver, "open_public_media", lambda *_args, **_kwargs: response)
    row = diagnostic._read_range(URL, 0, 7)
    assert row["read_bytes"] == 8
    assert row["comparison_valid"] is True
    assert row["throughput_mib_s"] is not None
    assert response.closed


@pytest.mark.parametrize("status,headers", [
    (200, {}), (206, {"Content-Range": "bytes 1-8/100"}),
    (206, {"Content-Range": "bytes 0-6/100"}),
    (206, {"Content-Range": "bytes 0-7/7"}),
    (206, {"Content-Range": "private malformed header"}),
    (206, {"Content-Length": "7"}),
])
def test_wrong_range_cannot_produce_comparison_throughput(monkeypatch, status, headers):
    response = Response(status=status, headers=headers)
    monkeypatch.setattr(diagnostic.resolver, "open_public_media", lambda *_args, **_kwargs: response)
    row = diagnostic._read_range(URL, 0, 7)
    assert row["read_bytes"] == 0
    assert row["comparison_valid"] is False
    assert row["throughput_mib_s"] is None
    assert response.closed


@pytest.mark.parametrize("failure,expected", [(False, 3), (True, 2)])
def test_short_or_interrupted_range_retains_only_received_count(monkeypatch, failure, expected):
    response = Response(body=b"abc", failure=failure)
    monkeypatch.setattr(diagnostic.resolver, "open_public_media", lambda *_args, **_kwargs: response)
    row = diagnostic._read_range(URL, 0, 7)
    assert row["read_bytes"] == expected
    assert row["comparison_valid"] is False
    assert row["throughput_mib_s"] is None
    assert "private upstream" not in str(row)
    assert response.closed


@pytest.mark.parametrize("status", [403, 429])
def test_open_error_preserves_restriction_without_upstream_text(monkeypatch, status):
    def reject(*_args, **_kwargs):
        raise diagnostic.resolver.DouyinResolveError(
            "private URL", code="http_error", status_code=status)
    monkeypatch.setattr(diagnostic.resolver, "open_public_media", reject)
    row = diagnostic._read_range(URL, 0, 7)
    assert row["status"] == status and row["read_bytes"] == 0
    assert row["comparison_valid"] is False
    assert "private URL" not in str(row)


@pytest.mark.parametrize("kind,total", [("short", 7), ("failed", 4)])
def test_partial_batch_sums_actual_bytes_and_withholds_comparison(monkeypatch, kind, total):
    def read(_url, start, end):
        if (start, end) == (0, 3):
            return _row(start, end, count=3 if kind == "short" else 0,
                        status=206 if kind == "short" else 503,
                        error=None if kind == "short" else "TimeoutError")
        return _row(start, end)
    monkeypatch.setattr(diagnostic, "_read_range", read)
    report = diagnostic.run_bounded(URL, _info(), sample_bytes=8)
    batch = next(row for row in report["parallel_samples"] if row["connections"] == 2)
    assert batch["read_bytes"] == total
    assert batch["comparison_valid"] is False
    assert batch["throughput_mib_s"] is None


def test_successful_batches_measure_received_bytes_with_monotonic_time(monkeypatch):
    ticks = itertools.count(0, .25)
    monkeypatch.setattr(diagnostic.time, "perf_counter", lambda: next(ticks))
    monkeypatch.setattr(diagnostic, "_read_range", lambda _url, start, end: _row(start, end))
    size = 8 * 1024 * 1024
    report = diagnostic.run_bounded(URL, _info(), sample_bytes=size)
    assert len(report["probes"]) == 1
    assert [batch["connections"] for batch in report["parallel_samples"]] == [1, 2, 4, 8]
    assert all(batch["read_bytes"] == size and batch["comparison_valid"]
               and batch["throughput_mib_s"] == 32 for batch in report["parallel_samples"])


@pytest.mark.parametrize("status", [403, 429])
@pytest.mark.parametrize("location", ["first", "repeat", "curl", "parallel"])
def test_restriction_stops_following_probe_stages(monkeypatch, status, location):
    calls, curl_calls, lock = [], [], threading.Lock()
    def read(_url, start, end):
        with lock:
            calls.append((start, end))
            call = len(calls)
        blocked = ((location == "first" and call == 1)
                   or (location == "repeat" and call == 2)
                   or (location == "parallel" and end - start + 1 == 4 and start == 0))
        return _row(start, end, count=0, status=status, error="HTTPError") if blocked else _row(start, end)
    def curl(*_args):
        curl_calls.append(True)
        return {"status": status if location == "curl" else 206}
    monkeypatch.setattr(diagnostic, "_read_range", read)
    monkeypatch.setattr(diagnostic, "_curl_probe", curl)
    report = diagnostic.run_bounded(URL, _info(), repeat=2 if location == "repeat" else 1,
                                    sample_bytes=8)
    assert report["stopped"] == "service_restriction" and report["stop_status"] == status
    assert report["ip_family"] == {}
    if location in {"first", "repeat"}:
        assert not curl_calls and not report["parallel_samples"]
        assert len(calls) == (1 if location == "first" else 2)
    elif location == "curl":
        assert len(calls) == 1 and not report["parallel_samples"]
    else:
        assert [batch["connections"] for batch in report["parallel_samples"]] == [1, 2]
        assert all(end - start + 1 >= 4 for start, end in calls)
        assert report["parallel_samples"][-1]["throughput_mib_s"] is None


@pytest.mark.parametrize("status,count,content_range,valid", [
    (206, 8, "bytes 0-7/100", True),
    (206, 3, "bytes 0-7/100", False),
    (200, 8, "", False),
    (206, 8, "bytes 1-8/100", False),
])
def test_curl_comparison_requires_exact_response_and_bounds_output(
        monkeypatch, status, count, content_range, valid):
    def curl(argv, **kwargs):
        assert argv[argv.index("--max-filesize") + 1] == "8"
        assert URL not in argv
        return SimpleNamespace(returncode=0, stdout=(
            f"status={status} effective={URL} ttfb=0.1 total=1 bytes={count} "
            f"peer=1.1.1.1 http=1.1 content_range={content_range} content_length=8"))
    monkeypatch.setattr(diagnostic.subprocess, "run", curl)
    # The autouse fixture substitutes curl at the run_bounded boundary only;
    # call the real implementation retained on the module function below.
    row = REAL_CURL(URL, 8)
    assert row["read_bytes"] == count and row["comparison_valid"] is valid
    assert (row["throughput_mib_s"] is not None) is valid
    assert "private" not in str(row)


REAL_CURL = diagnostic._curl_probe
