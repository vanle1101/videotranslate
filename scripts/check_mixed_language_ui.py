"""Opt-in real mixed-language Studio acceptance; no fake providers or media.

Pass a short, intentionally prepared QA video containing English and Chinese.
The real production controls create a project and spend real provider requests.
Existing projects are never edited. Outputs and checkpoints remain reviewable.
"""
import argparse
import json
import re
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.check_production_ui import (
    QApplication, StudioMainWindow, wait, evaluate, until, click, backend,
    event, export_through_ui, check_playback, reload_page,
    QUrl, QCoreApplication, QEvent, service_manager, ROOT,
)
from core.media_process import run_media


def click_ready(page, element_id, timeout=30):
    """Backend snapshots can arrive before the WebSocket updates its button."""
    until(page, "(() => {const e=document.getElementById(" + json.dumps(element_id) +
          ");return !!e && !e.disabled && !e.hidden && e.getClientRects().length>0 && "
          "getComputedStyle(e).visibility!=='hidden';})()", timeout=timeout)
    assert click(page, element_id), f'Control became unavailable: {element_id}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--resume', help='Resume only a QA project for this exact source')
    parser.add_argument('--review', action='store_true', help='Run real review of the restored QA project through its UI')
    parser.add_argument('--timeout', type=int, default=1200)
    args = parser.parse_args()
    if args.review and not args.resume:
        parser.error('--review requires --resume')
    source = args.source.resolve(strict=True)
    assert source.is_relative_to((ROOT / 'cleanup/qa').resolve()), 'Use an explicit QA input'
    assert args.timeout > 0, 'Timeout must be positive'
    if args.resume:
        from core.streaming.session_store import _read
        assert re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', args.resume), 'Invalid QA task ID'
        manifest = _read(args.resume)
        assert manifest and Path(manifest['session']['video_path']).resolve() == source, 'Resume source does not match QA input'
    probe = json.loads(run_media(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(source)], capture_output=True))
    assert 0 < float(probe['format']['duration']) <= 90, 'Keep this acceptance clip short'
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window = None
    task_id = None
    owns_backend = False
    try:
        assert not service_manager.is_running and not service_manager.server_thread, 'Acceptance must own its isolated backend'
        owns_backend = True
        port = service_manager.start_backend(timeout=30)
        base = f'http://127.0.0.1:{port}'
        event('BACKEND', {'port':port})
        window = StudioMainWindow(port)
        window.resize(1366,860)
        page = window.web_view.page()
        page.setAudioMuted(True)
        window.show()
        until(page,"document.readyState==='complete' && !!window.loadDroppedLocalVideo")
        wait(5000)
        if args.resume:
            evaluate(page,"(() => {window.__attached=null;window.studioAttachTask("+json.dumps(args.resume)+").then(v=>window.__attached=v);return true;})()")
            until(page,'window.__attached===true')
            task_id = args.resume
            snapshot = backend(base,f'/api/streaming/{task_id}')
            if snapshot['progress'].get('can_retry'):
                click_ready(page,'btn-retry-worker')
                until(page,"['RUNNING','PREVIEW_READY','PREPARED','COMPLETED'].includes(document.getElementById('task-progress').dataset.status)")
                event('RESUME_REQUESTED', {'task_id':task_id, 'action':'retry'})
            elif snapshot['progress'].get('can_resume'):
                click_ready(page,'btn-resume-worker')
                until(page,"['RUNNING','PREVIEW_READY','PREPARED','COMPLETED'].includes(document.getElementById('task-progress').dataset.status)")
                event('RESUME_REQUESTED', {'task_id':task_id, 'action':'resume'})
        else:
            evaluate(page,"(() => {window.loadDroppedLocalVideo("+json.dumps(str(source))+");document.getElementById('source-language').value='auto';document.getElementById('visual-translation').checked=true;return true;})()")
            until(page,"!document.getElementById('btn-start').disabled")
            assert click(page,'btn-start')
            until(page,"document.getElementById('task-progress').dataset.status==='RUNNING'")
            until(page,"!!localStorage.getItem('studio.last-task-id')")
            # Wait for the newly created matching source instead of taking a
            # remembered unrelated project from the asynchronous startup.
            deadline=time.monotonic()+45
            while time.monotonic()<deadline:
                candidate=evaluate(page,"localStorage.getItem('studio.last-task-id')")
                snapshot=backend(base,f'/api/streaming/{candidate}')
                if str(source).replace('\\','/') in snapshot.get('video_path','').replace('\\','/'):
                    task_id=candidate;break
                from core.streaming.session_store import _read
                manifest=_read(candidate)
                if manifest and Path(manifest.get('session',{}).get('video_path','')).resolve()==source:
                    task_id=candidate;break
                wait(200)
            assert task_id, 'No matching project created'
        event('TASK',{'task_id':task_id,'source':str(source)})
        if args.review:
            click_ready(page, 'btn-review-worker')
            until(page,"document.getElementById('task-progress').dataset.status==='RUNNING'")
            event('REVIEW_REQUESTED', {'task_id':task_id})
        deadline=time.monotonic()+args.timeout
        last=None
        promoted=False
        while time.monotonic()<deadline:
            snapshot=backend(base,f'/api/streaming/{task_id}')
            progress=snapshot['progress']
            state=(progress.get('status'),progress.get('phase'),progress.get('stage'))
            if state!=last:
                event('PROGRESS',{'status':state[0],'phase':state[1],'stage':state[2],
                    'states':progress.get('segment_states')})
                last=state
            if state[0] in {'FAILED','STOPPED','CANCELLED'}:
                raise AssertionError({'progress':progress,'rows':snapshot.get('segments')})
            if progress.get('can_translate_full') and not promoted:
                click_ready(page,'btn-translate-full')
                promoted=True
                until(page,"document.getElementById('btn-translate-full').disabled || document.getElementById('btn-translate-full').classList.contains('hidden')")
                event('FULL_TRANSLATION_REQUESTED', {'task_id':task_id})
            elif state[0] in {'PREPARED','COMPLETED'} and progress.get('translation_mode')=='full':
                break
            wait(500)
        else:
            raise AssertionError({'timeout':args.timeout,'progress':progress})
        rows=snapshot['segments']
        if args.review:
            assert progress.get('review_summary', {}).get('status') == 'completed', progress
        assert rows and all(row.get('final_vi','').strip() or row.get('confirmed_silence') for row in rows)
        assert all(row.get('status') in {'READY','PLAYED'} or row.get('confirmed_silence') for row in rows), rows
        assert snapshot.get('final_output_blocked') is False, {'output_gate':snapshot.get('final_output_blocked'),'rows':rows}
        assert any(any('\u4e00' <= c <= '\u9fff' for c in row.get('text_zh','')) for row in rows), rows
        assert any(any(c.isascii() and c.isalpha() for c in row.get('text_zh','')) for row in rows), rows
        event('TRANSLATION_ROWS',[{k:row.get(k) for k in ('id','text_zh','final_vi','status','needs_review','audio_url')} for row in rows])
        if snapshot.get('output_video_url') and not snapshot.get('output_outdated'):
            until(page,"!document.getElementById('task-result-link').classList.contains('hidden')")
            check_playback(page)
        else:
            export_through_ui(page,base,task_id)
        snapshot=backend(base,f'/api/streaming/{task_id}')
        assert snapshot.get('task_id') == task_id
        assert snapshot.get('final_output_blocked') is False, snapshot.get('missing_speech_ids')
        output=(ROOT/'workspace/outputs'/snapshot['output_filename']).resolve()
        assert output.parent == (ROOT/'workspace/outputs').resolve()
        assert output.is_file() and output.stat().st_size>0 and not snapshot.get('output_outdated')
        result=json.loads(run_media(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(output)],capture_output=True))
        assert {s['codec_type'] for s in result['streams']} >= {'audio','video'}
        assert abs(float(result['format']['duration'])-float(probe['format']['duration']))<.5
        run_media(['ffmpeg','-v','error','-i',str(output),'-f','null','-'],capture_output=True,timeout=120)
        reload_page(page)
        until(page,"localStorage.getItem('studio.last-task-id')==="+json.dumps(task_id))
        until(page,"document.querySelectorAll('.transcript-row').length==="+str(len(rows)))
        review_required=sum(bool(row.get('needs_review')) for row in snapshot['segments'])
        event('RUNTIME_PASS_REVIEW_REQUIRED' if review_required else 'RUNTIME_PASS',{
            'task_id':task_id,'mp4':str(output),'bytes':output.stat().st_size,
            'duration':result['format']['duration'],'review_required':review_required,
            'scope':'real provider execution, MP4 decode/playback and page reload; semantic approval not implied'})
    finally:
        try:
            if window:
                window.web_view.stop()
                window.web_view.setUrl(QUrl('about:blank'))
                wait(200)
                window.hide();window.deleteLater()
                QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
                app.processEvents()
        finally:
            # Qt disposal failures cannot skip cancellation of our own server.
            if owns_backend:
                service_manager.shutdown_all()
                remaining=service_manager.server_thread
                assert remaining is None or not remaining.is_alive(), 'Owned backend did not stop'
                event('QA_STOPPED',{'task_id':task_id})


if __name__=='__main__':
    main()
