"""Opt-in real UI/Edge acceptance on the explicitly owned short QA project.

No person/relationship is inferred: only a neutral QA voice label is recorded,
with empty self/listener fields. Existing manual words and all other WAVs must
survive. The trial Edge voice is restored through the same production controls.
Run trial, then reopen-restore in a fresh process to test durable references.
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
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

from check_production_ui import (backend, click, evaluate, event, until, wait,
    ui_state, open_history, export_through_ui)
from PySide6.QtCore import QCoreApplication, QEvent, QUrl
from PySide6.QtWidgets import QApplication
from desktop_app import StudioMainWindow
from core.services.service_manager import service_manager
from core.streaming.session_store import _read, _valid_row_audio

TASK_ID, ROW_ID = "38680e43", 6
LABEL = "Giọng QA 1"


def audio_hashes(snapshot):
    rows = _read(TASK_ID)["segments"]
    return {row["id"]: hashlib.sha256(Path(row["audio_path"]).read_bytes()).hexdigest()
            for row in rows if row.get("audio_path") and _valid_row_audio(row, row["audio_path"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["trial", "reopen-restore", "review-export"])
    parser.add_argument("--backend-port", type=int,
                        help="Reuse an already-owned loopback QA backend; do not start/stop another provider queue.")
    args = parser.parse_args()
    assert args.backend_port is None or 1024 < args.backend_port <= 65535
    app = QApplication.instance() or QApplication([])
    assert app.platformName() == "offscreen"
    app.setQuitOnLastWindowClosed(False)
    window = None
    try:
        port = args.backend_port or service_manager.start_backend(timeout=30)
        base = f"http://127.0.0.1:{port}"
        event("BACKEND", {"pid": os.getpid(), "port": port, "mode": args.mode})
        window = StudioMainWindow(port)
        window.resize(1366, 860)
        page = window.web_view.page()
        page.setAudioMuted(True)
        window.show()
        until(page, "document.readyState==='complete' && !!window.openTaskInStudio")
        evaluate(page, "(() => {window.__acceptanceErrors=[];window.addEventListener('error',e=>window.__acceptanceErrors.push(String(e.message)));window.addEventListener('unhandledrejection',e=>window.__acceptanceErrors.push(String(e.reason)));return true;})()")
        open_history(page, TASK_ID)
        before = backend(base, f"/api/streaming/{TASK_ID}")
        row = next(r for r in before["segments"] if r["id"] == ROW_ID)
        words, verdict = row["final_vi"], row["verification"]
        assert verdict["status"] == "manual", "Only the retained manual QA row may be changed"
        if args.mode == "reopen-restore":
            assert row["speaker_confirmation"]["label"] == LABEL
            assert row["voice_id"] == "edge:vi-VN-NamMinhNeural"
            durable = next(r for r in _read(TASK_ID)["segments"] if r["id"] == ROW_ID)
            assert _valid_row_audio(durable, durable["audio_path"])
            event("FRESH_PROCESS_CONFIRMATION_RESTORED", {"id": ROW_ID, "revision": row["revision"], "voice": row["voice_id"]})
        old_hashes = audio_hashes(before)
        voice = "edge:vi-VN-NamMinhNeural" if args.mode == "trial" else "edge:vi-VN-HoaiMyNeural"
        label = LABEL
        if args.mode == "review-export":
            # A genuine repeated acceptance run must create a new assertion;
            # saving identical choices correctly performs no semantic API work.
            prior_label = (row.get("speaker_confirmation") or {}).get("label")
            label = LABEL if prior_label == LABEL + " · kiểm thử" else LABEL + " · kiểm thử"
        assert click(page, f"seg-speaker-{ROW_ID}")
        expression = """(() => {const id=""" + str(ROW_ID) + "; const voice=" + json.dumps(voice) + ";" + """
            document.getElementById(`seg-speaker-name-${id}`).value=""" + json.dumps(label) + ";" + """
            document.getElementById(`seg-speaker-self-${id}`).value='';
            document.getElementById(`seg-speaker-listener-${id}`).value='';
            document.getElementById(`seg-speaker-all-${id}`).checked=false;
            const v=document.getElementById(`seg-speaker-voice-${id}`);
            v.value=voice; return v.value===voice;})()"""
        assert evaluate(page, expression), "Configured Edge voice is not available in the production selector"
        assert click(page, f"seg-speaker-save-{ROW_ID}")
        until(page, f"document.querySelector('#seg-row-{ROW_ID} .speaker-editor').hidden", timeout=30)
        saved = backend(base, f"/api/streaming/{TASK_ID}")
        changed = next(r for r in saved["segments"] if r["id"] == ROW_ID)
        assert changed["final_vi"] == words and changed["verification"] == verdict
        if args.mode == "review-export":
            prior_output = ROOT / "workspace/outputs" / f"douyin_translated_{TASK_ID}_hq.mp4"
            prior_stamp = prior_output.stat().st_mtime_ns if prior_output.is_file() else None
            assert not changed["tts_voice_outdated"] and old_hashes == audio_hashes(saved)
            targets = [r["id"] for r in saved["segments"] if r.get("speaker_review_pending")]
            assert targets and ROW_ID not in targets, "The unchanged manual words must never enter AI review"
            assert click(page, "btn-review-worker")
            until(page, "document.getElementById('task-progress').dataset.status==='RUNNING'")
            deadline = time.monotonic() + 1800
            seen = False
            while time.monotonic() < deadline:
                current = backend(base, f"/api/streaming/{TASK_ID}")
                active = current["progress"].get("reviewing_segment_ids", [])
                if active:
                    assert sorted(active) == sorted(targets), (active, targets)
                    seen = True
                if current["progress"]["status"] not in {"RUNNING", "PAUSED", "CANCELLING"}:
                    break
                wait(200)
            else:
                raise AssertionError("Real targeted Muse review exceeded thirty minutes")
            assert seen and not any(r.get("speaker_review_pending") for r in current["segments"])
            new_hashes = audio_hashes(current)
            assert {sid:digest for sid,digest in old_hashes.items() if sid not in targets} == {
                sid:digest for sid,digest in new_hashes.items() if sid not in targets}
            event("REAL_TARGETED_MUSE_REVIEW", {"targets": targets, "review": current["progress"]["review_summary"],
                "manual_words_preserved": next(r for r in current["segments"] if r["id"] == ROW_ID)["final_vi"] == words})
            assert not ui_state(page)["errors"], ui_state(page)
            # A successful review normally owns automatic export. If it has
            # already published fresh output, inspect it; otherwise run the
            # real export control explicitly. Never bypass a content gate.
            from check_production_ui import check_playback
            # The final review event schedules automatic export asynchronously.
            # PREPARED is not an export result; wait for the real page to expose
            # a validated result or an idle manual-export action.
            until(page, "!document.getElementById('task-result-link').classList.contains('hidden') || !document.getElementById('btn-export-hq').disabled", timeout=180)
            current = backend(base, f"/api/streaming/{TASK_ID}")
            if current.get("output_filename") and not current.get("output_outdated"):
                output = (ROOT / "workspace/outputs" / current["output_filename"]).resolve()
                assert output.is_file() and output.stat().st_size > 0
                assert output != prior_output.resolve() or output.stat().st_mtime_ns != prior_stamp
                check_playback(page)
            else:
                export_through_ui(page, base, TASK_ID, fresh_after=prior_stamp)
            event("PASS", {"mode": args.mode, "task_id": TASK_ID, "offscreen": True, "muted": page.isAudioMuted()})
            return
        assert changed["voice_id"] == voice and changed["tts_voice_outdated"]
        assert changed["status"] == "FAILED" and saved["progress"]["can_retry"]
        assert old_hashes == audio_hashes(saved), "Saving a voice selection removed/replaced a healthy WAV"
        assert not ui_state(page)["resultVisible"]
        event("VOICE_SELECTION_DURABLE", {"id": ROW_ID, "voice": voice, "revision": changed["revision"]})
        # Retry is the user's actual runtime control, not a direct executor call.
        assert click(page, "btn-retry-worker")
        until(page, "document.getElementById('task-progress').dataset.status==='RUNNING'", timeout=30)
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            current = backend(base, f"/api/streaming/{TASK_ID}")
            updated = next(r for r in current["segments"] if r["id"] == ROW_ID)
            if not current["progress"]["status"] in {"RUNNING", "PAUSED", "CANCELLING"}:
                assert updated["status"] in {"READY", "PLAYED"} and not updated["tts_voice_outdated"], updated
                break
            wait(200)
        else:
            raise AssertionError("Real Edge voice recovery exceeded ten minutes")
        assert updated["voice_id"] == voice and updated["final_vi"] == words and updated["verification"] == verdict
        new_hashes = audio_hashes(current)
        assert new_hashes[ROW_ID] != old_hashes[ROW_ID], "The voice retry reused the other voice's cached audio"
        assert {sid:digest for sid,digest in new_hashes.items() if sid != ROW_ID} == {
            sid:digest for sid,digest in old_hashes.items() if sid != ROW_ID}
        event("REAL_EDGE_TARGETED_RECOVERY", {"id": ROW_ID, "voice": voice,
            "duration": updated["tts_duration"], "sha256": new_hashes[ROW_ID], "unrelated_wavs_unchanged": True})
        assert not ui_state(page)["errors"], ui_state(page)
        if args.mode == "reopen-restore":
            # Role/context changes invalidate dependent review. Healthy WAVs
            # alone do not entitle this harness to export before that audit.
            event("DEPENDENT_REVIEW_REQUIRED", {"review": current["progress"]["review_summary"]})
        event("PASS", {"mode": args.mode, "task_id": TASK_ID, "offscreen": True, "muted": page.isAudioMuted()})
    finally:
        try:
            if window is not None:
                window.web_view.stop()
                window.web_view.setUrl(QUrl("about:blank"))
                wait(200)
                window.hide()
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                app.processEvents()
        finally:
            if args.backend_port is None:
                service_manager.shutdown_all()
                event("BACKEND_STOPPED", True)
            else:
                event("SHARED_BACKEND_UI_DETACHED", {"port": args.backend_port})


if __name__ == "__main__":
    main()
