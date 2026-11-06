"""Repeatable offline benchmark for the public video download worker.

The harness uses a local HTTP server, so it never sends test traffic to Douyin.
It still exercises ``core.download_worker.download_resolved_video`` with a real
HTTP response and a response adapter injected only by this script.  The server
supports byte ranges and deterministic failure modes for regression checks.

Examples (PowerShell):

    venv\\Scripts\\python.exe scripts\\benchmark_downloader.py --repeat 3
    venv\\Scripts\\python.exe scripts\\benchmark_downloader.py --scenario all

The JSON output is suitable for comparing baseline and optimized revisions.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import http.client
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

# Running ``python scripts/benchmark_downloader.py`` sets sys.path[0] to the
# scripts directory; explicitly include the project root for ``core`` imports.
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import download_worker as worker


VIDEO_ID = "7688769264395767049"


def _sample_mp4(target_bytes: int = 0) -> bytes:
    """Create the same tiny valid MP4 used by the worker acceptance tests."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required for the local benchmark")
    with tempfile.TemporaryDirectory(prefix="download-benchmark-ffmpeg-") as directory:
        output = Path(directory) / "sample.mp4"
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=64x64:r=24:d=1",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=1",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                "-shortest",
                str(output),
            ],
            check=True,
            capture_output=True,
            timeout=30,
            **flags,
        )
        data = output.read_bytes()
    if target_bytes > len(data):
        # MP4 permits a trailing free-space region.  Padding the valid sample
        # lets this same harness exercise multi-megabyte writes without
        # transcoding a long synthetic movie or contacting a real CDN.
        data += b"\0" * (target_bytes - len(data))
    return data


@dataclass
class ServerState:
    payload: bytes
    scenario: str = "normal"
    fail_status: int | None = None
    fail_first: int = 0
    delay: float = 0.0
    chunk_delay: float = 0.0
    chunk_size: int = 256 * 1024
    interrupt_bytes: int = 0
    requests: int = 0
    range_requests: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


class _VideoHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):  # pragma: no cover - benchmark output is JSON
        return

    @property
    def state(self) -> ServerState:
        return self.server.state  # type: ignore[attr-defined]

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        if urlsplit(self.path).path != "/video.mp4":
            self.send_error(404)
            return
        with self.state.lock:
            self.state.requests += 1
            if self.headers.get("Range", "").startswith("bytes="):
                self.state.range_requests += 1
            request_number = self.state.requests
        if self.state.delay:
            time.sleep(self.state.delay)

        failure = self.state.fail_status if request_number <= self.state.fail_first else None
        if self.state.scenario.startswith("http") and self.state.scenario[4:].isdigit():
            failure = int(self.state.scenario[4:])
        if self.state.scenario == "cdn_failure":
            failure = 503
        if self.state.scenario == "timeout":
            # Send headers, then hold the socket long enough for the client
            # timeout.  The worker must preserve a safe, resumable failure.
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(self.state.payload)))
            self.end_headers()
            self.wfile.flush()
            time.sleep(15)
            return
        if failure:
            self.send_response(failure)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            return

        payload = self.state.payload
        if self.state.scenario == "corrupt":
            payload = b"not-a-video-" + payload[:128]
        range_header = self.headers.get("Range", "")
        start, end = 0, len(payload) - 1
        status = 200
        if range_header.startswith("bytes="):
            try:
                first, last = range_header[6:].split("-", 1)
                start = int(first)
                end = int(last) if last else len(payload) - 1
                if start < 0 or start >= len(payload) or end < start:
                    raise ValueError
                end = min(end, len(payload) - 1)
                status = 206
            except (TypeError, ValueError):
                self.send_response(416)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        body = payload[start : end + 1]
        declared_length = len(body)
        if self.state.scenario == "interrupt" and request_number == 1:
            # Deliberately close after a valid prefix.  The next request must
            # use Range + If-Range and complete the exact same entity.
            body = body[: self.state.interrupt_bytes or max(1, len(body) // 3)]
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(declared_length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", '"local-benchmark-v1"')
        self.send_header("Content-Range", f"bytes {start}-{end}/{len(payload)}") if status == 206 else None
        self.end_headers()
        for offset in range(0, len(body), max(1, self.state.chunk_size)):
            self.wfile.write(body[offset:offset + self.state.chunk_size])
            self.wfile.flush()
            if self.state.chunk_delay:
                time.sleep(self.state.chunk_delay)
        if self.state.scenario == "interrupt" and request_number == 1:
            self.close_connection = True
            self.connection.close()


class _QuietHTTPServer(ThreadingHTTPServer):
    """Suppress expected client resets when adaptive probing closes a stream."""

    def handle_error(self, _request, _client_address):  # pragma: no cover - diagnostics only
        return


class _ResponseAdapter:
    """Small requests-like adapter expected by ``download_resolved_video``."""

    def __init__(self, response: http.client.HTTPResponse, metrics: dict, request_url: str | None = None):
        self._response = response
        self.status = response.status
        self.headers = {key: value for key, value in response.getheaders()}
        self._metrics = metrics
        self._first_byte = False
        self._request_url = request_url
        self.url = request_url
        self._request_started = metrics["response_started"]

    def iter_content(self, chunk_size=65536):
        while True:
            started = time.monotonic()
            try:
                chunk = self._response.read(chunk_size)
            finally:
                self._metrics["network_read_seconds"] += time.monotonic() - started
            if chunk and not self._first_byte:
                self._first_byte = True
                if self._metrics["ttfb_seconds"] is None:
                    self._metrics["ttfb_seconds"] = max(time.monotonic() - self._request_started, 0.0)
            if not chunk:
                return
            self._metrics["network_bytes"] += len(chunk)
            yield chunk

    def close(self):
        self._response.close()


def _serve(state: ServerState):
    server = _QuietHTTPServer(("127.0.0.1", 0), _VideoHandler)
    server.state = state  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class LocalVideoServer:
    """Importable local HTTP fixture for downloader and range tests."""

    def __init__(self, payload: bytes, scenario: str = "normal", *, delay: float = 0.0,
                 chunk_delay: float = 0.0, chunk_size: int = 256 * 1024):
        self.state = ServerState(payload=payload, scenario=scenario, delay=delay,
                                 chunk_delay=chunk_delay, chunk_size=chunk_size)
        self.server = None
        self.thread = None

    def __enter__(self):
        self.server, self.thread = _serve(self.state)
        return self

    @property
    def url(self):
        if self.server is None:
            raise RuntimeError("LocalVideoServer must be entered before use")
        host, port = self.server.server_address
        return f"http://{host}:{port}/video.mp4"

    def close(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            if self.thread:
                self.thread.join(timeout=3)
            self.server = None
            self.thread = None

    def __exit__(self, *_exc):
        self.close()


def make_local_opener(server: LocalVideoServer, metrics: dict | None = None):
    """Return an ``open_public_media``-compatible callable for tests."""
    metrics = metrics if metrics is not None else {}
    metrics.setdefault("network_bytes", 0)
    metrics.setdefault("network_read_seconds", 0.0)
    metrics.setdefault("ttfb_seconds", None)
    metrics.setdefault("response_started", None)
    metrics.setdefault("connect_seconds", 0.0)

    def opener(url, headers=None):
        del url
        request_started = time.monotonic()
        host, port = server.server.server_address
        connection = http.client.HTTPConnection(host, port, timeout=3)
        connect_started = time.monotonic()
        connection.request("GET", "/video.mp4", headers=dict(headers or {}))
        response = connection.getresponse()
        metrics["connect_seconds"] += time.monotonic() - connect_started
        metrics["response_started"] = request_started
        return _ResponseAdapter(response, metrics, server.url)

    return opener


def _info(url: str):
    return {
        "id": VIDEO_ID,
        "title": "Local benchmark video",
        "duration": 1.0,
        "formats": [
            {
                "urls": [url],
                "codec": "h264",
                "width": 64,
                "height": 64,
                "bitrate": 100_000,
                "size": 1,
                "original": True,
            }
        ],
    }


def run_once(payload: bytes, scenario: str, work_dir: Path):
    local_server = LocalVideoServer(payload, scenario=scenario,
                                    chunk_delay=getattr(run_once, "chunk_delay", 0.0),
                                    chunk_size=getattr(run_once, "chunk_size", 256 * 1024))
    local_server.__enter__()
    state = local_server.state
    metrics = {
        "network_bytes": 0,
        "network_read_seconds": 0.0,
        "ttfb_seconds": None,
        "response_started": None,
        "connect_seconds": 0.0,
        "disk_write_seconds": 0.0,
        "verify_seconds": 0.0,
    }
    original_opener = worker.open_public_media
    original_adaptive = worker.adaptive_chunks

    worker.open_public_media = make_local_opener(local_server, metrics)
    if getattr(run_once, "disable_ranges", False):
        worker.adaptive_chunks = lambda response, **_kwargs: response.iter_content(256 * 1024)
    original_verify = worker.verify_video

    def timed_verify(path):
        started_verify = time.monotonic()
        try:
            return original_verify(path)
        finally:
            metrics["verify_seconds"] += time.monotonic() - started_verify

    worker.verify_video = timed_verify
    original_path_open = Path.open

    class _TimedFile:
        def __init__(self, handle):
            self._handle = handle

        def __getattr__(self, name):
            return getattr(self._handle, name)

        def write(self, data):
            started_write = time.monotonic()
            try:
                return self._handle.write(data)
            finally:
                metrics["disk_write_seconds"] += time.monotonic() - started_write

        def flush(self):
            started_write = time.monotonic()
            try:
                return self._handle.flush()
            finally:
                metrics["disk_write_seconds"] += time.monotonic() - started_write

        def __enter__(self):
            self._handle.__enter__()
            return self

        def __exit__(self, *exc):
            return self._handle.__exit__(*exc)

    def timed_path_open(path, mode="r", *args, **kwargs):
        if ".mp4.part" not in path.name:
            if scenario == "disk_failure" and ".mp4.part" in path.name:
                raise OSError(errno.ENOSPC, "synthetic disk full")
            return original_path_open(path, mode, *args, **kwargs)
        if scenario == "disk_failure" and any(flag in mode for flag in ("w", "a", "+")):
            raise OSError(errno.ENOSPC, "synthetic disk full")
        return _TimedFile(original_path_open(path, mode, *args, **kwargs))

    Path.open = timed_path_open
    events = []
    prefix = str(work_dir / f"video-{scenario}")
    started = time.monotonic()
    result = None
    hash_check_seconds = None
    try:
        result = worker.download_resolved_video(_info("http://127.0.0.1/video.mp4"), prefix, events.append)
        finished = time.monotonic()
        succeeded = True
        output = Path(result["file_path"])
        hash_started = time.monotonic()
        output_digest = hashlib.sha256(output.read_bytes()).hexdigest()
        integrity = output_digest == hashlib.sha256(payload).hexdigest()
        hash_check_seconds = time.monotonic() - hash_started
        error = None
        error_code, resumable = None, False
    except Exception as exc:  # benchmark intentionally exercises failure modes
        succeeded = False
        integrity = False
        error = type(exc).__name__ + ": " + str(exc)
        error_code, resumable = getattr(exc, "code", None), bool(getattr(exc, "resumable", False))
        finished = time.monotonic()
    # Stop the clock before ``ThreadingHTTPServer.shutdown``.  Its default
    # selector poll interval is 0.5 s and would otherwise dwarf tiny local
    # payloads, making a downloader optimization appear ineffective.
    worker.open_public_media = original_opener
    worker.adaptive_chunks = original_adaptive
    worker.verify_video = original_verify
    Path.open = original_path_open
    local_server.close()
    elapsed = max(finished - started, 1e-9)
    downloaded = max((event.get("downloaded_bytes") or 0 for event in events), default=0)
    worker_metrics = result.get("download_metrics", {}) if result else {}
    # A successful Range request is another connection, not a retry. On a
    # failed worker call, diagnostics expose the actual attempt number.
    retries = worker_metrics.get("retry_count", max(
        (max(event.get("retry_count", 0), event.get("attempt", 1) - 1) for event in events), default=0))
    network_wait = worker_metrics.get("network_read_seconds")
    return {
        "scenario": scenario,
        "success": succeeded,
        "integrity": integrity,
        "bytes": downloaded,
        "duration_seconds": elapsed,
        "average_mbps": downloaded / elapsed / 1024 / 1024,
        "download_seconds": worker_metrics.get("elapsed_seconds"),
        "download_average_mibps": worker_metrics.get("average_speed", 0) / 1024 / 1024 if worker_metrics else None,
        "hash_check_seconds": hash_check_seconds,
        "ttfb_seconds": metrics["ttfb_seconds"],
        "network_connect_seconds": metrics["connect_seconds"],
        "network_read_seconds": metrics["network_read_seconds"],
        "network_wait_seconds": network_wait,
        "disk_write_seconds": metrics["disk_write_seconds"],
        "verify_seconds": metrics["verify_seconds"],
        "http_requests": state.requests,
        "range_requests": getattr(state, "range_requests", 0),
        "wire_bytes": metrics["network_bytes"],
        "retry_count": retries,
        "strategy": worker_metrics.get("strategy"),
        "connections": worker_metrics.get("connections"),
        "peak_connections": max((event.get("connections", 1) for event in events), default=1),
        "strategy_history": list(dict.fromkeys(event["strategy"] for event in events if "strategy" in event)),
        "worker_metrics": worker_metrics,
        "completed_file_exists": Path(prefix + ".mp4").is_file(),
        "checkpoint_exists": Path(prefix + ".resume.json").is_file(),
        "resumable": resumable,
        "error_code": error_code,
        "error": error,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=3, help="normal runs to average (default: 3)")
    parser.add_argument("--size-bytes", type=int, default=0,
                        help="pad the valid MP4 to at least this many bytes")
    parser.add_argument("--large-bytes", type=int, default=0,
                        help="also run one large normal payload at this size")
    parser.add_argument("--chunk-delay-ms", type=float, default=0.0,
                        help="delay after each local response chunk (controlled bottleneck)")
    parser.add_argument("--chunk-size", type=int, default=256 * 1024,
                        help="local response chunk size for --chunk-delay-ms")
    parser.add_argument("--disable-ranges", action="store_true",
                        help="baseline mode: force the worker's single stream")
    parser.add_argument(
        "--scenario",
        choices=("normal", "corrupt", "interrupt", "timeout", "disk_failure", "cdn_failure",
                 "http403", "http429", "http500", "all"),
        default="normal",
        help="normal, one fault mode, or all offline acceptance scenarios",
    )
    parser.add_argument("--json", type=Path, help="optional output JSON path")
    args = parser.parse_args(argv)
    if args.repeat < 1 or args.repeat > 30:
        parser.error("--repeat must be between 1 and 30")
    if args.size_bytes < 0 or args.large_bytes < 0:
        parser.error("payload sizes must be non-negative")
    if args.chunk_delay_ms < 0 or args.chunk_size < 1:
        parser.error("chunk delay/size must be non-negative/positive")
    run_once.chunk_delay = args.chunk_delay_ms / 1000.0
    run_once.chunk_size = args.chunk_size
    run_once.disable_ranges = args.disable_ranges
    payload = _sample_mp4(args.size_bytes)
    scenarios = ["normal"] * args.repeat if args.scenario in ("normal", "all") else [args.scenario] * args.repeat
    if args.scenario == "all":
        scenarios += ["corrupt", "interrupt", "timeout", "disk_failure",
                      "cdn_failure", "http403", "http429", "http500"]
    with tempfile.TemporaryDirectory(prefix="download-benchmark-") as directory:
        results = [run_once(payload, scenario, Path(directory)) for scenario in scenarios]
        if args.large_bytes:
            large_payload = _sample_mp4(args.large_bytes)
            results.append(run_once(large_payload, "large", Path(directory)))
    summary = {
        "payload_bytes": len(payload),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "runs": results,
        "normal_successes": sum(item["success"] for item in results if item["scenario"] == "normal"),
        "successful_runs": sum(item["success"] for item in results),
        "timing_notes": {
            "duration_seconds": "worker call including media verification, excluding fixture setup, SHA comparison and server shutdown",
            "download_seconds": "worker transfer phase including request opening, writes and retry waits, excluding media verification",
            "network_connect_seconds": "sum of local HTTP request/header waits; concurrent requests overlap",
            "network_read_seconds": "sum of socket read waits; concurrent reads overlap and can exceed wall time",
            "network_wait_seconds": "worker wall time awaiting the next contiguous block",
            "disk_write_seconds": "timed file writes/flushes; worker_metrics also includes checkpoint writes",
        },
    }
    encoded = json.dumps(summary, ensure_ascii=False, indent=2)
    print(encoded)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(encoded + "\n", encoding="utf-8")
    expected_failures = {"corrupt", "timeout", "disk_failure", "cdn_failure", "http403", "http429", "http500"}
    expected_successes = {"normal", "interrupt", "large"}
    observed_as_expected = all(
        (item["success"] and item["integrity"]
         and (item["scenario"] != "interrupt" or item["range_requests"] > 0)) if item["scenario"] in expected_successes
        else (not item["success"] and not item["completed_file_exists"])
        for item in results
        if item["scenario"] in (expected_successes | expected_failures)
    )
    return 0 if observed_as_expected else 1


if __name__ == "__main__":
    raise SystemExit(main())
