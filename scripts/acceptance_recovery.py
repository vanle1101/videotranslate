"""Audit/recover explicit saved IDs through one already-running local backend.

Default is read-only planning. --execute authorizes retry/full continuation and
strict full export; no project/cache rewrites or provider selection occur here.
The backend retains ownership if the runner is interrupted or times out.
"""
import argparse
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit, parse_qs, unquote

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TERMINAL = {"COMPLETED", "PREPARED", "PREVIEW_READY", "FAILED", "STOPPED", "CANCELLED"}
MANUAL_FIELDS = ("id", "start", "end", "final_vi", "revision", "confirmed_silence", "speaker_confirmation", "speaker_id", "speaker_evidence")


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def local_backend(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"} or not parsed.port):
        raise ValueError("Supply an explicit local HTTP backend URL with its current port")
    return value.rstrip("/")


def manual_rows(rows):
    return {str(row["id"]): {key: row.get(key) for key in MANUAL_FIELDS}
            for row in rows if (row.get("verification") or {}).get("status") == "manual"
            or row.get("speaker_confirmation")}


def verify_manual(before, rows):
    current = {str(row["id"]): row for row in rows}
    changed = [key for key, row in before.items() if key not in current or
               any(current[key].get(field) != row[field] for field in MANUAL_FIELDS)]
    if changed:
        raise RuntimeError("Manual rows changed or disappeared: " + ", ".join(changed))


def verify_source_url(value, source, input_dir):
    parsed = urlsplit(value or "")
    if parsed.scheme or parsed.netloc:
        raise RuntimeError("Backend source must use its local media route")
    if parsed.path == "/api/local-file":
        candidate = parse_qs(parsed.query).get("path", [None])[0]
        matches = candidate and Path(candidate).resolve() == source.resolve()
    else:
        matches = (source.resolve().parent == Path(input_dir).resolve()
                   and unquote(parsed.path) == f"/api/inputs/{source.name}" and not parsed.query)
    if not matches:
        raise RuntimeError("Backend source does not match the backed-up local project")


@contextmanager
def project_lock(directory, task_id):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{task_id}.lock"
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps({"pid": os.getpid(), "started_at": time.time()}))
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


async def api(client, method, route, **kwargs):
    # A dropped polling connection is not a terminal backend state. Retry
    # observations only; mutations need an explicit state check before replay.
    for attempt in range(6):
        try:
            response = await client.request(method, route, **kwargs)
            break
        except httpx.TransportError:
            if method.upper() != "GET" or attempt == 5:
                raise
            await asyncio.sleep(min(2 ** attempt, 30))
    if response.status_code >= 400:
        raise RuntimeError(f"{method} {route}: HTTP {response.status_code}: {response.text[:1000]}")
    return response.json()


def processing(snapshot):
    progress = snapshot.get("progress", {})
    return (progress.get("status") not in TERMINAL or progress.get("can_stop") is True
            or progress.get("can_pause") is True or progress.get("review_summary", {}).get("status") == "running")


async def wait_idle(client, task_id, manual, folder, deadline, poll):
    stable = 0
    while time.monotonic() < deadline:
        snapshot = await api(client, "GET", f"/api/streaming/{task_id}")
        verify_manual(manual, snapshot["segments"])
        progress = snapshot.get("progress", {})
        with (folder / "progress.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"observed_at": time.time(), "progress": progress}, ensure_ascii=False) + "\n")
        stable = 0 if processing(snapshot) else stable + 1
        if stable >= 2:
            return snapshot
        await asyncio.sleep(poll)
    raise TimeoutError("Recovery observation deadline reached; backend still owns its task. No stop/reset was sent.")


async def wait_export(client, task_id, folder, deadline, poll):
    while time.monotonic() < deadline:
        result = await api(client, "GET", f"/api/streaming/export-hq/status/{task_id}")
        write_json(folder / "export-status.json", result)
        if result["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            if result["status"] != "COMPLETED":
                raise RuntimeError("Export did not complete: " + result.get("stage", result["status"]))
            return result
        await asyncio.sleep(poll)
    raise TimeoutError("Export observation deadline reached; render remains owned by backend.")


def validate_media(path, duration, folder):
    metadata = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
        "format=duration,size:stream=codec_type,codec_name,width,height,sample_rate", "-of", "json", str(path)],
        capture_output=True, check=True, timeout=60)
    (folder / "output-ffprobe.json").write_bytes(metadata.stdout)
    probe = json.loads(metadata.stdout)
    if abs(float(probe["format"]["duration"]) - duration) > .5:
        raise RuntimeError("Published duration differs from source by more than 0.5 seconds")
    if not {"audio", "video"} <= {row["codec_type"] for row in probe["streams"]}:
        raise RuntimeError("Published output lacks audio or video")
    with (folder / "output-decode.log").open("wb") as log:
        subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-i", str(path),
            "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"],
            stdout=log, stderr=subprocess.STDOUT, check=True, timeout=max(120, duration * 2))
    return {"sha256": digest(path), "bytes": path.stat().st_size,
            "source_duration": duration, "output_duration": float(probe["format"]["duration"]),
            "complete_decode": True}


async def recover_one(client, task_id, folder, *, execute=False, timeout=21600, poll=5):
    from config import settings
    from core.streaming.session_store import _read, _project_path, _valid_row_audio
    from core.streaming.output_gate import invalid_dub_timing_ids, missing_spoken_output_ids
    folder.mkdir(parents=True, exist_ok=False)
    path = _project_path(task_id)
    original = path.read_bytes()
    (folder / "project.before.json").write_bytes(original)
    saved = _read(task_id)
    if path.read_bytes() != original:
        raise RuntimeError("Project changed during backup; wait for its current owner")
    fields, rows = saved["session"], saved["segments"]
    source = Path(fields["video_path"])
    manual = manual_rows(rows)
    baseline = {"task_id": task_id, "manifest_sha256": hashlib.sha256(original).hexdigest(),
        "source_path": str(source), "source_sha256": digest(source), "manual_rows": manual,
        "row_revisions": {str(row["id"]): row.get("revision", 0) for row in rows},
        "valid_audio_sha256": {str(row["id"]): digest(row["audio_path"]) for row in rows
                               if _valid_row_audio(row, row.get("audio_path"))},
        "voice": fields.get("voice"), "tts_engine": fields.get("tts_engine_name"),
        "source_version": fields.get("_chunked_source_identity"), "execute": execute}
    write_json(folder / "baseline.json", baseline)
    if not execute:
        from core.streaming.output_gate import final_output_metadata
        # Saved coverage is useful diagnostic context, but cannot certify that
        # a legacy recognizer heard all speech. Keep that assertion unknown.
        preflight = final_output_metadata(rows,
            valid_audio=lambda row: str(row["id"]) in baseline["valid_audio_sha256"],
            source_complete=None, total_duration=fields.get("total_duration"))
        preflight["saved_source_prepared_seconds"] = fields.get("_source_prepared_seconds")
        preflight["source_acceptance"] = "requires actual source coverage verification"
        write_json(folder / "preflight.json", preflight)
        return {"task_id": task_id, "status": "PLANNED", "manual_rows": len(manual), "segments": len(rows),
                "missing_speech_ids": preflight["missing_speech_ids"],
                "missing_audio_ids": preflight["missing_audio_ids"],
                "invalid_timing_ids": preflight["invalid_timing_ids"]}
    snapshot = await api(client, "GET", f"/api/streaming/{task_id}")
    verify_source_url(snapshot.get("video_url"), source, settings.INPUT_DIR)
    verify_manual(manual, snapshot["segments"])
    write_json(folder / "snapshot.before.json", snapshot)
    if snapshot.get("voice") != baseline["voice"] or snapshot.get("tts_engine") != baseline["tts_engine"]:
        raise RuntimeError("Backend project voice/engine differs from backed-up project")
    deadline = time.monotonic() + timeout
    if processing(snapshot):
        snapshot = await wait_idle(client, task_id, manual, folder, deadline, poll)
    # A failed/empty preview must be repaired first. Promotion is legal only
    # after the backend explicitly reports that the preview can continue.
    if snapshot.get("progress", {}).get("translation_mode") == "preview":
        progress = snapshot.get("progress", {})
        if progress.get("status") in {"FAILED", "PREPARED"} and progress.get("can_retry"):
            await api(client, "POST", f"/api/streaming/{task_id}/retry")
            snapshot = await wait_idle(client, task_id, manual, folder, deadline, poll)
            progress = snapshot.get("progress", {})
        if progress.get("can_translate_full") is not True:
            raise RuntimeError("Preview is incomplete and cannot be promoted safely")
        await api(client, "POST", f"/api/streaming/{task_id}/translate-full")
        snapshot = await wait_idle(client, task_id, manual, folder, deadline, poll)
    elif snapshot.get("final_output_blocked") or snapshot.get("media_complete") is not True:
        if not snapshot.get("progress", {}).get("can_retry"):
            raise RuntimeError("Project has incomplete media but no supported retry action")
        await api(client, "POST", f"/api/streaming/{task_id}/retry")
        snapshot = await wait_idle(client, task_id, manual, folder, deadline, poll)
    write_json(folder / "snapshot.recovered.json", snapshot)
    if snapshot.get("media_complete") is not True or snapshot.get("final_output_blocked"):
        raise RuntimeError("Full media still incomplete; no partial export requested")
    if missing_spoken_output_ids(snapshot["segments"]):
        raise RuntimeError("Translation contains missing speech or placeholder notes; source recovery is required")
    # Text edits and existing valid manual speech are invariants, not repair targets.
    for row in _read(task_id)["segments"]:
        row_id = str(row["id"])
        if row_id in manual and row_id in baseline["valid_audio_sha256"]:
            if not row.get("audio_path") or digest(row["audio_path"]) != baseline["valid_audio_sha256"][row_id]:
                raise RuntimeError(f"Previously valid manual speech changed for row {row_id}")
    tasks = await api(client, "GET", "/api/tasks")
    active = next((row for row in tasks.get("tasks", []) if row.get("parent_session_id") == task_id
                   and row.get("status") in {"RUNNING", "CANCELLING"}), None)
    if not active:
        await api(client, "POST", "/api/streaming/export-hq/jobs", json={"task_id": task_id, "allow_incomplete": False})
    result = await wait_export(client, task_id, folder, deadline, poll)
    final = await api(client, "GET", f"/api/streaming/{task_id}")
    verify_manual(manual, final["segments"])
    if digest(source) != baseline["source_sha256"]:
        raise RuntimeError("Source bytes changed during recovery")
    if result.get("output_incomplete") or invalid_dub_timing_ids(final["segments"], final["duration"]):
        raise RuntimeError("Published result is partial or its saved timeline is invalid")
    name = result["output_filename"]
    if not isinstance(name, str) or Path(name).name != name or not name.lower().endswith(".mp4"):
        raise RuntimeError("Unexpected output filename")
    evidence = await asyncio.to_thread(validate_media, settings.OUTPUT_DIR / name, final["duration"], folder)
    after = _read(task_id)
    write_json(folder / "timeline.json", [{key: row.get(key) for key in
        ("id", "start", "end", "dub_start", "dub_end", "tts_duration", "final_vi", "needs_review", "verification")}
        for row in final["segments"]])
    (folder / "project.after.json").write_bytes(path.read_bytes())
    write_json(folder / "snapshot.after.json", final)
    return {"task_id": task_id, "status": "VALIDATED", "output_filename": name,
        "output_is_draft": result.get("output_is_draft"),
        "output_review_segment_ids": result.get("output_review_segment_ids", []),
        "manual_rows_preserved": len(manual), "media": evidence,
        "unchanged_audio_ids": [row["id"] for row in after["segments"] if row.get("audio_path")
            and str(row["id"]) in baseline["valid_audio_sha256"] and Path(row["audio_path"]).is_file()
            and digest(row["audio_path"]) == baseline["valid_audio_sha256"][str(row["id"])]]}


async def run(args):
    from config import settings
    url = local_backend(args.backend)
    ids = args.project
    if len(ids) != len(set(ids)) or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", item) for item in ids):
        raise ValueError("Project IDs must be distinct safe saved IDs")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = settings.WORKSPACE_DIR / "audit" / f"acceptance_{stamp}_{os.getpid()}"
    root.mkdir(parents=True, exist_ok=False)
    results = []
    async with httpx.AsyncClient(base_url=url, timeout=60, trust_env=False) as client:
        health = await api(client, "GET", "/api/health")
        from core.runtime_revision import source_revision
        expected_revision = source_revision()
        schema = await api(client, "GET", "/openapi.json")
        async_export_available = "/api/streaming/export-hq/jobs" in schema.get("paths", {})
        write_json(root / "backend.json", {"url": url, "health": health,
                                             "expected_source_revision": expected_revision,
                                             "source_revision_matches": health.get("source_revision") == expected_revision,
                                             "async_export_available": async_export_available})
        if args.execute and health.get("source_revision") != expected_revision:
            raise RuntimeError("Backend predates the current source revision; restart Studio before recovery")
        if args.execute and not async_export_available:
            raise RuntimeError("Backend does not expose the updated async export API; restart before execution")
        for task_id in ids:
            try:
                with project_lock(settings.WORKSPACE_DIR / "audit" / "recovery-locks", task_id):
                    result = await recover_one(client, task_id, root / task_id,
                                               execute=args.execute, timeout=args.timeout, poll=args.poll)
            except Exception as error:
                result = {"task_id": task_id, "status": "FAILED", "error_type": type(error).__name__, "error": str(error)}
            results.append(result)
            write_json(root / "summary.json", results)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            if result["status"] == "FAILED":
                break  # Do not compound an unassessed failure across more projects.
    print(f"Audit: {root}")
    return int(any(row["status"] == "FAILED" for row in results))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True, help="Current local desktop backend URL")
    parser.add_argument("--project", action="append", required=True, help="Exact saved project ID; repeat to process sequentially")
    parser.add_argument("--execute", action="store_true", help="Retry/continue and export through the backend")
    parser.add_argument("--timeout", type=float, default=21600, help="Observation seconds per project; timeout does not stop backend")
    parser.add_argument("--poll", type=float, default=5)
    args = parser.parse_args()
    if args.timeout <= 0 or args.poll < .1:
        parser.error("timeout must be positive and poll must be at least 0.1 seconds")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
