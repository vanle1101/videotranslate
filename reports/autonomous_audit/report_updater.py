"""Bounded, read-only audit reporter; never runs a pipeline or repairs source.

Reads the 15-second watchdog and runner runtime_events.jsonl. Writes report
artifacts every 60 seconds. Optional localhost HTTP server exposes only the
dashboard and sanitized audit JSON; it has no mutation routes.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import sys
import threading
import time
import uuid

from review_log_telemetry import review_telemetry

try:
    import psutil
except ImportError:
    psutil = None

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
VIDEO_IDS = {"SHORT": "7692745161054506290", "LONG": "7666381909177470271"}
STAGES = ["download", "ASR", "translation", "segmentation", "TTS", "alignment", "mix", "export", "verification"]
SCALAR_FIELDS = {"run_id", "video_label", "video_id", "task_id", "stage", "status", "progress_pct",
                 "processed_seconds", "total_seconds", "elapsed_seconds", "eta_seconds", "output_filename",
                 "started_at", "completed_at", "retry_count", "completed_chunks", "total_chunks", "retry_chunks",
                 "completed_segments", "total_segments", "stage_progress_pct", "phase", "source_prepared_seconds", "current_segment", "review_scope"}
SAFE_DETAIL_FIELDS = {"summary", "reason", "error_type", "component", "severity", "stage", "file", "line",
                      "elapsed_seconds", "duration_seconds", "video_stream", "audio_stream", "subtitles",
                      "duration_valid", "picture_valid", "audio_valid", "subtitles_valid", "sync_valid",
                      "semantic_valid", "subtitle_coverage_valid", "output_path", "evidence_path",
                      "video_frames", "audio_samples", "subtitle_count", "missing_segments", "failed_segments"}
PUBLIC_FILES = {"dashboard.html", "live_progress.json", "error_history.json", "benchmark_results.json", "test_results.json", "final_report.md", "fixes_applied.md", "historical_log_audit.json"}


def now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def sanitize(value):
    text = str(value)
    text = re.sub(r"(?i)((?:api[_-]?key|authorization|password|secret|token|cookie)\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)bearer\s+[^\s,;]+", "Bearer [REDACTED]", text)
    text = re.sub(r"(https?://[^\s?#]+)[?#][^\s]+", r"\1", text)
    return text[:1600]


def safe_scalars(value, fields):
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in fields:
        item = value.get(key)
        if isinstance(item, str):
            result[key] = sanitize(item)
        elif item is None or isinstance(item, (bool, int)) or isinstance(item, float) and math.isfinite(item):
            if key in value:
                result[key] = item
    return result


def numeric_dict(value):
    if not isinstance(value, dict):
        return {}
    return {sanitize(key): item for key, item in list(value.items())[:60]
            if isinstance(item, (bool, int)) or isinstance(item, float) and math.isfinite(item)}


def read_json(path, default=None, limit=20 * 1024 * 1024):
    try:
        if path.stat().st_size > limit:
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)


def write_text(path, content):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def seconds_since(value):
    try:
        return max(0, time.time() - dt.datetime.fromisoformat(value).timestamp())
    except (ValueError, TypeError):
        return None


class Reporter:
    def __init__(self, root=ROOT, output=HERE, interval=60):
        self.root, self.output, self.interval = root.resolve(), output.resolve(), interval
        self.output.mkdir(parents=True, exist_ok=True)
        self.observer = self.root / "workspace" / "audit" / "continuous_monitor"
        self.original = self.root / "workspace" / "audit" / "continuous-monitor"
        self.event_file = self.output / "runtime_events.jsonl"
        initial = {"started_at": now(), "jobs": {label: {"video_label": label, "video_id": identifier,
                    "status": "WAITING", "stage": None, "progress_pct": None, "eta_seconds": None,
                    "actual_output_verified": False, "verification": {}} for label, identifier in VIDEO_IDS.items()},
                   "tests": {}, "fixes": {}, "runtime_errors": {}, "event_ids": [], "event_offset": 0,
                   "event_identity": None, "samples": 0, "benchmarks": [], "resource_samples": [],
                   "closed_group_ids": [], "verification_requests": [], "last_io": {}, "last_event_at": None,
                   "blocked_reasons": [], "warnings": [], "modified_files": [], "resolved_blocker_ids": [], "diagnoses": {}}
        self.state = read_json(self.output / "_reporter_state.json", initial)
        for key, value in initial.items():
            self.state.setdefault(key, value)
        self.sample_run = uuid.uuid4().hex[:12]

    def events(self):
        try:
            stat = self.event_file.stat()
            identity = [stat.st_dev, stat.st_ino]
            offset = self.state["event_offset"]
            if identity != self.state["event_identity"] or stat.st_size < offset:
                offset = 0
            self.state["event_identity"] = identity
            with self.event_file.open("rb") as stream:
                stream.seek(offset)
                blob = stream.read(8 * 1024 * 1024)
            complete = blob.rfind(b"\n") + 1
            self.state["event_offset"] = offset + complete
            for raw in blob[:complete].splitlines():
                if len(raw) > 1024 * 1024:
                    self.state["warnings"].append("Oversize runner event skipped; limit 1 MiB")
                    continue
                try:
                    event = json.loads(raw.decode("utf-8"))
                    if not isinstance(event, dict):
                        raise ValueError("Event is not an object")
                except (ValueError, UnicodeDecodeError):
                    self.state["warnings"].append("Invalid complete runner event skipped; raw contents omitted")
                    continue
                identifier = str(event.get("event_id") or hashlib.sha256(raw).hexdigest())
                if identifier in self.state["event_ids"]:
                    continue
                self.state["event_ids"].append(identifier)
                self.state["event_ids"] = self.state["event_ids"][-30000:]
                self.consume(event, identifier)
            self.state["warnings"] = list(dict.fromkeys(self.state["warnings"]))[-20:]
        except FileNotFoundError:
            pass
        except OSError as error:
            self.state["warnings"].append("Runtime event read unavailable: " + type(error).__name__)

    def consume(self, event, identifier):
        kind = str(event.get("event", event.get("type", "PROGRESS"))).upper().replace("-", "_")
        stamp = event.get("timestamp", event.get("at", now()))
        if isinstance(stamp, (int, float)):
            stamp = dt.datetime.fromtimestamp(stamp).astimezone().isoformat(timespec="seconds")
        self.state["last_event_at"] = sanitize(stamp)
        label = str(event.get("video_label", event.get("label", ""))).upper()
        if label not in VIDEO_IDS:
            label = next((name for name, video in VIDEO_IDS.items() if str(event.get("video_id")) == video), "")
        job = self.state["jobs"].get(label)
        progress = event.get("progress") if isinstance(event.get("progress"), dict) else {}
        if job:
            job.update(safe_scalars(event, SCALAR_FIELDS))
            job.update(safe_scalars(progress, SCALAR_FIELDS))
            job["last_event_at"] = sanitize(stamp)
            if kind in {"RUN_STARTED", "JOB_STARTED", "VIDEO_STARTED"}:
                job["started_at"] = sanitize(stamp)
                job["status"] = event.get("status", "RUNNING")
            if kind in {"RUN_COMPLETED", "JOB_COMPLETED", "VIDEO_COMPLETED", "PIPELINE_COMPLETED"}:
                job["completed_at"] = sanitize(stamp)
                job["status"] = event.get("status", "COMPLETED_UNVERIFIED")
            for field in ("ai_queue", "segment_states"):
                if field in event or field in progress:
                    job[field] = numeric_dict(event.get(field, progress.get(field)))
            summary = event.get("review_summary", progress.get("review_summary"))
            if isinstance(summary, dict):
                job["review_summary"] = numeric_dict(summary)
                if isinstance(summary.get("status"), str):
                    job["review_summary"]["status"] = sanitize(summary["status"])
            segment_ids = event.get("reviewing_segment_ids", progress.get("reviewing_segment_ids"))
            if isinstance(segment_ids, list):
                job["reviewing_segment_ids"] = [value for value in segment_ids[:20000] if type(value) is int]
        if kind in {"ERROR", "INCIDENT", "FAILED", "BLOCKED"}:
            error = safe_scalars(event, {"error_type", "component", "stage", "severity", "task_id", "video_id", "video_label", "status", "summary", "reason"})
            error.update(id=identifier, event_at=sanitize(stamp), status="OPEN_UNVERIFIED", source="runner")
            error["details"] = safe_scalars(event.get("details"), SAFE_DETAIL_FIELDS)
            self.state["runtime_errors"][identifier] = error
            if kind == "BLOCKED":
                self.state["blocked_reasons"].append(error)
        if kind == "BLOCKER_RESOLVED":
            # Explicit recovery evidence explains an external limit; it is not a code fix.
            reference = event.get("evidence_path")
            if reference and Path(str(reference)).is_file():
                self.state["resolved_blocker_ids"].extend(str(item) for item in event.get("incident_ids", []))
        if kind in {"TEST_RESULT", "TEST_COMPLETED"}:
            name = sanitize(event.get("test_name", event.get("name", identifier)))
            key = str(event.get("test_id", name))
            self.state["tests"][key] = {"test_id": key, "test_name": name,
                "test_kind": sanitize(event.get("test_kind", "unspecified")),
                "result": str(event.get("result", event.get("status", "UNKNOWN"))).upper(),
                "at": sanitize(stamp), "run_id": sanitize(event.get("run_id", "")),
                "duration_seconds": event.get("duration_seconds"),
                "evidence_path": sanitize(event.get("evidence_path", "")),
                "video_label": label or None, "details": safe_scalars(event.get("details"), SAFE_DETAIL_FIELDS)}
        if kind in {"FIX_APPLIED", "SOURCE_CHANGED", "CONFIG_CHANGED"}:
            fix_id = sanitize(event.get("fix_id", identifier))
            files = [sanitize(item) for item in event.get("files", []) if isinstance(item, str)]
            if event.get("file"):
                files.append(sanitize(event["file"]))
            self.state["modified_files"] = sorted(set(self.state["modified_files"] + files))
            self.state["fixes"][fix_id] = {"fix_id": fix_id, "applied_at": sanitize(stamp),
                "status": "APPLIED_UNVERIFIED", "files": files,
                "summary": sanitize(event.get("summary", "Source/config change recorded")),
                "incident_ids": [sanitize(item) for item in event.get("incident_ids", [])], "test_refs": []}
        if kind in {"FIX_VERIFIED", "FIX_VERIFY"}:
            self.state["verification_requests"].append({"fix_id": sanitize(event.get("fix_id", "")),
                "result": str(event.get("result", event.get("status", "UNKNOWN"))).upper(),
                "test_refs": [sanitize(item) for item in event.get("test_refs", [])],
                "incident_ids": [sanitize(item) for item in event.get("incident_ids", [])], "at": sanitize(stamp)})
        if kind in {"FIX_ROLLED_BACK", "ROLLBACK"}:
            fix = self.state["fixes"].get(str(event.get("fix_id")))
            if fix:
                fix["status"] = "ROLLED_BACK"
        if kind == "DIAGNOSIS":
            for group_id in event.get("incident_ids", []):
                reference = event.get("evidence_path")
                if isinstance(group_id, str) and reference and Path(str(reference)).is_file():
                    self.state["diagnoses"][group_id] = {
                        "root_cause": sanitize(event.get("root_cause", event.get("summary", ""))),
                        "evidence_path": sanitize(reference), "at": sanitize(stamp),
                        "verification_scope": sanitize(event.get("verification_scope", "diagnostic evidence")),
                    }
        if kind == "FIX_TESTED":
            fix = self.state["fixes"].get(str(event.get("fix_id")))
            refs = [sanitize(item) for item in event.get("test_refs", [])]
            tests = [self.state["tests"].get(ref) for ref in refs]
            if fix and refs and all(test and test["result"] in {"PASS", "PASSED"} for test in tests):
                fix["isolated_test_status"] = "PASS"
                fix["isolated_test_refs"] = refs
                fix["runtime_verification"] = "PENDING"
        if kind in {"OUTPUT_VERIFIED", "MEDIA_VERIFICATION", "OUTPUT_VERIFICATION"} and job:
            details = safe_scalars(event.get("details", event.get("verification", {})), SAFE_DETAIL_FIELDS)
            for field in SAFE_DETAIL_FIELDS:
                if field in event:
                    details.update(safe_scalars(event, {field}))
            job["verification"] = details
            required = ("picture_valid", "audio_valid", "subtitles_valid", "duration_valid", "sync_valid")
            evidence = details.get("evidence_path", event.get("evidence_path"))
            evidence_exists = bool(evidence and Path(str(evidence)).is_file())
            job["actual_output_verified"] = bool(all(details.get(field) is True for field in required) and evidence_exists)
            job["verification_at"] = sanitize(stamp)
            job["verification_evidence_path"] = sanitize(evidence or "")
        if kind in {"BENCHMARK_RESULT", "BENCHMARK"}:
            self.state["benchmarks"].append({"at": sanitize(stamp), "video_label": label or None,
                "run_id": sanitize(event.get("run_id", "")), "metrics": numeric_dict(event.get("metrics")),
                "summary": sanitize(event.get("summary", ""))})
            self.state["benchmarks"] = self.state["benchmarks"][-1000:]

    def verified_fixes(self):
        self.state["closed_group_ids"] = []
        for request in self.state["verification_requests"]:
            fix = self.state["fixes"].get(request["fix_id"])
            if not fix or fix["status"] == "ROLLED_BACK":
                continue
            refs = request["test_refs"]
            tests = [self.state["tests"].get(ref) for ref in refs]
            success = request["result"] in {"PASS", "PASSED", "VERIFIED"}
            # A claimed verification without linked passing tests cannot close a fix.
            if success and refs and all(test and test["result"] in {"PASS", "PASSED"} for test in tests):
                fix.update(status="VERIFIED_FIXED", verified_at=request["at"], test_refs=refs)
                self.state["closed_group_ids"].extend(request["incident_ids"] or fix["incident_ids"])
            elif fix["status"] == "VERIFIED_FIXED":
                fix["status"] = "VERIFICATION_REGRESSED"
        self.state["closed_group_ids"] = sorted(set(self.state["closed_group_ids"]))

    def resource_details(self, latest):
        data = {"io_available": psutil is not None, "handle_count": None, "open_fd_count": None,
                "read_bytes": None, "write_bytes": None, "read_bytes_per_second": None, "write_bytes_per_second": None}
        if not psutil:
            return data
        current = {}
        handles = fds = read = written = 0
        observed = time.monotonic()
        rates = [0.0, 0.0]
        comparable = False
        for row in latest.get("processes", {}).get("processes", []):
            try:
                process = psutil.Process(row["pid"])
                identity = str(process.pid) + ":" + str(process.create_time())
                io = process.io_counters()
                handles += process.num_handles() if hasattr(process, "num_handles") else 0
                fds += process.num_fds() if hasattr(process, "num_fds") else 0
                read += io.read_bytes
                written += io.write_bytes
                previous = self.state["last_io"].get(identity)
                if previous and observed > previous[2]:
                    gap = observed - previous[2]
                    rates[0] += max(0, io.read_bytes - previous[0]) / gap
                    rates[1] += max(0, io.write_bytes - previous[1]) / gap
                    comparable = True
                current[identity] = [io.read_bytes, io.write_bytes, observed]
            except (psutil.Error, OSError, KeyError):
                continue
        self.state["last_io"] = current
        if current:
            data.update(handle_count=handles if os.name == "nt" else None,
                        open_fd_count=fds if os.name != "nt" else None,
                        read_bytes=read, write_bytes=written,
                        read_bytes_per_second=round(rates[0], 2) if comparable else None,
                        write_bytes_per_second=round(rates[1], 2) if comparable else None)
        return data

    def history(self, initial, observed):
        groups = []
        closed = set(self.state["closed_group_ids"])
        for original in initial.get("incidents", []):
            group = safe_scalars(original, {"id", "severity", "component", "stage", "status", "suspected_cause",
                                           "first_log_at", "last_log_at", "evidence", "occurrences", "verification"})
            group["task_ids"] = [sanitize(item) for item in original.get("task_ids", [])]
            group["status"] = "VERIFIED_FIXED" if original.get("id") in closed else original.get("status", "OPEN_UNVERIFIED")
            diagnosis = self.state["diagnoses"].get(original.get("id"))
            if diagnosis:
                group.update(diagnosis)
                group["suspected_cause"] = diagnosis["root_cause"]
            groups.append(group)
        incidents = []
        for original in observed.get("incidents", []):
            row = safe_scalars(original, {"key", "severity", "component", "stage", "task_id", "error_type", "first_event_at", "last_event_at", "count", "status"})
            row["source"] = original.get("source")
            # Raw transcripts/full provider payloads are never copied to the dashboard.
            row["evidence_reference"] = str(self.observer / "incidents.json")
            mapped = "SOURCE_PREP_FAILURE" if original.get("error_type") in {"SOURCE_RETRY_PENDING", "MANIFEST_FAILED"} else original.get("key")
            row["group_id"] = mapped
            row["status"] = "VERIFIED_FIXED" if mapped in closed else row.get("status", "OPEN_UNVERIFIED")
            incidents.append(row)
            if mapped not in {group.get("id") for group in groups}:
                groups.append({"id": mapped, "severity": row.get("severity", "High"),
                               "component": row.get("component"), "stage": row.get("stage"),
                               "status": row["status"], "suspected_cause": row.get("error_type"), "task_ids": [row.get("task_id")]})
        runtime = []
        for original in self.state["runtime_errors"].values():
            row = dict(original)
            if row["id"] in self.state["resolved_blocker_ids"]:
                row["status"] = "EXTERNAL_RECOVERED_WITH_EVIDENCE"
            elif row["id"] in closed:
                row["status"] = "VERIFIED_FIXED"
            runtime.append(row)
        log_occurrences = sum(row.get("count", 0) for row in incidents if row.get("error_type") == "SOURCE_RETRY_PENDING" or str(row.get("source", "")).startswith("log:"))
        return {"updated_at": now(), "initial_evidence_reference": str(self.original / "initial-findings.json"),
                "groups": groups, "task_incidents": incidents, "runner_incidents": runtime,
                "counts": {"diagnostic_groups": len(groups), "task_incident_keys": len(incidents),
                           "logged_failure_occurrences": log_occurrences, "runner_incident_events": len(runtime),
                           "verified_fixed_groups": sum(row["status"] == "VERIFIED_FIXED" for row in groups),
                           "unresolved_groups": sum(row["status"] != "VERIFIED_FIXED" for row in groups)},
                "count_policy": "Diagnostic groups, per-task incident keys and logged occurrences are different counts; manifest observations are not new failure events"}

    def update(self):
        self.events()
        self.verified_fixes()
        latest = read_json(self.observer / "latest.json", {})
        initial = read_json(self.original / "initial-findings.json", {})
        observed = read_json(self.observer / "incidents.json", {})
        self.state["samples"] += 1
        resources = self.resource_details(latest)
        processes = latest.get("processes", {})
        system = latest.get("system", {})
        resources.update(cpu_percent=processes.get("cpu_total_percent"), rss_bytes=processes.get("rss_total_bytes"),
                         system_cpu_percent=system.get("cpu_percent"), system_ram_available_bytes=system.get("ram_available_bytes"),
                         system_ram_used_percent=system.get("ram_used_percent"), disk=system.get("project_disk"), gpu=system.get("gpu"))
        age = seconds_since(latest.get("observed_at"))
        stale = age is None or age > max(90, self.interval * 2)
        resources["watchdog_stale"] = stale
        resources["watchdog_age_seconds"] = round(age, 2) if age is not None else None
        sample = {"at": now(), **{key: value for key, value in resources.items() if isinstance(value, (int, float)) and not isinstance(value, bool)}}
        self.state["resource_samples"].append(sample)
        self.state["resource_samples"] = self.state["resource_samples"][-2000:]
        jobs = []
        tasks = {task.get("task_id"): task for task in latest.get("tasks", []) if task.get("task_id")}
        for label in VIDEO_IDS:
            job = dict(self.state["jobs"][label])
            manifest = tasks.get(job.get("task_id"))
            if manifest:
                job["manifest_observation"] = {key: manifest.get(key) for key in ("state", "updated_at", "segment_count", "segment_statuses", "source_prepared_seconds", "total_duration_seconds", "output_filename", "output_exists", "output_size_bytes")}
            job["event_age_seconds"] = seconds_since(job.get("last_event_at"))
            if job.get("started_at") and job.get("elapsed_seconds") is None:
                job["elapsed_seconds"] = seconds_since(job["started_at"])
            if not isinstance(job.get("eta_seconds"), (int, float)):
                job["eta_seconds"] = None
            job["progress_source"] = "runner event" if job.get("last_event_at") else "No ordered runner event yet"
            if job.get("task_id"):
                job["review_substages"] = review_telemetry(self.root / "workspace" / "logs" / "ai.log",
                    job["task_id"], since=job.get("started_at") or self.state["started_at"],
                    total_segments=manifest.get("segment_count") if manifest else None,
                    summary=job.get("review_summary"))
            jobs.append(job)
        errors = self.history(initial, observed)
        historical = read_json(self.output / "historical_log_audit.json", {})
        if historical:
            errors["complete_historical_log"] = {
                "counts": historical.get("counts", {}),
                "evidence_path": str(self.output / "historical_log_audit.json"),
                "scope": historical.get("scope"), "limitation": historical.get("limitation"),
            }
        tests = list(self.state["tests"].values())
        test_counts = dict(collections.Counter(test["result"] for test in tests))
        all_tests_pass = bool(tests) and all(test["result"] in {"PASS", "PASSED"} for test in tests)
        terminal_states = {"COMPLETED", "COMPLETED_UNVERIFIED", "VERIFIED COMPLETED", "VERIFIED_COMPLETED"}
        all_outputs_verified = all(job.get("actual_output_verified") is True and str(job.get("status", "")).upper() in terminal_states for job in jobs)
        no_open_groups = all(group.get("status") == "VERIFIED_FIXED" for group in errors["groups"])
        no_open_runtime = all(row.get("status") in {"VERIFIED_FIXED", "EXTERNAL_RECOVERED_WITH_EVIDENCE"} for row in errors["runner_incidents"])
        unresolved_blockers = [row for row in self.state["blocked_reasons"] if row["id"] not in self.state["resolved_blocker_ids"]]
        short, long = jobs
        ordered_proven = bool(short.get("verification_at") and long.get("started_at") and str(short["verification_at"]) <= str(long["started_at"]))
        verified = all_outputs_verified and all_tests_pass and no_open_groups and no_open_runtime and ordered_proven and not unresolved_blockers
        status = "VERIFIED COMPLETED" if verified else "BLOCKED" if unresolved_blockers else "PARTIALLY FIXED"
        live = {"updated_at": now(), "reporter_pid": os.getpid(), "reporter_run_id": self.sample_run,
                "report_samples": self.state["samples"], "report_interval_seconds": self.interval,
                "audit_started_at": self.state["started_at"], "elapsed_audit_seconds": seconds_since(self.state["started_at"]),
                "authorization": {"immediate_repairs_authorized": True, "inactivity_gate_enforced": False,
                    "source": "Latest user instruction cancels 30-minute gate; ordered real SHORT then LONG validation authorized",
                    "watchdog_input_data": latest.get("user_inactivity"), "input_gate_role": "Informational only"},
                "ordered_runs": jobs, "ordered_run_ids": VIDEO_IDS, "stages": STAGES,
                "resources": resources, "health": latest.get("health", {}), "API_telemetry": latest.get("API_telemetry", {}),
                "counts": errors["counts"], "test_counts": test_counts,
                "historical_error_counts": historical.get("counts", {}),
                "fix_counts": dict(collections.Counter(fix["status"] for fix in self.state["fixes"].values())),
                "status": status, "verified_completed": verified,
                "notes": ["Reporter collects evidence; it never changes source, restarts services or calls task routes",
                          "No ETA is invented; missing runner telemetry remains null",
                          "Output verification requires measured picture/audio/subtitles/duration/sync checks and an evidence file",
                          "Original frontend already shows stage, measured progress, download speed/ETA, segment/retry/chunk counts, AI queue and pipeline ETA",
                          "Frontend lacks host I/O/handle metrics and the ordered audit comparison; this separate dashboard supplies available metrics"],
                "warnings": self.state["warnings"], "blocked_reasons": unresolved_blockers,
                "acceptance_gates": {"outputs_verified_and_terminal": all_outputs_verified,
                    "all_recorded_tests_pass": all_tests_pass, "all_diagnostic_groups_verified_fixed": no_open_groups,
                    "no_unresolved_runner_incidents": no_open_runtime, "ordered_short_then_long_proven": ordered_proven},
                "evidence_references": {"original_findings": str(self.original / "initial-findings.json"),
                    "preexisting_patch": str(self.original / "preexisting-source-changes.patch"),
                    "source_baseline_hashes": str(self.original / "source-baseline-hashes.json"),
                    "initial_manifests": str(self.original / "initial-evidence"), "runtime_events": str(self.event_file)},
                "modified_source_files": self.state["modified_files"]}
        benchmarks = {"updated_at": now(), "resource_sample_count": len(self.state["resource_samples"]),
                      "samples": self.state["resource_samples"], "runner_benchmarks": self.state["benchmarks"],
                      "peaks": {key: max((row[key] for row in self.state["resource_samples"] if isinstance(row.get(key), (int, float))), default=None)
                                for key in ("cpu_percent", "rss_bytes", "handle_count", "read_bytes_per_second", "write_bytes_per_second")},
                      "comparison": "No before/after conclusion until comparable measured runner benchmarks exist"}
        write_json(self.output / "live_progress.json", live)
        write_json(self.output / "error_history.json", errors)
        write_json(self.output / "benchmark_results.json", benchmarks)
        write_json(self.output / "test_results.json", {"updated_at": now(), "counts": test_counts, "tests": tests,
                    "all_recorded_tests_pass": all_tests_pass, "note": "No test result is fabricated or inferred from pipeline DONE"})
        self.documents(live, errors, tests, benchmarks)
        write_json(self.output / "_reporter_state.json", self.state)
        return live

    def documents(self, live, errors, tests, benchmarks):
        fixes = ["# Applied fixes", "", "Updated: " + live["updated_at"], "",
                 "Source/config repairs are authorized immediately. The inactivity gate is informational.",
                 "A fix remains unverified until explicit verification references passing recorded tests.", ""]
        if not self.state["fixes"]:
            fixes += ["No applied fix has been recorded by the runner yet.", ""]
        for fix in self.state["fixes"].values():
            fixes += [f"- **{fix['fix_id']} — {fix['status']}**: {fix['summary']}",
                      "  Files: " + ", ".join(fix["files"] or ["(not supplied)"]),
                      "  Linked tests: " + ", ".join(fix["test_refs"] or ["(none)"])]
            if fix.get("isolated_test_status"):
                fixes += ["  Isolated tests: " + fix["isolated_test_status"] + "; production runtime verification: "
                          + fix.get("runtime_verification", "PENDING") + "."]
        write_text(self.output / "fixes_applied.md", "\n".join(fixes) + "\n")
        report = ["# Autonomous audit — current evidence", "", "Updated: " + live["updated_at"],
                  "Status: **" + live["status"] + "**", "",
                  f"Observed audit elapsed: {live['elapsed_audit_seconds']:.1f} seconds. Actual video processing durations below come from runner events.",
                  f"Diagnostic groups: {errors['counts']['diagnostic_groups']}; verified fixed: {errors['counts']['verified_fixed_groups']}; unresolved: {errors['counts']['unresolved_groups']}.",
                  f"Per-task incident keys: {errors['counts']['task_incident_keys']}; logged failure occurrences: {errors['counts']['logged_failure_occurrences']}; runner incident events: {errors['counts']['runner_incident_events']}.", "",
                  "| Ordered video | State / stage | Progress | Elapsed / ETA (s) | Output independently verified |",
                  "| --- | --- | --- | --- | --- |"]
        for job in live["ordered_runs"]:
            report.append(f"| {job['video_label']} {job['video_id']} | {job.get('status')} / {job.get('stage')} | {job.get('progress_pct')} | {job.get('elapsed_seconds')} / {job.get('eta_seconds')} | {job.get('actual_output_verified')} |")
        historical = live.get("historical_error_counts", {})
        if historical:
            report += ["", f"Complete historical errors.log: {historical.get('all_error_occurrences')} occurrences; "
                       f"{historical.get('intentional_test_fault_occurrences')} intentional test faults; "
                       f"{historical.get('historical_runtime_occurrences')} historical runtime failures.",
                       "These occurrences are not a count of current failed tasks. Evidence: historical_log_audit.json."]
        report += ["", "Root causes / important diagnostic groups:", ""]
        for group in errors["groups"]:
            report.append(f"- {group.get('severity')} **{group.get('id')}** ({group.get('status')}): {group.get('suspected_cause', '(not established)')}")
            if group.get("evidence_path"):
                report.append("  Diagnostic verification scope: " + group.get("verification_scope", "unspecified")
                              + "; evidence: " + group["evidence_path"])
        report += ["", "Modified source files:", ""]
        report += ["- " + path for path in live["modified_source_files"]] or ["- No source modification event recorded."]
        report += ["", "Recorded actual tests:", ""]
        report += [f"- {test['test_kind']}: {test['test_name']} — {test['result']} ({test.get('duration_seconds')} seconds); evidence: {test.get('evidence_path') or '(not supplied)'}" for test in tests] or ["- No runner test result recorded; no pass claimed."]
        report += ["", "Remaining limitations:", ""] + ["- " + item for item in live["notes"]]
        report += ["- Health sample stale: " + str(live["resources"]["watchdog_stale"]),
                   "- Resource samples: " + str(benchmarks["resource_sample_count"]) + "; no optimization benefit claimed before measured comparison.",
                   "- External API/network restrictions are recorded only when an actual runner event supplies evidence.", "",
                   "Evidence and backup references:", ""] + ["- " + key + ": " + path for key, path in live["evidence_references"].items()]
        write_text(self.output / "final_report.md", "\n".join(report) + "\n")


def server(output, port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            name = self.path.split("?", 1)[0].lstrip("/") or "dashboard.html"
            if name not in PUBLIC_FILES:
                self.send_error(404)
                return
            try:
                payload = (output / name).read_bytes()
            except OSError:
                self.send_error(404)
                return
            kind = "text/html" if name.endswith(".html") else "application/json" if name.endswith(".json") else "text/plain"
            self.send_response(200)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_):
            pass
    instance = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=instance.serve_forever, daemon=True).start()
    return instance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=ROOT)
    parser.add_argument("--output-dir", type=Path, default=HERE)
    parser.add_argument("--interval", type=float, default=60)
    parser.add_argument("--max-runtime-seconds", type=float, default=86400)
    parser.add_argument("--serve", type=int, help="Start dashboard on this localhost TCP port")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not 15 <= args.interval <= 60:
        parser.error("interval must be 15..60 seconds")
    reporter = Reporter(args.project, args.output_dir, args.interval)
    http = server(reporter.output, args.serve) if args.serve else None
    started = time.monotonic()
    try:
        while True:
            before = time.monotonic()
            try:
                live = reporter.update()
                print(json.dumps({"at": live["updated_at"], "sample": live["report_samples"], "status": live["status"],
                                  "watchdog_stale": live["resources"]["watchdog_stale"], "tests": live["test_counts"]}), flush=True)
            except Exception as error:
                with (reporter.output / "reporter_errors.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"at": now(), "error": type(error).__name__, "summary": sanitize(error)}) + "\n")
                if args.once:
                    return 1
            if args.once or args.max_runtime_seconds > 0 and time.monotonic() - started >= args.max_runtime_seconds:
                break
            time.sleep(max(0, args.interval - (time.monotonic() - before)))
    except KeyboardInterrupt:
        pass
    finally:
        if http:
            http.shutdown()
            http.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
