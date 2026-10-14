"""Opt-in real mixed-language Studio acceptance; no fake providers or media.

Pass a short, intentionally prepared QA video containing English and Chinese.
The real production controls create a project and spend real provider requests.
Existing projects are never edited. Outputs and checkpoints remain reviewable.
"""
import argparse
import hashlib
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


# Observe the actual native media method; never replace playback, its promise,
# media bytes, or error handling. Detached dubbing Audio objects aren't in DOM.
MEDIA_OBSERVER = r"""(() => {
    if (window.__qaMedia) return true;
    const media = window.__qaMedia = {entries:[], errors:[]};
    const original = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function(...args) {
        if (!media.entries.some(entry => entry.element === this)) {
            const entry = {element:this, playing:0};
            media.entries.push(entry);
            this.addEventListener('playing', () => entry.playing++);
            this.addEventListener('error', () => media.errors.push({source:this.currentSrc,
                code:this.error?.code, message:this.error?.message}));
        }
        return original.apply(this,args);
    };
    return true;
})()"""


def check_translated_preview(page, snapshot):
    """Exercise the Studio player and its actual translated WAV before Full."""
    progress = snapshot['progress']
    assert progress.get('translation_mode') == 'preview' and progress.get('preview_output_ready') is True, progress
    assert not progress.get('preview_untranslated_ids') and not progress.get('preview_missing_audio_ids'), progress
    boundary = float(progress['processed_seconds'])
    rows = [row for row in snapshot['segments'] if row.get('final_vi', '').strip()
        and row.get('audio_url') and row.get('status') in {'READY', 'PLAYED'}
        and not row.get('confirmed_silence') and row['end'] <= boundary + .001]
    assert rows, 'No translated speech available to audition before Full'
    row = max(rows, key=lambda value: float(value.get('tts_duration') or value['end'] - value['start']))
    assert evaluate(page, MEDIA_OBSERVER)
    if evaluate(page, "!document.getElementById('video-player').paused"):
        click_ready(page, 'player-play-toggle')
    # Source text is intentionally hidden in the Translation tab. The time
    # button is the visible seek control in both transcript modes.
    selector = json.dumps(f"#seg-row-{row['id']} .transcript-time")
    until(page, "(() => {const e=document.querySelector(" + selector +
        ");return !!e && !e.disabled && e.getClientRects().length>0;})()")
    assert evaluate(page, "(() => {const e=document.querySelector(" + selector +
        ");e.scrollIntoView({block:'nearest'});e.click();return true;})()")
    until(page, "(() => {const v=document.getElementById('video-player');return v.readyState>=2 && !v.seeking;})()", timeout=120)
    # Match the precise revisioned WAV, not BGM, voice samples or source audio.
    expected = json.dumps(row['audio_url'])
    state_js = """(() => {
        const expected = new URL(""" + expected + """,location.href).href;
        const entries = window.__qaMedia.entries.filter(e => e.element.tagName==='AUDIO' && e.element.currentSrc===expected);
        const entry = entries[entries.length-1], a=entry?.element, v=document.getElementById('video-player');
        return {audio:a?{source:a.currentSrc,time:a.currentTime,ready:a.readyState,
            paused:a.paused,muted:a.muted,volume:a.volume,error:a.error?.code||null,playing:entry.playing}:null,
            video:{time:v.currentTime,ready:v.readyState,paused:v.paused,seeking:v.seeking,error:v.error?.code||null},
            alerts:window.__qaAlerts||[]};
    })()"""
    click_ready(page, 'player-play-toggle')
    deadline, first, last = time.monotonic() + 120, None, None
    while time.monotonic() < deadline:
        last = evaluate(page, state_js)
        a, v = last['audio'], last['video']
        assert not last['alerts'] and not v['error'] and not (a and a['error']), last
        if a and a['playing'] and a['ready'] >= 2 and not a['paused'] and not a['muted'] and a['volume'] > 0 and not v['paused'] and not v['seeking']:
            if first is None:
                first = (a['time'], v['time'], time.monotonic())
            if (a['time'] - first[0] >= .10 and v['time'] - first[1] >= .10
                    and time.monotonic() - first[2] >= .10):
                break
        wait(40)
    else:
        raise AssertionError({'preview_audio_never_advanced_with_video': last, 'segment_id':row['id']})
    if evaluate(page, "!document.getElementById('video-player').paused"):
        click_ready(page, 'player-play-toggle')
    event('TRANSLATED_PREVIEW_PLAYBACK', {'task_id':snapshot['task_id'], 'segment_id':row['id'],
        'state':last, 'scope':'Actual translated WAV and video clocks advanced; page output muted; naturalness not judged'})


def check_result_seeks(page, rows):
    """Verify native result-media seeks forward and backward at speech times."""
    spoken = sorted((row for row in rows if row.get('final_vi', '').strip()
                     and not row.get('confirmed_silence')), key=lambda row: row['start'])
    assert spoken, 'No spoken output to test'
    click_ready(page, 'task-result-link')
    until(page, "!document.getElementById('result-preview-modal').classList.contains('hidden')")
    until(page, "document.getElementById('result-preview-video').readyState>=2", timeout=120)
    duration = evaluate(page, "document.getElementById('result-preview-video').duration")
    assert isinstance(duration, (int, float)) and duration > .25, duration
    # Start, late, then middle explicitly exercises both seek directions.
    tested = []
    for row in (spoken[0], spoken[-1], spoken[len(spoken)//2]):
        target = min(float(row.get('dub_start') if row.get('dub_start') is not None else row['start']), duration - .25)
        evaluate(page, "(() => {const v=document.getElementById('result-preview-video');v.pause();"
            "window.__qaSeekError=null;v.currentTime=" + json.dumps(target) + ";return true;})()")
        until(page, "(() => {const v=document.getElementById('result-preview-video');return !v.seeking && v.readyState>=2 && Math.abs(v.currentTime-"
            + json.dumps(target) + ")<.15;})()", timeout=120)
        evaluate(page, "(() => {document.getElementById('result-preview-video').play().catch(e=>window.__qaSeekError=String(e));return true;})()")
        until(page, "(() => {const v=document.getElementById('result-preview-video');return !v.paused && !v.seeking && !v.error && v.currentTime>="
            + json.dumps(target + .10) + ";})()", timeout=60)
        state = evaluate(page, "(() => {const v=document.getElementById('result-preview-video');return {time:v.currentTime,source:v.currentSrc,error:v.error?.code||window.__qaSeekError};})()")
        assert not state['error'], state
        tested.append({'target':target, **state})
    click_ready(page, 'btn-close-result-preview')
    event('RESULT_FORWARD_BACKWARD_SEEKS', tested)


def click_ready(page, element_id, timeout=30):
    """Backend snapshots can arrive before the WebSocket updates its button."""
    until(page, "(() => {const e=document.getElementById(" + json.dumps(element_id) +
          ");return !!e && !e.disabled && !e.hidden && e.getClientRects().length>0 && "
          "getComputedStyle(e).visibility!=='hidden';})()", timeout=timeout)
    assert click(page, element_id), f'Control became unavailable: {element_id}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--expect-source', choices=['mixed', 'zh', 'ko'], default='mixed',
        help='Assert the observed source scripts; default retains the mixed-language check.')
    parser.add_argument('--resume', help='Resume only a QA project for this exact source')
    parser.add_argument('--review', action='store_true', help='Run real review of the restored QA project through its UI')
    parser.add_argument('--timeout', type=int, default=1200)
    parser.add_argument('--max-source-seconds', type=int, default=90,
        help='Explicit maximum source duration for this real-provider acceptance run (up to 7200).')
    args = parser.parse_args()
    if args.review and not args.resume:
        parser.error('--review requires --resume')
    source = args.source.resolve(strict=True)
    assert any(source.is_relative_to((ROOT / name).resolve())
        for name in ('cleanup/qa', 'workspace/inputs')), 'Use an explicit retained or QA input'
    assert args.timeout > 0, 'Timeout must be positive'
    assert 0 < args.max_source_seconds <= 7200, 'Invalid source-duration budget'
    if args.resume:
        from core.streaming.session_store import _read
        assert re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', args.resume), 'Invalid QA task ID'
        manifest = _read(args.resume)
        assert manifest and Path(manifest['session']['video_path']).resolve() == source, 'Resume source does not match QA input'
        retained_rows = manifest['segments']
        if isinstance(retained_rows, dict):
            retained_rows = list(retained_rows.values())
        retained_audio = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for row in retained_rows if row.get('audio_path')
            and (path := Path(row['audio_path'])).is_file()}
    probe = json.loads(run_media(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(source)], capture_output=True))
    assert 0 < float(probe['format']['duration']) <= args.max_source_seconds, 'Source exceeds the explicit acceptance duration budget'
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
        event('UI_READY', {})
        # Native JS alert dialogs block an unattended Qt event loop indefinitely.
        # Record the actual error instead; never accept a confirmation for the user.
        evaluate(page,"(() => {window.__qaAlerts=[];window.alert=m=>{window.__qaAlerts.push(String(m));console.error('QA_ALERT',m);};return true;})()")
        wait(5000)
        if args.resume:
            evaluate(page,"(() => {window.__attached=null;window.studioAttachTask("+json.dumps(args.resume)+").then(v=>window.__attached=v);return true;})()")
            until(page,'window.__attached===true')
            task_id = args.resume
            snapshot = backend(base,f'/api/streaming/{task_id}')
            restored = {row['id']: row for row in snapshot['segments']}
            assert snapshot.get('task_id') == task_id, 'Restore created a different task'
            assert {row['id'] for row in retained_rows}.issubset(restored), 'Restore discarded saved source rows'
            assert float(snapshot['progress'].get('source_prepared_seconds', 0)) >= float(
                manifest['session'].get('_source_prepared_seconds', 0)) - .001, 'Restore lost ASR coverage'
            for row in retained_rows:
                saved = restored[row['id']]
                assert (saved['start'], saved['end'], saved.get('asr_text')) == (
                    row['start'], row['end'], row.get('asr_text')), 'Restore changed measured source ownership'
                if (row.get('verification') or {}).get('status') == 'manual':
                    for key in ('text_zh', 'final_vi', 'revision', 'voice_id'):
                        assert saved.get(key) == row.get(key), ('Restore changed a manual edit', row['id'], key)
            for filename, fingerprint in retained_audio.items():
                assert hashlib.sha256(Path(filename).read_bytes()).hexdigest() == fingerprint, 'Restore rewrote saved audio'
            event('RESTORED_CHECKPOINT_VALIDATED', {'task_id':task_id, 'retained_rows':len(retained_rows),
                'retained_audio_files':len(retained_audio),
                'source_prepared_seconds':snapshot['progress'].get('source_prepared_seconds')})
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
            assert not evaluate(page, 'window.__qaAlerts'), evaluate(page, 'window.__qaAlerts')
            event('SOURCE_SELECTED', {'source':str(source)})
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
            alerts = evaluate(page, 'window.__qaAlerts || []')
            assert not alerts, {'ui_alerts':alerts, 'task_id':task_id}
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
                check_translated_preview(page, snapshot)
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
        # Validate persisted source coverage independently of the UI's Completed
        # label: a full-length source picture can hide omitted translated chunks.
        from core.streaming.session_store import _read
        saved = _read(task_id)
        fields = saved['session']
        source_duration = float(probe['format']['duration'])
        assert len({row['id'] for row in rows}) == len(rows), 'Duplicate segment ownership'
        if fields.get('_chunked_source_started'):
            assert fields.get('_visual_prepass_complete') is True, 'Source processing incomplete'
            assert float(fields.get('_source_prepared_seconds', 0)) >= source_duration - .1, 'ASR coverage missing at EOF'
            cursor = float(fields.get('_legacy_visual_prefix', 0))
            chunks = sorted(fields.get('_chunk_jobs', []), key=lambda value:value['start'])
            assert len({chunk['id'] for chunk in chunks}) == len(chunks), 'Duplicate chunk ownership'
            for chunk in chunks:
                assert chunk['state'] == 'COMPLETED', chunk
                assert abs(float(chunk['start']) - cursor) <= .01, {'gap_or_overlap_after':cursor, 'chunk':chunk}
                assert float(chunk['end']) > cursor, chunk
                cursor = float(chunk['end'])
            assert abs(cursor - source_duration) <= .1, {'coverage_end':cursor, 'source_duration':source_duration}
            event('SOURCE_COVERAGE_VALIDATED', {'task_id':task_id, 'chunks':len(chunks), 'seconds':cursor})
        if args.review:
            assert progress.get('review_summary', {}).get('status') == 'completed', progress
        assert rows and all(row.get('final_vi','').strip() or row.get('confirmed_silence') for row in rows)
        assert all(row.get('status') in {'READY','PLAYED'} or row.get('confirmed_silence') for row in rows), rows
        assert snapshot.get('final_output_blocked') is False, {'output_gate':snapshot.get('final_output_blocked'),'rows':rows}
        if args.expect_source == 'ko':
            assert any(any('\uac00' <= c <= '\ud7a3' for c in row.get('text_zh','')) for row in rows), rows
            assert not any(any('\u4e00' <= c <= '\u9fff' for c in row.get('text_zh','')) for row in rows), rows
        else:
            assert any(any('\u4e00' <= c <= '\u9fff' for c in row.get('text_zh','')) for row in rows), rows
        if args.expect_source == 'mixed':
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
        run_media(['ffmpeg','-v','error','-xerror','-err_detect','explode','-i',str(output),'-f','null','-'],
            capture_output=True,timeout=max(120, min(7200, int(float(probe['format']['duration']) * 2))))
        check_result_seeks(page, rows)
        reload_page(page)
        until(page,"localStorage.getItem('studio.last-task-id')==="+json.dumps(task_id))
        until(page,"document.querySelectorAll('.transcript-row').length==="+str(len(rows)))
        review_required=sum(bool(row.get('needs_review')) for row in snapshot['segments'])
        event('RUNTIME_PASS_REVIEW_REQUIRED' if review_required else 'RUNTIME_PASS',{
            'task_id':task_id,'mp4':str(output),'bytes':output.stat().st_size,
            'duration':result['format']['duration'],'review_required':review_required,
            'translated_preview_playback':'verified' if promoted else 'not tested: restored full task',
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
