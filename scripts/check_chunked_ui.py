"""Opt-in real provider QA for preview -> editable prefix -> full translation.

Uses the production desktop page offscreen and muted. No providers, HTTP,
recognition, speech or media are mocked. Retains the named QA project for reruns.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

from check_production_ui import backend, click, evaluate, event, until, wait, ui_state, open_history, check_playback
from check_partial_ui import hashes, partial_playback
from PySide6.QtCore import QCoreApplication, QEvent, QUrl
from PySide6.QtWidgets import QApplication
from config import settings
from desktop_app import StudioMainWindow
from core.services.service_manager import service_manager

SOURCE = ROOT / "workspace/temp/partial-qa.mp4"
RECORD = ROOT / "workspace/temp/preview-qa.json"


def wait_for_idle(page, base, task_id, timeout=3600):
    deadline, previous = time.monotonic() + timeout, None
    while time.monotonic() < deadline:
        snapshot = backend(base, f"/api/streaming/{task_id}")
        progress = snapshot["progress"]
        current = (progress["status"], progress.get("stage"), progress.get("processed_seconds"),
                   sum(row["status"] in {"READY", "PLAYED"} for row in snapshot["segments"]))
        if current != previous:
            event("PROGRESS", {"status": current[0], "stage": current[1], "processed": current[2], "ready": current[3]})
            previous = current
        if progress["status"] in {"FAILED", "STOPPED", "PREVIEW_READY", "COMPLETED"}:
            until(page, "document.getElementById('task-progress').dataset.status===" + json.dumps(progress["status"]), timeout=30)
            return snapshot
        wait(200)
    raise AssertionError("Real runtime did not reach a terminal state within the acceptance deadline")


def edit_through_ui(page, base, task_id, row):
    sid, old_revision = row["id"], row.get("revision", 0)
    # Punctuation-only change keeps the original sentence's meaning.
    punctuation = "." if row["final_vi"].endswith("!") else "!"
    text = row["final_vi"].rstrip(".!?") + punctuation
    assert text != row["final_vi"]
    assert click(page, f"seg-vi-{sid}")
    until(page, f"document.getElementById('seg-row-{sid}').dataset.editing==='true'")
    assert evaluate(page, "(() => {const e=document.getElementById(" + json.dumps(f"seg-input-{sid}") +
                    ");e.value=" + json.dumps(text) + ";e.dispatchEvent(new Event('input',{bubbles:true}));return true;})()")
    assert evaluate(page, "(() => {const b=document.querySelector(" + json.dumps(f"#seg-row-{sid} .transcript-editor-actions button") +
                    ");if(!b||b.disabled)return false;b.click();return true;})()")
    until(page, f"document.getElementById('seg-row-{sid}').dataset.editing==='false'", timeout=120)
    snapshot = backend(base, f"/api/streaming/{task_id}")
    edited = next(item for item in snapshot["segments"] if item["id"] == sid)
    assert edited["revision"] > old_revision and edited["final_vi"] == text
    assert edited["status"] in {"READY", "PLAYED"} and edited.get("audio_url")
    event("EDIT_SAVED", {"id": sid, "revision": edited["revision"], "text": text})
    return edited


def inspect_extended_tail(page, snapshot):
    """Seek with the production transcript control, then observe real media."""
    tail = next(row for row in snapshot["segments"] if row.get("dub_tail_limit") is not None
                and row["dub_end"] > row["end"] + .35)
    until(page, "document.getElementById('video-player').readyState>=2", timeout=90)
    assert evaluate(page, "(() => {const b=document.querySelector(" +
                    json.dumps(f"#seg-row-{tail['id']} .transcript-time") +
                    ");if(!b)return false;b.click();return true;})()")
    if evaluate(page, "document.getElementById('video-player').paused"):
        assert click(page, "player-play-toggle")
    time_point = tail["end"] + .36
    expression = """(() => {const v=document.getElementById('video-player');
        const a=window.__qaAudio.find(a=>a.currentSrc.includes('/audio/""" + snapshot["task_id"] + "/" + str(tail["id"]) + """'));
        return {time:v.currentTime,paused:v.paused,text:document.getElementById('subtitle-text').textContent,
            audio:a?{time:a.currentTime,paused:a.paused,ready:a.readyState,error:a.error?.message||null}:null};})()"""
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        state = evaluate(page, expression)
        if state["time"] >= time_point:
            break
        wait(30)
    assert time_point <= state["time"] < tail["dub_end"], state
    assert not state["paused"] and state["audio"] and not state["audio"]["paused"], state
    assert state["audio"]["ready"] >= 2 and state["audio"]["error"] is None, state
    assert state["text"].strip() and state["text"].strip() in tail["final_vi"], state
    event("EXTENDED_TAIL_UI_PASS", {"id": tail["id"], "source_end": tail["end"],
          "dub_end": tail["dub_end"], "state": state})
    assert click(page, "player-play-toggle")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["preview", "full", "resume", "resume-stop", "inspect", "inspect-tail"])
    args = parser.parse_args()
    assert SOURCE.is_file() and SOURCE.stat().st_size < 100_000_000
    assert settings.LLM_PROVIDER == "opencode"
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
        evaluate(page, "(() => {window.__acceptanceErrors=[];window.addEventListener('error',e=>window.__acceptanceErrors.push(String(e.message)));window.addEventListener('unhandledrejection',e=>window.__acceptanceErrors.push(String(e.reason)));window.__qaAudio=[];const Original=window.Audio;window.Audio=new Proxy(Original,{construct(target,args){const a=new target(...args);window.__qaAudio.push(a);return a;}});return true;})()")
        if args.mode == "preview":
            assert not RECORD.exists(), "Refuse to overwrite an existing QA project; use resume"
            existing = {row["task_id"] for row in backend(base, "/api/tasks")["tasks"]}
            window.notify_js_file_selected(str(SOURCE))
            until(page, "!document.getElementById('btn-start').disabled")
            assert click(page, "btn-start")
            deadline, task_id = time.monotonic() + 30, None
            while time.monotonic() < deadline:
                candidates = [row for row in backend(base, "/api/tasks")["tasks"]
                              if row["task_id"] not in existing and row.get("translation_mode") == "preview"]
                if len(candidates) == 1:
                    task_id = candidates[0]["task_id"]
                    break
                wait()
            assert task_id, "Actual UI start did not reserve a preview session"
            record = {"task_id": task_id, "source": str(SOURCE)}
            RECORD.write_text(json.dumps(record), encoding="utf-8")
        else:
            record = json.loads(RECORD.read_text(encoding="utf-8"))
            assert record["source"] == str(SOURCE)
            task_id = record["task_id"]
            open_history(page, task_id)
            snapshot = backend(base, f"/api/streaming/{task_id}")
            retained = hashes(task_id, snapshot["segments"])
            assert all(retained.get(sid) == digest for sid, digest in record.get("edited_hashes", {}).items())
            if args.mode in {"inspect", "inspect-tail"}:
                assert snapshot["progress"]["status"] == "COMPLETED" and snapshot["output_filename"]
                if args.mode == "inspect-tail":
                    inspect_extended_tail(page, snapshot)
                check_playback(page)
                event("FINAL_REOPEN_PASS", {"task_id": task_id, "output": snapshot["output_filename"]})
                return
            if args.mode == "full":
                if snapshot["progress"]["can_translate_full"]:
                    assert click(page, "btn-translate-full")
                else:
                    # A previous acceptance run may have stopped after the
                    # real promotion. Resume its saved full session unchanged.
                    assert snapshot["progress"]["translation_mode"] == "full" and snapshot["progress"]["can_retry"]
                    assert click(page, "btn-retry-worker")
                until(page, "document.getElementById('task-progress').dataset.status==='RUNNING'")
                # A completed early sentence remains editable while later Muse
                # requests execute; do not wait for the full result to test this.
                first = next(row for row in snapshot["segments"] if row["status"] in {"READY", "PLAYED"} and row["final_vi"])
                edited = edit_through_ui(page, base, task_id, first)
                record["edited"] = {"id": edited["id"], "revision": edited["revision"], "text": edited["final_vi"]}
                record["edited_hashes"] = hashes(task_id, [edited])
                RECORD.write_text(json.dumps(record), encoding="utf-8")
                partial_playback(page, backend(base, f"/api/streaming/{task_id}"))
                assert click(page, "player-play-toggle")
            else:
                assert snapshot["progress"]["can_retry"]
                assert click(page, "btn-retry-worker")
                until(page, "document.getElementById('task-progress').dataset.status==='RUNNING'", timeout=30)
                if args.mode == "resume-stop":
                    partial_playback(page, snapshot)
                    assert ui_state(page)["status"] == "RUNNING", ui_state(page)
                    assert click(page, "btn-stop-worker")
                    until(page, "document.getElementById('task-progress').dataset.status==='STOPPED'", timeout=30)
                    stopped = backend(base, f"/api/streaming/{task_id}")
                    saved = hashes(task_id, stopped["segments"])
                    assert all(saved.get(sid) == digest for sid, digest in retained.items())
                    state = [(row["id"], row["status"], row.get("revision"), row.get("final_vi"))
                             for row in stopped["segments"]]
                    wait(5000)
                    late = backend(base, f"/api/streaming/{task_id}")
                    assert [(row["id"], row["status"], row.get("revision"), row.get("final_vi"))
                            for row in late["segments"]] == state, "Late reply changed a stopped task"
                    assert not stopped["output_filename"] and not ui_state(page)["errors"], ui_state(page)
                    event("CHUNKED_STOP_PASS", {"task_id": task_id, "retained_wavs": len(saved),
                          "edited_revision": record.get("edited", {}).get("revision"), "state": ui_state(page)})
                    return
        event("TASK", task_id)
        snapshot = wait_for_idle(page, base, task_id)
        if snapshot["progress"]["status"] == "FAILED":
            event("REAL_FAILURE", {"progress": snapshot["progress"], "rows": [row for row in snapshot["segments"] if row["status"] == "FAILED"]})
            raise AssertionError(snapshot["progress"]["stage"])
        if args.mode == "preview":
            assert snapshot["progress"]["status"] == "PREVIEW_READY"
            assert 0 < snapshot["progress"]["processed_seconds"] < snapshot["duration"]
            assert snapshot["progress"]["source_prepared_seconds"] < snapshot["duration"]
            assert not snapshot["output_filename"]
            assert not evaluate(page, "document.getElementById('btn-translate-full').classList.contains('hidden')")
            assert evaluate(page, "document.getElementById('btn-export-hq').disabled")
            partial_playback(page, snapshot)
            record["preview_hashes"] = hashes(task_id, snapshot["segments"])
            RECORD.write_text(json.dumps(record), encoding="utf-8")
            event("PREVIEW_PASS", {"task_id": task_id, "state": ui_state(page), "coverage": snapshot["progress"]["processed_seconds"]})
        elif snapshot["progress"]["status"] == "PREVIEW_READY":
            event("PREVIEW_RETRY_PASS", {"task_id": task_id, "state": ui_state(page)})
        else:
            assert snapshot["progress"]["status"] == "COMPLETED"
            until(page, "!document.getElementById('task-result-link').classList.contains('hidden')", timeout=180)
            snapshot = backend(base, f"/api/streaming/{task_id}")
            assert snapshot["output_filename"] and all(row["status"] in {"READY", "PLAYED"} for row in snapshot["segments"])
            if record.get("edited"):
                edited = next(row for row in snapshot["segments"] if row["id"] == record["edited"]["id"])
                assert edited["revision"] == record["edited"]["revision"] and edited["final_vi"] == record["edited"]["text"]
                assert all(hashes(task_id, [edited]).get(sid) == digest for sid, digest in record["edited_hashes"].items())
            check_playback(page)
            record["output"] = snapshot["output_filename"]
            RECORD.write_text(json.dumps(record), encoding="utf-8")
            event("FULL_PASS", {"task_id": task_id, "output": snapshot["output_filename"], "state": ui_state(page)})
        assert not ui_state(page)["errors"], ui_state(page)
    finally:
        service_manager.shutdown_all()
        if window is not None:
            window.web_view.stop()
            window.web_view.setUrl(QUrl("about:blank"))
            wait(200)
            window.hide()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()


if __name__ == "__main__":
    main()
