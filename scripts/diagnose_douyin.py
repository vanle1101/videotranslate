"""Reproducible, sanitized Douyin download diagnostics.

The default invocation only resolves a fresh public source and performs bounded
range probes.  ``--full-baseline`` runs one complete transfer through a frozen
copy of the current download worker, writing only host/metric metadata to the
audit directory.  Signed URLs are deliberately kept in memory and never
serialized or printed.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import douyin_resolver as resolver  # noqa: E402


DEFAULT_URL = "https://www.douyin.com/video/7689326480735620402"
SAMPLE_BYTES = 8 * 1024 * 1024
SAFE_HOST = re.compile(r"^[a-z0-9.-]{1,255}$", re.I)


def host_only(url: str | None) -> str | None:
    try:
        host = urlsplit(url or "").hostname
    except ValueError:
        return None
    return host.lower() if host and SAFE_HOST.fullmatch(host) else None


def sanitize_headers(headers: Any) -> dict[str, Any]:
    if not hasattr(headers, "items"):
        return {}
    result: dict[str, Any] = {}
    for key in ("Content-Length", "Content-Range", "Accept-Ranges", "Content-Type",
                "ETag", "Last-Modified", "Server", "Via", "Age"):
        value = headers.get(key)
        if value is not None:
            # ETag/Last-Modified are useful for resume tests but bounded to avoid
            # ever persisting an unexpected header value.
            result[key.lower().replace("-", "_")] = str(value)[:512]
    return result


def dns_probe(host: str) -> dict[str, Any]:
    started = time.perf_counter()
    rows: list[dict[str, Any]] = []
    error_type = None
    seen: set[str] = set()
    try:
        for family, _kind, _proto, _canon, sockaddr in socket.getaddrinfo(
                host, 443, type=socket.SOCK_STREAM):
            address = sockaddr[0]
            if address in seen:
                continue
            seen.add(address)
            ip = ipaddress.ip_address(address)
            rows.append({"ip_version": ip.version, "is_global": ip.is_global})
    except OSError as exc:
        error_type = type(exc).__name__
    return {"host": host, "lookup_ms": round((time.perf_counter() - started) * 1000, 2),
            "addresses": rows, "error_type": error_type}


def _format_summary(info: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    formats: list[dict[str, Any]] = []
    for item in info.get("formats", []):
        urls = item.get("urls", []) if isinstance(item, dict) else []
        formats.append({"codec": item.get("codec"), "width": item.get("width"),
                        "height": item.get("height"), "bitrate": item.get("bitrate"),
                        "size": item.get("size"), "original": bool(item.get("original")),
                        "url_hosts": [host_only(u) for u in urls],
                        "url_count": len(urls)})
    chosen = max(info["formats"], key=lambda item: (
        bool(item.get("original")), item.get("width", 0) * item.get("height", 0),
        item.get("bitrate", 0), item.get("codec") == "h264"))
    urls = chosen.get("urls", [])
    primary = urls[0]
    chosen_summary = {"codec": chosen.get("codec"), "width": chosen.get("width"),
                      "height": chosen.get("height"), "bitrate": chosen.get("bitrate"),
                      "size": chosen.get("size"), "original": bool(chosen.get("original")),
                      "url_host": host_only(primary), "url_count": len(urls)}
    return formats, chosen_summary, primary


def _exact_range_headers(status: Any, headers: dict[str, Any], start: int, end: int) -> bool:
    """Only an exact partial entity response can enter a Range comparison."""
    match = re.fullmatch(r"bytes (\d{1,20})-(\d{1,20})/(\d{1,20})",
                         str(headers.get("content_range", "")))
    if status != 206 or not match:
        return False
    first, last, total = map(int, match.groups())
    length = headers.get("content_length")
    return (first == start and last == end and end < total
            and (length is None or (str(length).isdigit() and int(length) == end - start + 1)))


def _valid_range_sample(row: dict[str, Any]) -> bool:
    start, end, count = row.get("start"), row.get("end"), row.get("read_bytes")
    return (type(start) is int and type(end) is int and type(count) is int
            and end >= start and count == end - start + 1
            and not row.get("error_type") and not row.get("skipped")
            and _exact_range_headers(row.get("status"), row.get("headers", {}), start, end))


def _service_restricted(row: dict[str, Any]) -> bool:
    return row.get("status") in {403, 429}


def _read_range(url: str, start: int, end: int) -> dict[str, Any]:
    response = None
    expected = end - start + 1
    begun = time.perf_counter()
    opened = None
    count = 0
    result: dict[str, Any] = {"start": start, "end": end, "requested_bytes": expected,
                              "read_bytes": 0, "throughput_mib_s": None,
                              "range_valid": False, "comparison_valid": False}
    try:
        response = resolver.open_public_media(url, headers={"Range": f"bytes={start}-{end}"})
        opened = time.perf_counter()
        result.update({"status": response.status, "final_host": host_only(response.url),
                       "ttfb_ms": round((opened - begun) * 1000, 2),
                       "headers": sanitize_headers(response.headers)})
        result["range_valid"] = _exact_range_headers(response.status, result["headers"], start, end)
        # Do not consume an ignored Range, access-denied body, or a different
        # suffix: it cannot provide comparable benchmark bytes.
        if not result["range_valid"]:
            result["error_code"] = "range_invalid"
            return result
        while count < expected:
            chunk = response.read(min(256 * 1024, expected - count))
            if not chunk:
                break
            count += len(chunk)
        elapsed = max(time.perf_counter() - opened, 1e-9)
        result.update({"read_bytes": count, "read_ms": round(elapsed * 1000, 2)})
        result["comparison_valid"] = _valid_range_sample(result)
        if result["comparison_valid"]:
            result["throughput_mib_s"] = round(count / elapsed / 1024 / 1024, 3)
        else:
            result["error_code"] = "incomplete_read"
    except Exception as exc:  # diagnostics must complete and classify safely
        status = getattr(exc, "status_code", None)
        if type(status) is int and 100 <= status <= 599:
            result["status"] = status
        result.update({"read_bytes": count, "error_type": type(exc).__name__,
                       "error_code": getattr(exc, "code", None)})
        if opened is not None:
            result["read_ms"] = round(max(time.perf_counter() - opened, 0) * 1000, 2)
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
    return result


def _read_range_family(url: str, start: int, end: int, family: int | None,
                       family_hosts: set[str] | None = None) -> dict[str, Any]:
    """Run one bounded probe with an optional pinned IP family.

    The resolver's normal policy still validates all public addresses.  For
    diagnostics only, the wrapper filters the already-validated list; if a
    family is unavailable it falls back to the normal list and records that in
    the result rather than making an unsafe direct-IP request.
    """
    if family is None:
        return _read_range(url, start, end)
    original = resolver._public_addresses
    selected: list[tuple[str, str, int]] = []
    family_version = 4 if family == socket.AF_INET else 6

    def filtered(host: str):
        addresses = original(host)
        # The share host may be IPv4-only while the redirect target is dual
        # stack.  Leave hosts outside the captured dual-stack target untouched;
        # fail (and report skip) when the target lacks the requested family.
        if family_hosts is not None and host not in family_hosts:
            return addresses
        wanted = [address for address in addresses
                  if ipaddress.ip_address(address).version == family_version]
        if not wanted:
            raise RuntimeError("requested_ip_family_unavailable")
        selected.append((host, wanted[0], 4 if family == socket.AF_INET else 6))
        return wanted

    resolver._public_addresses = filtered
    try:
        result = _read_range(url, start, end)
        result["requested_ip_version"] = family_version
        result["selected_peers"] = [{"host": host, "ip": address,
                                      "ip_version": version}
                                     for host, address, version in selected]
        return result
    except RuntimeError as exc:
        if str(exc) == "requested_ip_family_unavailable":
            return {"start": start, "end": end, "requested_ip_version":
                    family_version,
                    "skipped": "family_unavailable"}
        raise
    finally:
        resolver._public_addresses = original


def _run_redirect_capture(url: str, operation):
    """Capture only host/status/peer metadata while an operation is running."""
    request_original = resolver._request_once
    address_original = resolver._public_addresses
    redirects: list[dict[str, Any]] = []
    peers: list[dict[str, Any]] = []

    def request_capture(target, headers):
        started = time.perf_counter()
        response = request_original(target, headers)
        redirects.append({"host": host_only(target), "status": response.status,
                          "response_host": host_only(response.url),
                          "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)})
        return response

    def address_capture(host):
        addresses = address_original(host)
        peers.append({"host": host, "selected_ip": addresses[0] if addresses else None,
                      "ip_versions": sorted({ipaddress.ip_address(address).version
                                               for address in addresses})})
        return addresses

    resolver._request_once = request_capture
    resolver._public_addresses = address_capture
    try:
        return operation(), redirects, peers
    finally:
        resolver._request_once = request_original
        resolver._public_addresses = address_original


def _curl_probe(url: str, sample_bytes: int) -> dict[str, Any]:
    """Use curl without putting a signed URL in argv, logs, or output JSON."""
    target = tempfile.NamedTemporaryFile(prefix="douyin-curl-", suffix=".bin", delete=False)
    target_path = target.name
    target.close()
    # curl's config parser treats backslashes as escapes on Windows.
    config_target = target_path.replace("\\", "/")
    end = sample_bytes - 1

    def config_quote(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    config = "\n".join((
        "url = " + config_quote(url),
        "header = " + config_quote(f"Range: bytes=0-{end}"),
        "header = " + config_quote("Accept-Encoding: identity"),
        "output = " + config_quote(config_target),
        "write-out = " + config_quote("status=%{http_code} effective=%{url_effective} "
                                      "ttfb=%{time_starttransfer} total=%{time_total} "
                                      "bytes=%{size_download} peer=%{remote_ip} "
                                      "http=%{http_version} content_range=%header{content-range} "
                                      "content_length=%header{content-length}"),
    )) + "\n"
    result: dict[str, Any] = {"client": "curl", "requested_bytes": sample_bytes,
                              "start": 0, "end": end, "read_bytes": 0,
                              "throughput_mib_s": None, "comparison_valid": False}
    started = time.perf_counter()
    try:
        completed = subprocess.run(["curl.exe", "--config", "-", "--silent", "--show-error",
                                    "--max-time", "60", "--connect-timeout", "12", "--location",
                                    "--max-filesize", str(sample_bytes)],
                                   input=config, capture_output=True, text=True, timeout=75)
        metrics = completed.stdout.strip()
        fields = dict(re.findall(r"(status|effective|ttfb|total|bytes|peer|http)=([^ ]*)", metrics))
        header_fields = re.search(r"content_range=(.*?) content_length=([^ ]*)", metrics)
        headers = {"content_range": header_fields.group(1),
                   "content_length": header_fields.group(2)} if header_fields else {}
        if not headers.get("content_length"):
            headers.pop("content_length", None)
        result.update({"exit_code": completed.returncode, "status": int(fields["status"])
                       if fields.get("status", "").isdigit() else None,
                       "ttfb_ms": round(float(fields["ttfb"]) * 1000, 2)
                       if fields.get("ttfb") else None,
                       "wall_ms": round((time.perf_counter() - started) * 1000, 2),
                       "read_bytes": int(float(fields["bytes"])) if fields.get("bytes") else 0,
                       "http_version": fields.get("http"), "peer": fields.get("peer"),
                       "final_host": host_only(fields.get("effective")), "headers": headers})
        total_seconds = float(fields["total"]) if fields.get("total") else 0
        result["range_valid"] = _exact_range_headers(result["status"], headers, 0, end)
        result["comparison_valid"] = completed.returncode == 0 and _valid_range_sample(result)
        if total_seconds and result["comparison_valid"]:
            result["throughput_mib_s"] = round(result["read_bytes"] / total_seconds / 1024 / 1024, 3)
    except Exception as exc:
        result.update({"error_type": type(exc).__name__,
                       "wall_ms": round((time.perf_counter() - started) * 1000, 2)})
    finally:
        try:
            os.unlink(target_path)
        except OSError:
            pass
    return result


def run_bounded(url: str, info: dict[str, Any], repeat: int = 1,
                sample_bytes: int = SAMPLE_BYTES) -> dict[str, Any]:
    formats, chosen, primary = _format_summary(info)
    hosts = []
    for item in formats:
        for host in item["url_hosts"]:
            if host and host not in hosts:
                hosts.append(host)
    operation = lambda: _read_range(primary, 0, sample_bytes - 1)
    first, redirects, peers = _run_redirect_capture(primary, operation)
    report: dict[str, Any] = {"formats": formats, "chosen": chosen,
                              "dns": [dns_probe(host) for host in hosts],
                              "redirects": redirects, "peers": peers, "probes": [first],
                              "parallel_samples": [], "ip_family": {}}

    def stop_restricted(row):
        if _service_restricted(row):
            report.update(stopped="service_restriction", stop_status=row["status"])
            return True
        return False

    report["range_supported"] = _exact_range_headers(
        first.get("status"), first.get("headers", {}), 0, sample_bytes - 1)
    content_range = first.get("headers", {}).get("content_range", "")
    match = re.fullmatch(r"bytes (\d{1,20})-(\d{1,20})/(\d{1,20})", content_range)
    if report["range_supported"] and match:
        report["entity_total_bytes"] = int(match.group(3))
    if stop_restricted(first):
        return report
    # The redirect-capture transfer is already the first measured sample.
    # Reusing it avoids silently doubling traffic for --repeat 1.
    for _ in range(max(1, repeat) - 1):
        row = _read_range(primary, 0, sample_bytes - 1)
        report["probes"].append(row)
        if stop_restricted(row):
            return report
    report["curl"] = _curl_probe(primary, sample_bytes)
    if stop_restricted(report["curl"]):
        return report
    if report["range_supported"]:
        for workers in (1, 2, 4, 8):
            ranges = []
            step = sample_bytes // workers
            for index in range(workers):
                start = index * step
                end = (index + 1) * step - 1 if index < workers - 1 else sample_bytes - 1
                ranges.append((start, end))
            started = time.perf_counter()
            restricted = threading.Event()

            def probe(start, end):
                if restricted.is_set():
                    return {"start": start, "end": end, "read_bytes": 0,
                            "skipped": "service_restriction", "comparison_valid": False}
                row = _read_range(primary, start, end)
                if _service_restricted(row):
                    # In-flight requests can finish; no task may start a new
                    # HTTP probe after the access/rate decision is observed.
                    restricted.set()
                return row

            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(probe, start, end)
                           for start, end in ranges]
                rows = [future.result() for future in as_completed(futures)]
            elapsed = max(time.perf_counter() - started, 1e-9)
            received = sum(row.get("read_bytes", 0) for row in rows
                           if type(row.get("read_bytes")) is int and row["read_bytes"] >= 0)
            complete = len(rows) == workers and all(_valid_range_sample(row) for row in rows)
            report["parallel_samples"].append({
                "connections": workers, "sample_bytes": sample_bytes,
                "read_bytes": received, "comparison_valid": complete,
                "elapsed_ms": round(elapsed * 1000, 2),
                "throughput_mib_s": round(received / elapsed / 1024 / 1024, 3) if complete else None,
                "segments": sorted(rows, key=lambda row: row.get("start", -1)),
            })
            if any(stop_restricted(row) for row in rows):
                return report
    dual_hosts = {peer["host"] for peer in peers
                  if {4, 6}.issubset(set(peer.get("ip_versions", [])))}
    if not dual_hosts:
        return report
    for family_name, family in (("ipv4", socket.AF_INET), ("ipv6", socket.AF_INET6)):
        family_rows = []
        for _ in range(min(max(1, repeat), 3)):
            row = _read_range_family(primary, 0, sample_bytes - 1, family, dual_hosts)
            family_rows.append(row)
            report["ip_family"][family_name] = family_rows
            if stop_restricted(row):
                return report
        # Only keep this expensive family section when the resolver saw a
        # matching dual-stack redirect target.  A skipped row is retained when
        # that target lacks one family so results remain explicit.
        if dual_hosts:
            report["ip_family"][family_name] = family_rows
    return report


def _load_snapshot(snapshot: Path):
    spec = importlib.util.spec_from_file_location("download_worker_baseline_snapshot", snapshot)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load worker snapshot")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _ffprobe(path: Path) -> dict[str, Any]:
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
         "-of", "json", str(path)], capture_output=True, text=True, timeout=120, **flags)
    try:
        data = json.loads(result.stdout)
    except (TypeError, ValueError):
        data = {}
    return {"return_code": result.returncode, "duration": data.get("format", {}).get("duration"),
            "streams": [stream.get("codec_type") for stream in data.get("streams", [])]}


def run_full_baseline(url: str, info: dict[str, Any], output_dir: Path,
                      snapshot: Path | None, prefix_name: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", prefix_name):
        raise ValueError("Benchmark prefix must be a plain file name.")
    output_dir = Path(output_dir)
    # Full benchmarks own a new directory exclusively. An old video or resume
    # checkpoint must never be discarded to make a repeated benchmark fresh.
    try:
        output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise FileExistsError("Full benchmark requires a new, nonexistent --output-dir; "
                              "existing videos and checkpoints were preserved.") from None
    if snapshot is None:
        import core.download_worker as worker
    else:
        worker = _load_snapshot(snapshot)
    _formats, chosen, _primary = _format_summary(info)
    prefix = output_dir / prefix_name
    events: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    transfer_start = None
    transfer_end = None
    started = time.monotonic()

    def send(event: dict[str, Any]) -> None:
        nonlocal transfer_start, transfer_end
        now = time.monotonic()
        safe: dict[str, Any] = {"at_monotonic": now, "kind": event.get("kind"),
                                "phase": event.get("phase"),
                                "stage": str(event.get("stage", ""))[:256],
                                "downloaded_bytes": event.get("downloaded_bytes"),
                                "total_bytes": event.get("total_bytes"),
                                "progress_pct": event.get("progress_pct"),
                                "speed": event.get("speed"), "eta": event.get("eta")}
        events.append(safe)
        if event.get("kind") == "diagnostic":
            diagnostic = {"code": event.get("code"), "error_type": event.get("error_type"),
                          "status_code": event.get("status_code"), "attempt": event.get("attempt"),
                          "downloaded_bytes": event.get("downloaded_bytes")}
            diagnostics.append(diagnostic)
        downloaded = event.get("downloaded_bytes")
        total = event.get("total_bytes")
        if event.get("phase") == "download" and isinstance(downloaded, int):
            if transfer_start is None and downloaded == 0:
                transfer_start = now
            if transfer_start is not None and isinstance(total, int) and total > 0 and downloaded >= total:
                transfer_end = now

    error: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    try:
        result = worker.download_resolved_video(info, str(prefix), send)
    except Exception as exc:
        error = {"type": type(exc).__name__, "code": getattr(exc, "code", None),
                 "message": str(exc)[:256]}
    completed = Path(str(prefix) + ".mp4")
    ended = time.monotonic()
    if transfer_start is None:
        # A failed open can occur before the first progress event; retain the
        # call timing while making the distinction explicit in the report.
        transfer_start = started
    if transfer_end is None and result and completed.is_file():
        transfer_end = ended
    transfer_seconds = (transfer_end - transfer_start) if transfer_end is not None else None
    media: dict[str, Any] = {"path": str(completed), "exists": completed.is_file()}
    if completed.is_file():
        stat = completed.stat()
        media.update({"size_bytes": stat.st_size, "sha256": _sha256(completed),
                      "ffprobe": _ffprobe(completed)})
    source = ROOT / "workspace" / "inputs" / "video_aeb864f59796441a82cb02abba4a7b31.mp4"
    if source.is_file():
        media["source_size_bytes"] = source.stat().st_size
        media["source_sha256"] = _sha256(source)
        media["sha256_matches_existing_source"] = media.get("sha256") == media.get("source_sha256")
    speeds = [float(event["speed"]) for event in events
              if isinstance(event.get("speed"), (int, float)) and event.get("speed") >= 0]
    metrics = result.get("download_metrics") if isinstance(result, dict) else None
    network_metric = None
    disk_metric = None
    if isinstance(metrics, dict):
        # The resolver already reduces transport URLs to scheme+host. Keep the
        # bounded telemetry, which is the authoritative network/disk split for
        # current workers; no arbitrary result fields are serialized here.
        transfer_seconds = metrics.get("elapsed_seconds") if isinstance(
            metrics.get("elapsed_seconds"), (int, float)) else transfer_seconds
        network_metric = metrics.get("network_read_seconds") if isinstance(
            metrics.get("network_read_seconds"), (int, float)) else None
        disk_metric = metrics.get("disk_write_seconds") if isinstance(
            metrics.get("disk_write_seconds"), (int, float)) else None
        strategy = metrics.get("strategy") if isinstance(metrics.get("strategy"), str) else "single-stream"
        connections = metrics.get("connections") if isinstance(metrics.get("connections"), int) else 1
        retry_count_metric = metrics.get("retry_count") if isinstance(metrics.get("retry_count"), int) else len(diagnostics)
        peak_metric = metrics.get("peak_speed") if isinstance(metrics.get("peak_speed"), (int, float)) else 0
    else:
        strategy, connections, retry_count_metric, peak_metric = "single-stream", 1, len(diagnostics), 0
    return {"strategy": strategy, "connection_count": connections, "chosen": chosen,
            "retry_count": retry_count_metric, "diagnostics": diagnostics,
            "peak_mib_per_s": round(max(peak_metric, max(speeds, default=0)) / 1024 / 1024, 3),
            "network_seconds": round(network_metric if network_metric is not None else transfer_seconds, 3)
            if (network_metric is not None or transfer_seconds is not None) else None,
            "disk_seconds": round(disk_metric, 3) if disk_metric is not None else None,
            "download_metrics": metrics,
            "selected_strategy": strategy, "selected_connections": connections,
            "started_monotonic": started, "ended_monotonic": ended,
            "call_wall_seconds": round(ended - started, 3),
            "transfer_wall_seconds": round(transfer_seconds, 3) if transfer_seconds is not None else None,
            "error": error, "result": {key: value for key, value in (result or {}).items()
                                        if key not in {"file_path"}},
            "media": media, "events": events}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--sample-mib", type=int, choices=(8, 16), default=8)
    parser.add_argument("--full-baseline", action="store_true",
                        help="Run one complete transfer using a frozen worker snapshot.")
    parser.add_argument("--full-current", action="store_true",
                        help="Run one complete transfer using the current worker import.")
    parser.add_argument("--audit-dir", type=Path, default=ROOT / "workspace" / "audit")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "workspace" / "temp" / "download_audit" / "baseline_live",
                        help="Full benchmark requires a new, nonexistent directory; existing artifacts are preserved.")
    args = parser.parse_args()
    if (args.full_baseline or args.full_current) and (args.output_dir.exists() or args.output_dir.is_symlink()):
        parser.error("Full benchmark requires a new, nonexistent --output-dir; existing artifacts were preserved.")
    args.audit_dir.mkdir(parents=True, exist_ok=True)
    resolved_at = time.time()
    info = resolver.resolve_douyin(args.url)
    formats, chosen, _primary = _format_summary(info)
    report: dict[str, Any] = {"source_id": resolver._video_id(args.url),
                              "source_host": host_only(args.url), "resolved_at_epoch": resolved_at,
                              "duration_seconds": info.get("duration"), "formats": formats,
                              "chosen": chosen}
    if args.full_baseline or args.full_current:
        if args.full_baseline and args.full_current:
            parser.error("choose only one of --full-baseline or --full-current")
        if args.full_baseline:
            snapshot = args.audit_dir / "download_worker_baseline_snapshot.py"
            source = ROOT / "core" / "download_worker.py"
            if not snapshot.exists():
                shutil.copyfile(source, snapshot)
            report["full_baseline"] = run_full_baseline(
                args.url, info, args.output_dir, snapshot, "video_baseline_live")
            report["worker_snapshot"] = str(snapshot)
        else:
            report["full_current"] = run_full_baseline(
                args.url, info, args.output_dir, None, "video_current_live")
    else:
        report["bounded"] = run_bounded(args.url, info, args.repeat, args.sample_mib * 1024 * 1024)
    report["notes"] = ["Signed source URLs are kept in memory only; output stores hosts and fixed metrics.",
                        "All clocks used for transfer timing are monotonic."]
    stamp = time.strftime("%Y%m%d_%H%M%S")
    output = args.audit_dir / f"diagnose_douyin_{stamp}.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # Avoid non-ASCII console output on Windows cp1252 consoles.
    print(str(output.resolve()).encode("ascii", "backslashreplace").decode("ascii"))
    full = report.get("full_baseline") or report.get("full_current") or {}
    return 0 if not full.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
