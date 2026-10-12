"""Resume a named real project through offscreen UI attached to running Studio.

No server copy, media mock, visible window or desktop input. The selected
project's existing checkpoints are advanced by its normal Resume handler.
"""
import argparse
import json
import time
import urllib.request
from pathlib import Path

from check_production_ui import (QApplication, QCoreApplication, QEvent, QUrl,
    StudioMainWindow, backend, click, evaluate, event, open_history, until, wait,
    check_playback)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--task', required=True)
    parser.add_argument('--timeout', type=int, default=1800)
    args = parser.parse_args()
    base = f'http://127.0.0.1:{args.port}'
    app = QApplication.instance() or QApplication([])
    assert app.platformName() == 'offscreen'
    app.setQuitOnLastWindowClosed(False)
    window = StudioMainWindow(args.port)
    page = window.web_view.page()
    page.setAudioMuted(True)
    started = False
    try:
        window.show()
        until(page, "document.readyState==='complete' && !!window.openTaskInStudio")
        open_history(page, args.task)
        snapshot = backend(base, f'/api/streaming/{args.task}')
        event('BEFORE', {'task':args.task,'duration':snapshot['duration'],
            'status':snapshot['progress']['status'],'rows':len(snapshot['segments'])})
        assert snapshot['progress']['can_retry'], snapshot['progress']['status']
        assert click(page, 'btn-retry-worker')
        started = True
        until(page, "document.getElementById('task-progress').dataset.status==='RUNNING'")
        deadline = time.monotonic() + args.timeout
        previous = None
        prepared_at = None
        while time.monotonic() < deadline:
            snapshot = backend(base, f'/api/streaming/{args.task}')
            progress = snapshot['progress']
            state = evaluate(page, "({status:document.getElementById('task-progress').dataset.status,eta:document.getElementById('task-progress-eta').textContent,percent:document.getElementById('task-progress-value').textContent})")
            current = {'status':progress['status'],'stage':progress.get('stage'),
                'states':progress.get('segment_states'),'eta_seconds':progress.get('eta_seconds'),
                'eta_scope':progress.get('eta_scope'),'ui':state}
            if current != previous:
                event('REAL_PROGRESS', current)
                previous = current
            prepared_at = (prepared_at or time.monotonic()) if progress['status']=='PREPARED' else None
            if (progress['status'] in {'FAILED','STOPPED','CANCELLED'} or
                prepared_at and (progress.get('final_output_blocked') or time.monotonic()-prepared_at>60)):
                event('NOT_COMPLETED', {'progress':{k:progress.get(k) for k in (
                    'status','stage','segment_states','missing_speech_ids','final_output_blocked',
                    'review_summary','eta_seconds','eta_scope')},'failures':[
                    {k:r.get(k) for k in ('id','start','end','status','failed_stage','error')}
                    for r in snapshot['segments'] if r['status']=='FAILED']})
                return 1
            if progress['status'] == 'COMPLETED' and snapshot.get('output_filename'):
                from core.streaming.export import HQExporter
                from config import settings
                output = settings.OUTPUT_DIR / Path(snapshot['output_filename']).name
                HQExporter.validate_output(output, snapshot['duration'])
                check_playback(page)
                event('REAL_OUTPUT_VALIDATED', {'path':str(output.resolve()),'bytes':output.stat().st_size})
                return 0
            # Full snapshots enrich every subtitle and may be large for a long
            # project. The UI already receives WebSocket progress; avoid a
            # second high-frequency poll adding load during the measurement.
            wait(15000)
        event('DEADLINE', {'task':args.task})
        return 2
    finally:
        if started:
            tasks = backend(base, '/api/tasks')['tasks']
            if any(t['task_id']==args.task and t['status'] in {'RUNNING','PAUSED'} for t in tasks):
                request = urllib.request.Request(base+f'/api/tasks/{args.task}/stop', data=b'', method='POST')
                with urllib.request.urlopen(request, timeout=60) as response:
                    event('CHECKPOINT_STOP', json.load(response))
        window.web_view.stop()
        window.web_view.setUrl(QUrl('about:blank'))
        wait(200)
        window.hide()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        event('QA_WINDOW_CLOSED', True)


if __name__ == '__main__':
    raise SystemExit(main())
