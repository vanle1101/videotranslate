"""Opt-in acceptance of real saved projects through the production Qt UI.

Never mocks HTTP, sockets, providers, or media. The offscreen platform and page
audio mute keep this suitable for unattended checks. Only explicitly named QA
projects may be modified. Existing user projects are never selected implicitly.
"""
import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import urllib.request

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer, QUrl
from PySide6.QtWidgets import QApplication
from desktop_app import StudioMainWindow
from core.services.service_manager import service_manager


def wait(milliseconds=100):
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def evaluate(page, expression):
    values = []
    loop = QEventLoop()

    def receive(value):
        values.append(value)
        loop.quit()

    page.runJavaScript("JSON.stringify(" + expression + ")", receive)
    QTimer.singleShot(10000, loop.quit)
    loop.exec()
    assert values and values[0], "JavaScript did not return a serializable result"
    return json.loads(values[0])


def until(page, expression, timeout=30):
    deadline = time.monotonic() + timeout
    state = None
    while time.monotonic() < deadline:
        state = evaluate(page, expression)
        if state:
            return state
        wait()
    raise AssertionError(f"UI condition timed out: {expression}; last={state!r}")


def click(page, element_id):
    return evaluate(page, "(() => { const e=document.getElementById(" +
                    json.dumps(element_id) + "); if (!e || e.disabled) return false; e.click(); return true; })()")


def reload_page(page):
    loaded = []

    def finished(success):
        loaded.append(success)

    page.loadFinished.connect(finished)
    try:
        page.triggerAction(page.WebAction.Reload)
        deadline = time.monotonic() + 30
        while not loaded and time.monotonic() < deadline:
            wait()
        assert loaded and loaded[-1], "Production page failed to reload"
        until(page, "document.readyState==='complete' && !!window.openTaskInStudio")
    finally:
        page.loadFinished.disconnect(finished)


def backend(base, path):
    with urllib.request.urlopen(base + path, timeout=30) as response:
        return json.load(response)


def event(name, value):
    print(name, json.dumps(value, ensure_ascii=False), flush=True)


def open_history(page, task_id):
    assert click(page, "tab-tasks")
    selector = "#tasks-table-body button[onclick=\"window.openTaskInStudio('" + task_id + "')\"]"
    until(page, "!!document.querySelector(" + json.dumps(selector) + ")")
    assert evaluate(page, "(() => {document.querySelector(" + json.dumps(selector) + ").click(); return true;})()")
    until(page, "!document.getElementById('view-studio').classList.contains('hidden') && document.querySelectorAll('[id^=seg-vi-]').length > 0")
    wait(300)


def ui_state(page):
    return evaluate(page, """({status:document.getElementById('task-progress').dataset.status,
        stage:document.getElementById('task-progress-stage').textContent,
        transcriptCount:document.querySelectorAll('[id^=seg-vi-]').length,
        captionEnabled:!document.getElementById('caption-style-controls').disabled,
        resultVisible:!document.getElementById('task-result-link').classList.contains('hidden'),
        resultUrl:document.getElementById('task-result-link').getAttribute('href'),
        voice:document.getElementById('voice-select').value,
        rowStates:Array.from(document.querySelectorAll('.transcript-state'),e=>e.textContent),
        bridge:!!window.desktopBridge,
        errors:window.__acceptanceErrors || []})""")


def check_playback(page):
    assert click(page, "task-result-link")
    until(page, "!document.getElementById('result-preview-modal').classList.contains('hidden')")
    state_expression = """(() => {const v=document.getElementById('result-preview-video');
        return {ready:v.readyState,time:v.currentTime,duration:v.duration,width:v.videoWidth,height:v.videoHeight,
          paused:v.paused,source:v.currentSrc,error:v.error ? {code:v.error.code,message:v.error.message}:null,
          message:document.getElementById('result-preview-status').textContent};})()"""
    deadline = time.monotonic() + 120
    state = {}
    while time.monotonic() < deadline:
        state = evaluate(page, state_expression)
        if state["ready"] >= 2 and state["time"] >= .3 and not state["paused"]:
            break
        if "Chưa phát được" in state["message"]:
            break
        wait(200)
    event("RESULT_PLAYBACK", state)
    assert state["error"] is None and state["ready"] >= 2 and state["time"] >= .3, state
    assert state["width"] > 0 and state["height"] > 0 and state["duration"] > 0, state
    assert click(page, "btn-close-result-preview")
    return state


def read_style(page):
    return evaluate(page, """({text_color:document.getElementById('caption-text-color').value,
        background_color:document.getElementById('caption-auto-background').checked ? null : document.getElementById('caption-background-color').value,
        position:document.getElementById('caption-position').value,
        blur_original:document.getElementById('caption-blur-original').checked})""")


def set_style(page, style):
    assert not evaluate(page, "document.getElementById('caption-style-controls').disabled")
    evaluate(page, """(() => {const s=""" + json.dumps(style) + """;
        document.getElementById('caption-style-panel').open=true;
        document.getElementById('caption-text-color').value=s.text_color;
        document.getElementById('caption-auto-background').checked=s.background_color===null;
        document.getElementById('caption-background-color').value=s.background_color || '#ffe500';
        document.getElementById('caption-position').value=s.position;
        document.getElementById('caption-blur-original').checked=s.blur_original;
        document.getElementById('caption-position').dispatchEvent(new Event('change',{bubbles:true}));
        return true;})()""")
    until(page, "document.getElementById('caption-style-status').textContent.startsWith('Đã cập nhật') && !document.getElementById('caption-style-controls').disabled")
    assert evaluate(page, "document.getElementById('caption-style-status').dataset.error") == "false"


def check_retry_stop(page, base, task_id):
    """Run actual worker requests through buttons, stop, and inspect durable audio."""
    assert task_id == "e978c201", "Only the existing stopped lifecycle QA task may retry"
    snapshot = backend(base, f"/api/streaming/{task_id}")
    assert snapshot["progress"].get("can_retry"), snapshot["progress"]
    manifest = json.loads((ROOT / "workspace/projects" / f"{task_id}.json").read_text(encoding="utf-8"))
    paths = [Path(row["audio_path"]) for row in manifest["segments"]
             if row["status"] in {"READY", "PLAYED"} and row.get("audio_path")]
    before = {str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    assert before, "Lifecycle QA must start with real preserved READY audio"
    assert click(page, "btn-retry-worker")
    until(page, "document.getElementById('task-progress').dataset.status==='RUNNING'", timeout=30)
    event("RETRY_RUNNING", ui_state(page))
    wait(1200)
    started = time.monotonic()
    assert click(page, "btn-stop-worker")
    until(page, "['STOPPED','CANCELLED'].includes(document.getElementById('task-progress').dataset.status)", timeout=30)
    stopped = backend(base, f"/api/streaming/{task_id}")
    assert stopped["progress"]["status"] in {"STOPPED","CANCELLED"}, stopped["progress"]
    stopped_rows = [(row["id"],row["status"],row.get("revision"),row.get("final_vi")) for row in stopped["segments"]]
    elapsed = round(time.monotonic()-started,3)
    wait(5000)
    after = {str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    assert after == before, "Stop changed already completed audio"
    late = backend(base, f"/api/streaming/{task_id}")
    assert [(row["id"],row["status"],row.get("revision"),row.get("final_vi")) for row in late["segments"]] == stopped_rows, "Late result mutated stopped task"
    assert late["progress"].get("can_retry"), late["progress"]
    event("RETRY_STOP", {"stop_seconds":elapsed,"preserved_ready_wavs":len(before),"late_writes":False,"ui_status":ui_state(page)["status"],"backend_status":late["progress"]["status"],"can_retry":True})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_id", choices=["38680e43", "e978c201", "5258c661"])
    parser.add_argument("--style", action="store_true", help="Save/reload a temporary QA caption style and restore the prior style. Marks its old render outdated.")
    parser.add_argument("--skip-playback", action="store_true")
    parser.add_argument("--retry-stop", action="store_true", help="Retry and stop real work through UI (e978c201 only).")
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    assert app.platformName() == "offscreen"
    app.setQuitOnLastWindowClosed(False)
    window = None
    original_style = None
    try:
        port = service_manager.start_backend(timeout=30)
        base = f"http://127.0.0.1:{port}"
        event("BACKEND", {"pid":os.getpid(), "port":port})
        window = StudioMainWindow(port)
        window.resize(1366, 860)
        page = window.web_view.page()
        page.setAudioMuted(True)
        window.show()  # Offscreen QPA only: no native desktop window is created.
        until(page, "document.readyState==='complete' && !!window.openTaskInStudio && !!document.getElementById('tab-tasks')")
        evaluate(page, """(() => {window.__acceptanceErrors=[];
            window.addEventListener('error', e=>window.__acceptanceErrors.push(String(e.message)));
            window.addEventListener('unhandledrejection',e=>window.__acceptanceErrors.push(String(e.reason)));
            return true;})()""")
        open_history(page, args.task_id)
        snapshot = backend(base, f"/api/streaming/{args.task_id}")
        state = ui_state(page)
        event("HISTORY_RESTORED", state)
        assert state["status"] == snapshot["progress"]["status"], (state,snapshot["progress"])
        assert state["transcriptCount"] == len(snapshot["segments"]), state
        assert state["bridge"] and not state["errors"], state
        if not args.skip_playback:
            assert state["resultVisible"], state
            check_playback(page)
        if args.retry_stop:
            check_retry_stop(page, base, args.task_id)
        if args.style:
            original_style = read_style(page)
            trial_style = {"text_color":"#ffffff", "background_color":"#334455", "position":"bottom", "blur_original":False}
            set_style(page, trial_style)
            saved = backend(base, f"/api/streaming/{args.task_id}")
            assert {k:v.lower() if isinstance(v,str) else v for k,v in saved["caption_style"].items()} == trial_style, saved["caption_style"]
            assert saved["output_outdated"] and not ui_state(page)["resultVisible"]
            reload_page(page)
            open_history(page,args.task_id)
            restored_style = read_style(page)
            assert restored_style == trial_style, restored_style
            event("STYLE_SAVE_RELOAD", {"style":restored_style,"revision":saved["caption_style_revision"],"old_output_hidden":True})
            set_style(page, original_style)
            event("STYLE_RESTORED", read_style(page))
            original_style = None
        state = ui_state(page)
        assert not state["errors"], state
        event("PASS", {"task_id":args.task_id,"offscreen":True,"audio_muted":page.isAudioMuted(),"state":state})
    finally:
        if window is not None:
            if original_style is not None:
                try:
                    set_style(window.web_view.page(), original_style)
                except Exception as error:
                    event("STYLE_RESTORE_FAILED", str(error))
            window.web_view.stop()
            window.web_view.setUrl(QUrl("about:blank"))
            wait(200)
            window.hide()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
            app.processEvents()
            window = None
        service_manager.shutdown_all()
        gc.collect()
        event("BACKEND_STOPPED", True)


if __name__ == "__main__":
    main()
