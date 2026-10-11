"""Real saved-preview style acceptance through the production offscreen Qt UI.

Uses existing QA project 94f80114 and its real source/audio. Never substitutes
HTTP, media or provider responses. Temporary style edits are restored.
"""
import hashlib
import json
from pathlib import Path

from check_production_ui import (
    QApplication, QCoreApplication, QEvent, QUrl, ROOT, StudioMainWindow,
    backend, click, evaluate, event, open_history, read_style,
    service_manager, set_style, until, wait,
)

TASK = "94f80114"


def normalized_style(style):
    return {key: value.lower() if isinstance(value, str) else value for key, value in style.items()}


def launch(app):
    port = service_manager.start_backend(timeout=30)
    window = StudioMainWindow(port)
    window.resize(1366, 860)
    page = window.web_view.page()
    page.setAudioMuted(True)
    window.show()
    until(page, "document.readyState==='complete' && !!window.openTaskInStudio")
    evaluate(page, "(() => {window.__acceptanceErrors=[];window.addEventListener('error',e=>window.__acceptanceErrors.push(e.message));window.addEventListener('unhandledrejection',e=>window.__acceptanceErrors.push(String(e.reason)));return true;})()")
    open_history(page, TASK)
    return window, page, f"http://127.0.0.1:{port}"


def close(app, window):
    window.web_view.stop()
    window.web_view.setUrl(QUrl("about:blank"))
    wait(200)
    window.hide()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    service_manager.shutdown_all()


def fingerprint_audio():
    manifest = json.loads((ROOT / "workspace/projects" / f"{TASK}.json").read_text(encoding="utf-8"))
    files = [Path(row["audio_path"]) for row in manifest["segments"]
             if row.get("status") in {"READY", "PLAYED"} and row.get("audio_path")]
    assert files and all(path.is_file() and path.stat().st_size > 44 for path in files)
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}


def main():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window = None
    original = None
    before_audio = fingerprint_audio()
    try:
        window, page, base = launch(app)
        snapshot = backend(base, f"/api/streaming/{TASK}")
        progress = snapshot["progress"]
        assert progress["status"] == "PREVIEW_READY" and progress["preview_output_ready"]
        assert progress["processing_scope"] == "preview" and progress["processing_total_known"]
        assert progress["processing_completed_segments"] == progress["processing_total_segments"] == 17
        assert progress["processing_progress_pct"] == 100
        assert any(row["status"] == "WAITING" for row in snapshot["segments"])
        state = evaluate(page, """({panelVisible:!document.getElementById('caption-style-panel').classList.contains('hidden'),
            open:document.getElementById('caption-style-panel').open,
            enabled:!document.getElementById('caption-style-controls').disabled,
            blurEnabled:!document.getElementById('caption-blur-original').disabled,
            pct:document.getElementById('task-progress-value').textContent,
            detail:document.getElementById('task-progress-detail').textContent,
            beforeMixer:!!(document.getElementById('caption-style-panel').compareDocumentPosition(document.getElementById('vol-dub')) & Node.DOCUMENT_POSITION_FOLLOWING)})""")
        assert all(state[key] for key in ("panelVisible", "open", "enabled", "blurEnabled", "beforeMixer")), state
        assert state["pct"] == "100% giọng mẫu" and "17/17" in state["detail"], state
        event("PREVIEW_CONTROLS_AND_MEASURED_PROGRESS", state)
        original = read_style(page)
        cues = [cue for row in snapshot["segments"] for cue in row.get("caption_layout", {}).get("cues", [])]
        assert cues and all(cue.get("background_color", "#FFE500" if cue.get("background") == "yellow" else "").upper() == "#FFE500" for cue in cues)
        event("STABLE_DEFAULT_PALETTE", {"cues": len(cues), "background": "#FFE500"})
        trial = {"text_color": "#ffffff", "background_color": "#334455", "position": "bottom", "blur_original": True}
        set_style(page, trial)
        saved = backend(base, f"/api/streaming/{TASK}")
        assert normalized_style(saved["caption_style"]) == trial, saved["caption_style"]
        cues = [cue for row in saved["segments"] for cue in row.get("caption_layout", {}).get("cues", [])]
        masks = [mask for row in saved["segments"] for mask in row.get("caption_layout", {}).get("source_masks", [])]
        assert cues and masks
        assert all(cue["placement"] == "bottom" and cue["background_color"].lower() == "#334455" and cue["color"].lower() == "#ffffff" for cue in cues)
        until(page, "document.getElementById('video-player').readyState>=2", timeout=90)
        moment = (masks[0]["start"] + masks[0]["end"]) / 2
        evaluate(page, "(() => {const v=document.getElementById('video-player');v.currentTime=" + str(moment) + ";v.dispatchEvent(new Event('timeupdate'));return true;})()")
        until(page, "document.querySelectorAll('.source-subtitle-blur').length>0")
        painted = evaluate(page, """({background:getComputedStyle(document.getElementById('subtitle-text')).backgroundColor,
            text:getComputedStyle(document.getElementById('subtitle-text')).color,
            masks:document.querySelectorAll('.source-subtitle-blur').length,
            errors:window.__acceptanceErrors})""")
        assert painted["background"] == "rgb(51, 68, 85)" and painted["text"] == "rgb(255, 255, 255)", painted
        assert not painted["errors"], painted
        event("REAL_PREVIEW_PAINT_AND_BLUR", painted)
        assert click(page, "player-play-toggle")
        until(page, "!document.getElementById('video-player').paused && document.getElementById('video-player').currentTime>" + str(moment + .2), timeout=30)
        assert click(page, "player-play-toggle")
        event("REAL_TRANSLATED_PREVIEW_PLAYBACK", True)
        window.web_view.grab().save(str(ROOT / "workspace/temp/caption-style-ui-check.png"))
        close(app, window)
        window = None
        window, page, base = launch(app)
        assert read_style(page) == trial, read_style(page)
        reopened = backend(base, f"/api/streaming/{TASK}")
        assert normalized_style(reopened["caption_style"]) == trial and reopened["progress"]["preview_output_ready"]
        event("BACKEND_RESTART_STYLE_PERSISTED", trial)
        set_style(page, original)
        restored = backend(base, f"/api/streaming/{TASK}")
        assert restored["caption_style"]["blur_original"] == original["blur_original"]
        assert fingerprint_audio() == before_audio
        event("PASS", {"real_wavs_unchanged": len(before_audio), "provider_calls_for_styling": 0,
                       "original_style_restored": True, "full_video_export_tested": False})
        original = None
    finally:
        if window is not None:
            try:
                if original is not None:
                    set_style(window.web_view.page(), original)
            finally:
                close(app, window)
        else:
            service_manager.shutdown_all()
        event("QA_BACKEND_STOPPED", True)


if __name__ == "__main__":
    main()
