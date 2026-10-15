"""Opt-in real History -> partial MP4 -> playback -> reopen acceptance.

Uses the real production Qt page, HTTP server and FFmpeg. No mock provider or
media. The selected retained job is not translated again or edited.
"""
import argparse
import gc
import hashlib
import json
import subprocess
import time

from check_production_ui import (ROOT, QApplication, StudioMainWindow,
    service_manager, QCoreApplication, QEvent, QUrl, backend, click, evaluate,
    event, open_history, reload_page, until, wait)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_id", choices=["5bea8fe5", "76f5aa6b", "38680e43"])
    parser.add_argument("--reuse", action="store_true", help="Verify a saved partial after a fresh backend restart without rendering again.")
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    assert app.platformName() == "offscreen"
    app.setQuitOnLastWindowClosed(False)
    window = None
    try:
        port = service_manager.start_backend(timeout=30)
        base = f"http://127.0.0.1:{port}"
        window = StudioMainWindow(port)
        window.resize(1366, 860)
        page = window.web_view.page()
        page.setAudioMuted(True)
        window.show()
        until(page, "document.readyState==='complete' && !!window.openTaskInStudio")
        evaluate(page, "(() => {window.__qaAlerts=[];window.alert=m=>window.__qaAlerts.push(String(m));return true;})()")
        open_history(page, args.task_id)
        before = backend(base, f"/api/streaming/{args.task_id}")
        def dialogue(snapshot):
            return hashlib.sha256(json.dumps(snapshot['segments'],sort_keys=True).encode()).hexdigest()
        digest = dialogue(before)
        assert before['partial_export_available'], before.get('partial_export_reason')
        assert before['final_output_blocked'] or before['final_media_blocked'], 'Use a genuinely incomplete project'
        event('PREFIX_BEFORE', {key:before.get(key) for key in ('task_id','partial_available_seconds','duration','final_output_blocked','final_media_blocked','output_filename')})
        started = time.monotonic()
        if args.reuse:
            partial = before['partial_output']
            assert partial, 'No validated saved partial after backend restart'
            state = {'scope':partial['scope'],'output_filename':partial['output_filename'],'partial_output':partial}
        else:
            until(page, "!document.getElementById('btn-export-partial').disabled")
            assert click(page, 'btn-export-partial')
            event('SCOPE_CONFIRMATION', evaluate(page, "document.getElementById('export-scope-summary').textContent"))
            assert click(page, 'btn-confirm-export')
            until(page, "!document.getElementById('btn-cancel-export').disabled || window.__qaAlerts.length", timeout=30)
            assert not evaluate(page, 'window.__qaAlerts'), evaluate(page, 'window.__qaAlerts')
            deadline, previous = started + 900, None
            while time.monotonic() < deadline:
                state = backend(base, f'/api/streaming/export-hq/status/{args.task_id}')
                stage = (state.get('status'), state.get('stage'))
                if stage != previous:
                    event('PARTIAL_EXPORT', state)
                    previous = stage
                if state.get('status') in {'FAILED','CANCELLED'}:
                    raise AssertionError(state)
                if state.get('status') == 'COMPLETED':
                    break
                wait(1000)
            else:
                raise AssertionError('Partial rendering exceeded 15 minutes')
        assert state['scope'] == 'ready_prefix'
        until(page, "!document.getElementById('partial-result').classList.contains('hidden')")
        path = (ROOT/'workspace/outputs'/state['output_filename']).resolve()
        assert path.parent == (ROOT/'workspace/outputs').resolve() and path.is_file()
        probe = subprocess.run(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(path)],
            capture_output=True,text=True,timeout=30,check=True)
        metadata = json.loads(probe.stdout)
        assert {s['codec_type'] for s in metadata['streams']} >= {'audio','video'}
        assert abs(float(metadata['format']['duration']) - state['partial_output']['duration']) < .15
        decode = subprocess.run(['ffmpeg','-v','error','-xerror','-i',str(path),'-f','null','-'],
            capture_output=True,text=True,timeout=300)
        assert decode.returncode == 0 and not decode.stderr.strip(), decode.stderr
        if not args.reuse:
            assert click(page, 'btn-close-export-modal')
        assert click(page, 'btn-preview-partial')
        until(page, "document.getElementById('result-preview-video').readyState>=2 && document.getElementById('result-preview-video').currentTime>.3", timeout=120)
        media = evaluate(page, "(() => {const v=document.getElementById('result-preview-video');return {duration:v.duration,time:v.currentTime,ready:v.readyState,width:v.videoWidth,error:v.error?.message || null};})()")
        assert media['width'] > 0 and not media['error'], media
        assert click(page, 'btn-close-result-preview')
        reload_page(page)
        open_history(page, args.task_id)
        until(page, "!document.getElementById('partial-result').classList.contains('hidden')")
        after = backend(base, f'/api/streaming/{args.task_id}')
        assert dialogue(after) == digest, 'Partial export changed dialogue/audio metadata'
        assert after.get('output_filename') == before.get('output_filename'), 'Partial export replaced full publication'
        assert after['partial_output']['output_filename'] == path.name
        assert evaluate(page, "document.getElementById('btn-export-hq').disabled && document.getElementById('btn-save-result').disabled")
        event('PASS_PARTIAL', {'task_id':args.task_id,'file':str(path),'bytes':path.stat().st_size,
            'seconds':state['partial_output']['duration'],'elapsed_seconds':round(time.monotonic()-started,3),
            'full_decode':True,'production_player':media,'reopen':True,'full_gates_retained':True,
            'dialogue_unchanged':True,'alerts':evaluate(page,'window.__qaAlerts || []')})
    finally:
        if window is not None:
            window.web_view.stop()
            window.web_view.setUrl(QUrl('about:blank'))
            wait(200)
            window.hide()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
            app.processEvents()
        service_manager.shutdown_all()
        gc.collect()
        stopped = service_manager.server_thread is None
        event('BACKEND_STOPPED', stopped)
        assert stopped


if __name__ == '__main__':
    main()
