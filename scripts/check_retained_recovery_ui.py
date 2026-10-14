"""Real retained-project retry through offscreen production UI; no mocks."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.check_production_ui import (QApplication, StudioMainWindow, service_manager,
    open_history, until, click, backend, evaluate, wait, event, QUrl, QCoreApplication,
    QEvent, export_through_ui)
from core.streaming.session_store import _read, _valid_row_audio


def fingerprint(row):
    path = row.get('audio_path')
    return {'text':row.get('final_vi'), 'revision':row.get('revision'),
            'timing':[row.get(k) for k in ('start','end','dub_start','dub_end')],
            'audio':hashlib.sha256(Path(path).read_bytes()).hexdigest() if path else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=int, default=1800)
    args = parser.parse_args()
    assert 30 <= args.timeout <= 7200
    task = '76f5aa6b'
    original = _read(task)
    manual = {r['id']:fingerprint(r) for r in original['segments']
              if (r.get('verification') or {}).get('status') == 'manual'}
    ids = [r['id'] for r in original['segments']]
    app = QApplication.instance() or QApplication([])
    assert app.platformName() == 'offscreen'
    app.setQuitOnLastWindowClosed(False)
    window, base = None, None
    try:
        assert not service_manager.is_running and not service_manager.server_thread
        port = service_manager.start_backend(timeout=30)
        base = f'http://127.0.0.1:{port}'
        window = StudioMainWindow(port)
        page = window.web_view.page()
        page.setAudioMuted(True)
        window.show()
        until(page, "document.readyState==='complete' && !!window.openTaskInStudio")
        open_history(page, task)
        state = backend(base, f'/api/streaming/{task}')
        assert state['progress']['can_retry'], state['progress']
        assert click(page, 'btn-retry-worker')
        until(page, "document.getElementById('task-progress').dataset.status==='RUNNING'")
        started = time.monotonic()
        deadline, last = started + args.timeout, None
        while time.monotonic() < deadline:
            state = backend(base, f'/api/streaming/{task}')
            progress = state['progress']
            current = {k:progress.get(k) for k in ('status','phase','stage','segment_states',
                'retry_pending_count','retry_next_attempt_at','can_export')}
            if current != last:
                event('RECOVERY_PROGRESS', current)
                last = current
            if progress['status'] in {'FAILED','STOPPED','CANCELLED','COMPLETED','PREPARED'}:
                break
            wait(1000)
        else:
            event('RECOVERY_OBSERVATION_DEADLINE', args.timeout)
            return 2
        saved = _read(task)
        valid = sum(bool(r.get('audio_path')) and _valid_row_audio(r,r['audio_path']) for r in saved['segments'])
        event('RECOVERY_RESULT', {'valid_audio':valid,'total':len(ids),
            'failed':[r['id'] for r in saved['segments'] if r['status']=='FAILED'],
            'review_required':sum(bool(r.get('needs_review')) for r in saved['segments']),
            'manual_preserved':len(manual)})
        if valid != len(ids):
            return 1
        export_through_ui(page,base,task)
        return 0
    finally:
        try:
            if base:
                state = backend(base, f'/api/streaming/{task}')
                if state['progress']['status'] in {'RUNNING','PAUSED'}:
                    req = urllib.request.Request(base+f'/api/tasks/{task}/stop', data=b'', method='POST')
                    with urllib.request.urlopen(req,timeout=60) as response:
                        event('CHECKPOINT_STOP',json.load(response))
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
            event('OWNED_BACKEND_STOPPED', service_manager.server_thread is None)
            # Verify preservation even after a deadline, cancellation, or
            # provider failure, once all native checkpoint writers drained.
            saved = _read(task)
            assert [r['id'] for r in saved['segments']] == ids
            for row in saved['segments']:
                if row['id'] in manual:
                    assert fingerprint(row) == manual[row['id']], f'Manual row changed: {row["id"]}'
            event('FINAL_CHECKPOINT', {'state':saved.get('state'),
                'valid_audio':sum(bool(r.get('audio_path')) and _valid_row_audio(r,r['audio_path'])
                                  for r in saved['segments']),
                'total':len(ids), 'manual_preserved':len(manual),
                'review_required':sum(bool(r.get('needs_review')) for r in saved['segments'])})


if __name__ == '__main__':
    raise SystemExit(main())
