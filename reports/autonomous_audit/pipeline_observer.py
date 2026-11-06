"""Bounded observer of an already active real job; no job starts or mutations."""
import argparse
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import uuid

HERE = Path(__file__).resolve().parent


def event(kind, **values):
    item = {"timestamp": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "event_id": uuid.uuid4().hex, "event": kind, **values}
    with (HERE / "runtime_events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(item, ensure_ascii=False) + "\n")
        stream.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--task", default="douyin_7692745161054506290")
    parser.add_argument("--label", choices=("SHORT", "LONG"), default="SHORT")
    parser.add_argument("--seconds", type=int, default=3600)
    parser.add_argument("--interval", type=int, default=60)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 1 <= args.seconds <= 86400 or not 15 <= args.interval <= 60:
        parser.error("Use a valid local port, bounded runtime and 15-60 second samples")
    started = time.monotonic()
    common = {"run_id": "real-" + uuid.uuid4().hex[:12], "video_label": args.label,
              "video_id": "7692745161054506290" if args.label == "SHORT" else "7666381909177470271",
              "task_id": args.task}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    errors = 0
    while time.monotonic() - started < args.seconds:
        before = time.monotonic()
        try:
            with opener.open(f"http://127.0.0.1:{args.port}/api/streaming/{args.task}", timeout=15) as response:
                snapshot = json.loads(response.read(8 * 1024 * 1024).decode("utf-8"))
            progress = snapshot.get("progress", {})
            public = {key: progress.get(key) for key in (
                "phase", "stage", "status", "progress_pct", "elapsed_seconds", "eta_seconds",
                "processed_seconds", "total_seconds", "source_prepared_seconds", "completed_chunks",
                "total_chunks", "retry_chunks", "ai_queue", "segment_states", "review_summary",
                "review_scope", "reviewing_segment_ids", "missing_speech_ids", "final_output_blocked")}
            public.update(output_filename=snapshot.get("output_filename", ""),
                          current_segment=(progress.get("reviewing_segment_ids") or [None])[0])
            event("PROGRESS", **common, progress=public, **{key: public.get(key) for key in (
                "status", "stage", "progress_pct", "elapsed_seconds", "eta_seconds", "processed_seconds",
                "total_seconds", "segment_states", "ai_queue", "output_filename")})
            errors = 0
            print(json.dumps({"status": progress.get("status"), "stage": progress.get("phase"),
                              "measured_seconds": progress.get("processed_seconds"),
                              "review": progress.get("review_summary"),
                              "missing_count": len(progress.get("missing_speech_ids", []))}), flush=True)
            if progress.get("status") not in {"RUNNING", "PAUSED", "CANCELLING"}:
                event("RUN_COMPLETED", **common, status=progress.get("status"),
                      stage="PIPELINE_TERMINAL_UNVERIFIED", progress=public,
                      summary="Actual worker terminal state; output acceptance is still required")
                subprocess.run([sys.executable, "-B", str(HERE / "report_updater.py"), "--once"], check=False)
                return 0
        except Exception as error:
            errors += 1
            if errors == 3:
                event("ERROR", **common, severity="High", component="backend/network", stage="observer",
                      error_type=type(error).__name__, summary="Three consecutive local snapshot probes failed")
            print(json.dumps({"observer_error": type(error).__name__, "consecutive_failures": errors}), flush=True)
        subprocess.run([sys.executable, "-B", str(HERE / "report_updater.py"), "--once"], check=False)
        time.sleep(max(0, args.interval - (time.monotonic() - before)))
    event("PROGRESS", **common, stage="OBSERVER_WINDOW_ENDED",
          summary="Bounded observer window ended; real desktop job and detached watchdog remain separately owned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
