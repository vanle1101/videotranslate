"""Resume a named real project through offscreen UI attached to running Studio.

No server copy, media mock, visible window or desktop input. The selected
project's existing checkpoints are advanced by its normal Resume handler.
"""
import argparse
import json
import math
import re
import time
import urllib.request
from pathlib import Path

from check_production_ui import (QApplication, QCoreApplication, QEvent, QUrl,
    StudioMainWindow, backend, click, evaluate, event, open_history, until, wait,
    check_playback)


def require_completed_snapshot(snapshot, task_id):
    """Reject missing counters or cross-task output before validating media."""
    assert snapshot.get('task_id') == task_id, 'Snapshot belongs to another task'
    progress = snapshot.get('progress')
    assert isinstance(progress, dict) and progress.get('status') == 'COMPLETED', 'Task is not completed'
    assert progress.get('translation_mode') == 'full', 'A preview is not a full translated output'
    assert snapshot.get('final_output_blocked') is False, 'Final output gate is missing or blocked'
    assert snapshot.get('output_outdated') is False, 'Output freshness is missing or outdated'
    rows = snapshot.get('segments')
    assert isinstance(rows, list) and rows, 'Missing segment evidence'
    assert snapshot.get('segments_count') == len(rows), 'Incomplete segment snapshot'
    counts = progress.get('segment_states')
    expected = {'PENDING', 'RUNNING', 'COMPLETED', 'RETRY_PENDING', 'REVIEW_REQUIRED', 'FAILED'}
    assert isinstance(counts, dict) and expected <= counts.keys(), 'Missing segment failure counts'
    assert all(type(counts[name]) is int and counts[name] >= 0 for name in expected), 'Invalid segment counts'
    assert sum(counts[name] for name in expected) == len(rows), 'Counters do not cover all segments'
    assert all(counts[name] == 0 for name in ('PENDING','RUNNING','RETRY_PENDING','FAILED')), counts
    assert all(row.get('status') in {'READY','PLAYED'} for row in rows), 'Unfinished segment remains'
    assert all(row.get('confirmed_silence') is True or str(row.get('final_vi') or '').strip()
               for row in rows), 'A spoken segment has no translation'
    duration = snapshot.get('duration')
    assert type(duration) in (int,float) and math.isfinite(duration) and duration > 0, 'Invalid source duration'
    filename = f'douyin_translated_{task_id}_hq.mp4'
    assert snapshot.get('output_filename') == filename, 'Output does not belong to requested task'
    assert snapshot.get('output_video_url') == f'/api/outputs/{filename}', 'Output URL and file do not agree'
    return filename, counts['REVIEW_REQUIRED']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--task', required=True)
    parser.add_argument('--timeout', type=int, default=1800)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 7200:
        parser.error('--timeout must be between 1 and 7200 seconds')
    if not 1 <= args.port <= 65535 or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', args.task):
        parser.error('Invalid backend port or task ID')
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
            assert snapshot.get('task_id') == args.task, 'Snapshot task changed'
            progress = snapshot['progress']
            state = evaluate(page, "({status:document.getElementById('task-progress').dataset.status,eta:document.getElementById('task-progress-eta').textContent,percent:document.getElementById('task-progress-value').textContent})")
            current = {'status':progress['status'],'stage':progress.get('stage'),
                'states':progress.get('segment_states'),'eta_seconds':progress.get('eta_seconds'),
                'eta_scope':progress.get('eta_scope'),'eta_reason':progress.get('eta_reason'),
                'eta_components':progress.get('eta_components'), 'ui':state}
            if current != previous:
                event('REAL_PROGRESS', current)
                previous = current
            prepared_at = (prepared_at or time.monotonic()) if progress['status']=='PREPARED' else None
            if (progress['status'] in {'FAILED','STOPPED','CANCELLED'} or
                prepared_at and (snapshot.get('final_output_blocked') is not False or time.monotonic()-prepared_at>60)):
                event('NOT_COMPLETED', {'progress':{k:progress.get(k) for k in (
                    'status','stage','segment_states',
                    'review_summary','eta_seconds','eta_scope')},'failures':[
                    {k:r.get(k) for k in ('id','start','end','status','failed_stage','error')}
                    for r in snapshot['segments'] if r['status']=='FAILED'],
                    'final_output_blocked':snapshot.get('final_output_blocked'),
                    'missing_speech_ids':snapshot.get('missing_speech_ids')})
                return 1
            if progress['status'] == 'COMPLETED':
                filename, review_required = require_completed_snapshot(snapshot, args.task)
                from core.streaming.export import HQExporter
                from config import settings
                output_root = settings.OUTPUT_DIR.resolve(strict=True)
                candidate = output_root / filename
                assert not candidate.is_symlink() and not candidate.is_junction(), 'Output is a redirected path'
                output = candidate.resolve(strict=True)
                assert output.parent == output_root, 'Output escaped configured directory'
                signature = (output.stat().st_size, output.stat().st_mtime_ns)
                # Production validator ffprobes A/V duration and fully decodes
                # both streams using ffmpeg -xerror, not just an initial frame.
                HQExporter.validate_output(output, snapshot['duration'])
                until(page,"(() => {const e=document.getElementById('task-result-link');"
                      "return !e.classList.contains('hidden') && new URL(e.href).pathname==="+
                      json.dumps(snapshot['output_video_url'])+";})()")
                check_playback(page)
                current = backend(base, f'/api/streaming/{args.task}')
                _, review_required = require_completed_snapshot(current, args.task)
                assert signature == (output.stat().st_size, output.stat().st_mtime_ns), 'Output changed during validation'
                event('REAL_OUTPUT_VALIDATED', {'task_id':args.task,'path':str(output),'bytes':output.stat().st_size,
                    'review_required':review_required,'semantic_approval':False,
                    'validation':'full audio/video decode, production playback; editorial review remains separate'})
                return 0
            # Full snapshots enrich every subtitle and may be large for a long
            # project. The UI already receives WebSocket progress; avoid a
            # second high-frequency poll adding load during the measurement.
            wait(15000)
        event('DEADLINE', {'task':args.task})
        return 2
    finally:
        try:
            if started:
                tasks = backend(base, '/api/tasks')['tasks']
                for task in tasks:
                    if (task['task_id'] in {args.task, 'export_' + args.task}
                            and task['status'] in {'RUNNING','PAUSED'}):
                        request = urllib.request.Request(base+f"/api/tasks/{task['task_id']}/stop", data=b'', method='POST')
                        with urllib.request.urlopen(request, timeout=60) as response:
                            event('CHECKPOINT_STOP', json.load(response))
        finally:
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
