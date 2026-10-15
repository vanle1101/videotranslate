"""Opt-in real production History -> Review -> Stop -> Review acceptance.

Uses the named retained project in place, keeps its media, and exits its own
offscreen backend. This is cancellation acceptance, not translation quality.
"""
import argparse
import gc
import hashlib
import json
import time
import urllib.request

from check_production_ui import (QApplication, StudioMainWindow, service_manager,
    QCoreApplication, QEvent, QUrl, backend, click, evaluate, event,
    open_history, until, wait)
from pathlib import Path
from core.streaming.session_store import _read


def fingerprint(state):
    return {row['id']: {'text': row.get('final_vi'), 'source': row.get('text_zh'),
        'revision': row.get('revision'), 'start': row['start'], 'end': row['end'],
        'audio': hashlib.sha256(Path(row['audio_path']).read_bytes()).hexdigest()
            if row.get('audio_path') else None} for row in state['segments']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task_id', choices=['5bea8fe5'])
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    assert app.platformName() == 'offscreen'
    app.setQuitOnLastWindowClosed(False)
    window, base = None, None
    try:
        port = service_manager.start_backend(timeout=30)
        base = f'http://127.0.0.1:{port}'
        window = StudioMainWindow(port)
        page = window.web_view.page()
        page.setAudioMuted(True)
        window.show()
        until(page, "document.readyState==='complete' && !!window.openTaskInStudio")
        evaluate(page, "(() => {window.__qaAlerts=[];window.alert=m=>window.__qaAlerts.push(String(m));return true;})()")
        open_history(page, args.task_id)
        before = backend(base, f'/api/streaming/{args.task_id}')
        assert not any(row['status'] == 'FAILED' for row in before['segments'])
        original = fingerprint(_read(args.task_id))
        observations = []
        for attempt in range(2):
            until(page, "!document.getElementById('btn-review-worker').disabled && !document.getElementById('btn-review-worker').classList.contains('hidden')")
            assert click(page, 'btn-review-worker')
            until(page, "!document.getElementById('btn-stop-worker').disabled && document.getElementById('task-progress').dataset.status==='RUNNING'")
            started = time.monotonic()
            assert click(page, 'btn-stop-worker')
            until(page, "document.getElementById('task-progress').dataset.status==='STOPPED'", timeout=120)
            stopped = backend(base, f'/api/streaming/{args.task_id}')
            event('AFTER_STOP', {'progress':{k:stopped['progress'].get(k) for k in ('status','phase','can_review','can_stop')}, 'ui':evaluate(page,
                "({reviewDisabled:document.getElementById('btn-review-worker').disabled,reviewHidden:document.getElementById('btn-review-worker').classList.contains('hidden'),status:document.getElementById('task-progress').dataset.status,alerts:window.__qaAlerts})")})
            until(page, "!document.getElementById('btn-review-worker').disabled && !document.getElementById('btn-review-worker').classList.contains('hidden')")
            state = backend(base, f'/api/streaming/{args.task_id}')
            assert state['progress']['can_review'] and state['progress']['status'] == 'STOPPED', state['progress']
            assert fingerprint(_read(args.task_id)) == original, 'Cancelled review changed retained speech'
            observations.append({'attempt': attempt+1, 'stop_seconds': round(time.monotonic()-started,3),
                'can_review': True, 'speech_preserved': True, 'backend_status': state['progress']['status']})
        assert not evaluate(page, 'window.__qaAlerts')
        event('PASS_REVIEW_RESTART', {'task_id':args.task_id,'attempts':observations,
            'missing_speech_ids':state['missing_speech_ids'], 'full_output_blocked':state['final_output_blocked']})
    finally:
        try:
            if base:
                state = backend(base, f'/api/streaming/{args.task_id}')
                if state['progress']['status'] in {'RUNNING', 'PAUSED', 'CANCELLING'}:
                    request = urllib.request.Request(base + f'/api/tasks/{args.task_id}/stop', data=b'', method='POST')
                    with urllib.request.urlopen(request, timeout=120) as response:
                        event('CHECKPOINT_STOP', json.load(response))
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
