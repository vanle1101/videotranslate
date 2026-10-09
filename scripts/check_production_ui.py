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
import urllib.error
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


def save_edit(page, base, task_id, sid, text):
    """Save through the real editor and check its durable, readable WAV."""
    assert click(page, f"seg-vi-{sid}")
    until(page, f"document.getElementById('seg-row-{sid}').dataset.editing==='true'")
    assert evaluate(page, "(() => {const e=document.getElementById(" + json.dumps(f"seg-input-{sid}") +
        ");e.value=" + json.dumps(text) + ";e.dispatchEvent(new Event('input',{bubbles:true}));return true;})()")
    selector = f"#seg-row-{sid} .transcript-editor-actions button"
    assert evaluate(page, "(() => {const b=document.querySelector(" + json.dumps(selector) +
        ");if(!b||b.disabled)return false;b.click();return true;})()")
    until(page, f"document.getElementById('seg-row-{sid}').dataset.editing==='false'", timeout=180)
    snapshot = backend(base, f"/api/streaming/{task_id}")
    row = next(row for row in snapshot['segments'] if row['id'] == sid)
    assert row['final_vi'] == text and row['status'] in {'READY', 'PLAYED'}
    from core.streaming.session_store import _read, _valid_row_audio
    durable = next(row for row in _read(task_id)['segments'] if row['id'] == sid)
    assert durable['final_vi'] == text and durable['revision'] == row['revision']
    assert _valid_row_audio(durable, durable['audio_path'])
    return row


def export_through_ui(page, base, task_id):
    """Wait for actual fresh bytes, then verify the production result player."""
    prior = ROOT / 'workspace/outputs' / f'douyin_translated_{task_id}_hq.mp4'
    stamp = prior.stat().st_mtime_ns if prior.is_file() else None
    assert click(page, 'btn-export-hq')
    until(page, "!document.getElementById('export-modal').classList.contains('hidden')")
    assert click(page, 'btn-confirm-export')
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        try:
            state = backend(base, f'/api/streaming/export-hq/status/{task_id}')
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
            state = {}
        assert state.get('status') not in {'FAILED', 'CANCELLED'}, state
        if state.get('status') == 'COMPLETED':
            snapshot = backend(base, f'/api/streaming/{task_id}')
            output = (ROOT / 'workspace/outputs' / snapshot['output_filename']).resolve()
            assert output.parent == (ROOT / 'workspace/outputs').resolve()
            assert output.is_file() and output.stat().st_size > 0 and not snapshot['output_outdated']
            assert output != prior.resolve() or output.stat().st_mtime_ns != stamp
            until(page, "!document.getElementById('task-result-link').classList.contains('hidden')")
            event('EDIT_STYLE_EXPORTED', {'file':str(output),'bytes':output.stat().st_size})
            check_playback(page)
            return
        wait(200)
    raise AssertionError('Real export after editing exceeded five minutes')


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
    parser.add_argument("--review", action="store_true", help="Run real Muse review and automatic export through the UI before playback.")
    parser.add_argument("--edit", action="store_true", help="Edit QA row 6 through UI, reopen it, and restore its original wording with real speech.")
    parser.add_argument("--export", action="store_true", help="Export after requested edit/style checks and verify real playback.")
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    assert app.platformName() == "offscreen"
    app.setQuitOnLastWindowClosed(False)
    window = None
    original_style = None
    original_edit = None
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
        if args.review:
            output_root = (ROOT / "workspace/outputs").resolve()
            prior_name = snapshot.get("output_filename")
            prior_file = output_root / prior_name if prior_name else None
            prior_stamp = prior_file.stat().st_mtime_ns if prior_file and prior_file.is_file() else None
            assert click(page, "btn-review-worker")
            until(page, "document.getElementById('task-progress').dataset.status==='RUNNING'")
            deadline = time.monotonic() + 1800
            previous = None
            while time.monotonic() < deadline:
                snapshot = backend(base, f"/api/streaming/{args.task_id}")
                progress = snapshot["progress"]
                current = (progress.get("status"), progress.get("stage"))
                if current != previous:
                    event("REVIEW_PROGRESS", progress)
                    previous = current
                if progress.get("status") in {"FAILED", "STOPPED", "CANCELLED"}:
                    raise AssertionError(f"Real review failed: {progress}")
                try:
                    export = backend(base, f"/api/streaming/export-hq/status/{args.task_id}")
                except urllib.error.HTTPError as error:
                    if error.code != 404:
                        raise
                    export = {}  # Review has not scheduled the real export yet.
                if export.get("status") in {"FAILED", "CANCELLED"}:
                    raise AssertionError(f"Real export failed: {export}")
                if snapshot.get("output_filename") and progress.get("review_summary", {}).get("status") == "completed":
                    output = (output_root / snapshot["output_filename"]).resolve()
                    assert output.parent == output_root and output.is_file(), snapshot["output_filename"]
                    assert not snapshot.get("output_outdated"), "Review returned an outdated render"
                    assert output.stat().st_size > 0 and export.get("status") == "COMPLETED", export
                    if prior_file == output:
                        assert output.stat().st_mtime_ns != prior_stamp, "Review reused the old exported file"
                    until(page, "!document.getElementById('task-result-link').classList.contains('hidden')")
                    event("REVIEW_EXPORTED", {"file":snapshot["output_filename"], "review":progress["review_summary"]})
                    break
                wait(1000)
            else:
                raise AssertionError("Real review/export exceeded 30 minutes")
            state = ui_state(page)
        if not args.skip_playback:
            assert state["resultVisible"], state
            check_playback(page)
        if args.retry_stop:
            check_retry_stop(page, base, args.task_id)
        if args.edit:
            assert args.task_id == '38680e43', 'Only the named completed media QA project may be edited'
            row = next(row for row in snapshot['segments'] if row['id'] == 6)
            original_edit = (row['id'], row['final_vi'])
            changed = row['final_vi'].rstrip('.!?') + '!'
            assert changed != row['final_vi']
            edited = save_edit(page, base, args.task_id, row['id'], changed)
            assert edited['revision'] > row['revision']
            reload_page(page)
            open_history(page, args.task_id)
            reopened = backend(base, f'/api/streaming/{args.task_id}')
            assert next(item for item in reopened['segments'] if item['id'] == row['id'])['final_vi'] == changed
            event('EDIT_SAVE_REOPEN', {'id':row['id'],'text':changed,'revision':edited['revision']})
            save_edit(page, base, args.task_id, row['id'], row['final_vi'])
            original_edit = None
        if args.style:
            original_style = read_style(page)
            trial_style = {"text_color":"#ffffff", "background_color":"#334455", "position":"bottom", "blur_original":False}
            set_style(page, trial_style)
            saved = backend(base, f"/api/streaming/{args.task_id}")
            assert {k:v.lower() if isinstance(v,str) else v for k,v in saved["caption_style"].items()} == trial_style, saved["caption_style"]
            assert saved["output_outdated"] and not ui_state(page)["resultVisible"]
            cues = [cue for row in saved["segments"] for cue in row.get("caption_layout",{}).get("cues",[])]
            assert cues and all(cue["placement"] == "bottom" and cue["color"].lower() == "#ffffff" and cue.get("background_color","").lower() == "#334455" for cue in cues)
            reload_page(page)
            open_history(page,args.task_id)
            restored_style = read_style(page)
            assert restored_style == trial_style, restored_style
            event("STYLE_SAVE_RELOAD", {"style":restored_style,"revision":saved["caption_style_revision"],"old_output_hidden":True})
            if saved.get("has_subtitle_regions"):
                blur_style = {**trial_style, "blur_original":True}
                set_style(page,blur_style)
                blurred = backend(base, f"/api/streaming/{args.task_id}")
                masks = [mask for row in blurred["segments"] for mask in row.get("caption_layout",{}).get("source_masks",[])]
                assert blurred["caption_style"]["blur_original"] is True and masks, blurred["caption_style"]
                event("ORIGINAL_SUBTITLE_BLUR", {"saved":True,"source_masks":len(masks)})
            set_style(page, original_style)
            event("STYLE_RESTORED", read_style(page))
            original_style = None
        if args.export:
            export_through_ui(page, base, args.task_id)
        state = ui_state(page)
        assert not state["errors"], state
        event("PASS", {"task_id":args.task_id,"offscreen":True,"audio_muted":page.isAudioMuted(),"state":state})
    finally:
        if window is not None:
            if original_edit is not None:
                try:
                    save_edit(window.web_view.page(), base, args.task_id, *original_edit)
                except Exception as error:
                    event('EDIT_RESTORE_FAILED', str(error))
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
