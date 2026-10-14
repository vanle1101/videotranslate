"""Opt-in real retained-project review despite independent speech failures.

Uses the production Qt page offscreen, real providers and persisted media.
This checks partial review ownership, not completion of the full MP4.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.check_production_ui import (
    QApplication, StudioMainWindow, service_manager, open_history, until, click,
    backend, evaluate, wait, event, QUrl, QCoreApplication, QEvent,
)
from core.streaming.session_store import _read, _valid_row_audio


def owned_state(row):
    fields = ('id', 'text_zh', 'final_vi', 'audio_path', 'dub_start', 'dub_end', 'revision')
    result = {key: row.get(key) for key in fields}
    path = row.get('audio_path')
    result['audio_sha256'] = hashlib.sha256(Path(path).read_bytes()).hexdigest() if path else None
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', choices=['76f5aa6b'], default='76f5aa6b')
    parser.add_argument('--timeout', type=int, default=1200)
    args = parser.parse_args()
    assert 1 <= args.timeout <= 1800
    original = _read(args.task)
    rows = original['segments']
    original_ids = [row['id'] for row in rows]
    failures = {row['id']: owned_state(row) for row in rows if row['status'] == 'FAILED'}
    manual = {row['id']: owned_state(row) for row in rows
              if (row.get('verification') or {}).get('status') == 'manual'}
    assert failures and any(row.get('needs_review') and row['status'] in {'READY','PLAYED'} for row in rows)
    app = QApplication.instance() or QApplication([])
    assert app.platformName() == 'offscreen'
    app.setQuitOnLastWindowClosed(False)
    window = None
    started = False
    base = None
    try:
        assert not service_manager.is_running and not service_manager.server_thread
        port = service_manager.start_backend(timeout=30)
        base = f'http://127.0.0.1:{port}'
        event('BACKEND', {'port':port, 'task':args.task})
        window = StudioMainWindow(port)
        page = window.web_view.page()
        page.setAudioMuted(True)
        window.show()
        until(page, "document.readyState==='complete' && !!window.openTaskInStudio")
        open_history(page, args.task)
        snapshot = backend(base, f'/api/streaming/{args.task}')
        assert snapshot['progress']['can_review'], snapshot['progress']
        until(page, "!document.getElementById('btn-review-worker').classList.contains('hidden') && !document.getElementById('btn-review-worker').disabled")
        assert click(page, 'btn-review-worker')
        started = True
        until(page, "document.getElementById('task-progress').dataset.status==='RUNNING'")
        deadline = time.monotonic() + args.timeout
        previous = None
        while time.monotonic() < deadline:
            snapshot = backend(base, f'/api/streaming/{args.task}')
            progress = snapshot['progress']
            current = {key: progress.get(key) for key in
                       ('status','stage','review_scope','reviewing_segment_ids','review_summary','segment_states')}
            if current != previous:
                event('PARTIAL_REVIEW_PROGRESS', current)
                previous = current
            assert progress['status'] != 'COMPLETED', 'Speech failures must not become completed'
            assert not snapshot.get('output_filename'), 'Partial review must not publish a final video'
            if progress['status'] in {'FAILED','STOPPED','CANCELLED'}:
                break
            wait(5000)
        else:
            event('PARTIAL_REVIEW_DEADLINE', {'seconds':args.timeout})
            return 2
        saved = _read(args.task)
        assert [row['id'] for row in saved['segments']] == original_ids
        for row in saved['segments']:
            expected = manual.get(row['id'], failures.get(row['id']))
            if expected is not None:
                assert owned_state(row) == expected, f'Out-of-scope speech changed: {row["id"]}'
        required = sum(bool(row.get('needs_review')) for row in saved['segments'])
        valid = sum(bool(row.get('audio_path')) and _valid_row_audio(row, row['audio_path']) for row in saved['segments'])
        event('PARTIAL_REVIEW_RESULT', {'task':args.task, 'review_required':required,
            'valid_audio':valid, 'preserved_failed_rows':len(failures), 'preserved_manual_rows':len(manual),
            'review_status':progress.get('review_summary',{}).get('status'),
            'ui_export_disabled':evaluate(page,"document.getElementById('btn-export-hq').disabled"),
            'full_output_complete':False})
        return 0 if progress.get('review_summary',{}).get('status') == 'completed' else 1
    finally:
        try:
            if started and base:
                tasks = backend(base, '/api/tasks')['tasks']
                if any(task['task_id'] == args.task and task['status'] in {'RUNNING','PAUSED'} for task in tasks):
                    request = urllib.request.Request(base+f'/api/tasks/{args.task}/stop', data=b'', method='POST')
                    with urllib.request.urlopen(request, timeout=60) as response:
                        event('CHECKPOINT_STOP', json.load(response))
        finally:
            try:
                if window is not None:
                    window.web_view.stop()
                    window.web_view.setUrl(QUrl('about:blank'))
                    wait(200)
                    window.hide()
                    window.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                    app.processEvents()
            finally:
                service_manager.shutdown_all()
                event('OWNED_BACKEND_STOPPED', service_manager.server_thread is None)


if __name__ == '__main__':
    raise SystemExit(main())
