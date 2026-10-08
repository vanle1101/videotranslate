"""Opt-in real Muse/Edge/Qt acceptance of incremental translated playback.

Uses a named short QA excerpt, actual UI buttons, HTTP and provider requests.
No provider/media mocks. Run sequentially with other Qt/runtime checks.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from check_production_ui import backend, click, evaluate, event, until, wait, ui_state, open_history
from PySide6.QtCore import QCoreApplication, QEvent, QUrl
from PySide6.QtWidgets import QApplication
from config import settings
from desktop_app import StudioMainWindow
from core.services.service_manager import service_manager

SOURCE = ROOT / "workspace/temp/partial-qa.mp4"
RECORD = ROOT / "workspace/temp/partial-qa.json"


def hashes(task_id, rows):
    root = (ROOT / "workspace/cache" / task_id / "segments").resolve()
    result = {}
    for row in rows:
        if row["status"] not in ("READY", "PLAYED") or not row.get("audio_url"):
            continue
        path = (root / f"seg_{row['id']}.wav").resolve()
        assert path.parent == root and path.is_file() and path.stat().st_size > 44
        result[str(row["id"])] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def partial_playback(page, snapshot):
    until(page, "document.getElementById('video-player').readyState>=2", timeout=90)
    state = evaluate(page, "(() => {const v=document.getElementById('video-player'); return {ready:v.readyState,time:v.currentTime,width:v.videoWidth,height:v.videoHeight,error:v.error?.message||null};})()")
    assert state["error"] is None and state["width"] > 0 and state["height"] > 0, state
    # Autoplay came from the actual Start action. Muting prevents audible QA.
    before = state["time"]
    until(page, f"document.getElementById('video-player').currentTime>{before + .2}", timeout=15)
    assert evaluate(page, "document.querySelectorAll('[id^=seg-vi-]').length") == len(snapshot["segments"])
    assert evaluate(page, "Array.from(document.querySelectorAll('[id^=seg-vi-]')).some(e=>e.textContent.trim().length>0)")
    event("PARTIAL_VIDEO_PLAYBACK", state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["start-stop", "resume"])
    args = parser.parse_args()
    assert SOURCE.is_file() and SOURCE.stat().st_size < 100_000_000
    assert settings.LLM_PROVIDER == "opencode", "Keep the actual configured Muse provider"
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window = None
    try:
        port = service_manager.start_backend(timeout=30)
        base = f"http://127.0.0.1:{port}"
        event("BACKEND", {"pid": os.getpid(), "port": port, "mode": args.mode})
        window = StudioMainWindow(port)
        window.resize(1366, 860)
        page = window.web_view.page()
        page.setAudioMuted(True)
        window.show()
        until(page, "document.readyState==='complete' && !!window.loadDroppedLocalVideo")
        evaluate(page, "(() => {window.__acceptanceErrors=[];window.addEventListener('error',e=>window.__acceptanceErrors.push(String(e.message)));window.addEventListener('unhandledrejection',e=>window.__acceptanceErrors.push(String(e.reason)));return true;})()")
        if args.mode == "start-stop":
            assert not RECORD.exists(), "Refuse to replace an existing QA record; use resume"
            window.notify_js_file_selected(str(SOURCE))
            until(page, "!document.getElementById('btn-start').disabled")
            assert click(page, "btn-start")
            deadline = time.monotonic() + 30
            task_id = None
            while time.monotonic() < deadline:
                rows = backend(base, "/api/tasks")["tasks"]
                candidates = [row for row in rows if row["task_type"] == "Realtime Dubbing" and row["status"] == "RUNNING"]
                if len(candidates) == 1:
                    task_id = candidates[0]["task_id"]
                    break
                wait()
            assert task_id, "Actual UI Start did not create a runtime task"
            RECORD.write_text(json.dumps({"task_id": task_id, "source": str(SOURCE)}), encoding="utf-8")
        else:
            record = json.loads(RECORD.read_text(encoding="utf-8"))
            assert record["source"] == str(SOURCE)
            task_id = record["task_id"]
            assert task_id.isalnum() and len(task_id) == 8
            open_history(page, task_id)
            snapshot = backend(base, f"/api/streaming/{task_id}")
            assert snapshot["progress"]["can_retry"] and snapshot["initialized"]
            assert hashes(task_id, snapshot["segments"]) == record["prefix_hashes"]
            assert snapshot["telemetry"]["playable_until"] < snapshot["duration"]
            event("FRESH_RESTORE", {"ui": ui_state(page), "preserved_wavs": len(record["prefix_hashes"])})
            assert click(page, "btn-retry-worker")
        event("TASK", task_id)
        deadline = time.monotonic() + 1800
        previous = None
        snapshot = {}
        while time.monotonic() < deadline:
            snapshot = backend(base, f"/api/streaming/{task_id}")
            progress = snapshot["progress"]
            ready = sum(row["status"] in ("READY", "PLAYED") for row in snapshot["segments"])
            current = (progress["status"], progress.get("stage"), ready, progress.get("visual_step"), progress.get("frames_done"))
            if current != previous:
                event("PROGRESS", {"status": progress["status"], "stage": progress.get("stage"), "ready": ready,
                    "step": progress.get("visual_step"), "frames": [progress.get("frames_done"), progress.get("frames_total")],
                    "ui_detail": evaluate(page, "document.getElementById('task-progress-detail').textContent")})
                previous = current
            if progress["status"] == "FAILED":
                event("REAL_FAILURE", progress)
                raise AssertionError(f"Real provider/runtime failure: {progress.get('stage')}")
            if (args.mode == "start-stop" and ready and ready < len(snapshot["segments"])
                    and snapshot["telemetry"].get("ready_to_play")):
                prefix = hashes(task_id, snapshot["segments"])
                assert prefix
                partial_playback(page, snapshot)
                assert ui_state(page)["status"] == "RUNNING", ui_state(page)
                assert not ui_state(page)["resultVisible"], "Partial output must not masquerade as full MP4"
                assert click(page, "btn-stop-worker")
                until(page, "document.getElementById('task-progress').dataset.status==='STOPPED'", timeout=30)
                stopped = backend(base, f"/api/streaming/{task_id}")
                retained = hashes(task_id, stopped["segments"])
                assert all(retained.get(sid) == digest for sid, digest in prefix.items())
                record = json.loads(RECORD.read_text(encoding="utf-8"))
                record["prefix_hashes"] = retained
                RECORD.write_text(json.dumps(record), encoding="utf-8")
                event("PARTIAL_STOP_PASS", {"task_id": task_id, "ready_wavs": len(retained), "playable_until": stopped["telemetry"]["playable_until"], "duration": stopped["duration"], "state": ui_state(page)})
                return
            if args.mode == "resume" and progress["status"] == "COMPLETED":
                record = json.loads(RECORD.read_text(encoding="utf-8"))
                now = hashes(task_id, snapshot["segments"])
                assert all(now.get(sid) == digest for sid, digest in record["prefix_hashes"].items())
                assert ready == len(snapshot["segments"])
                assert snapshot["telemetry"]["playable_until"] >= snapshot["duration"] - .1
                assert not ui_state(page)["errors"]
                event("RESUME_PASS", {"task_id": task_id, "ready": ready, "prefix_unchanged": True, "state": ui_state(page)})
                return
            wait(500)
        raise AssertionError("Real partial QA exceeded 30 minutes")
    finally:
        if window is not None:
            window.web_view.stop()
            window.web_view.setUrl(QUrl("about:blank"))
            wait(200)
            window.hide()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
        service_manager.shutdown_all()
        event("BACKEND_STOPPED", True)


if __name__ == "__main__":
    main()
