"""Headless QtWebEngine smoke test, including the browser file-upload workflow."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineCore import QWebEnginePage
from desktop_app import StudioMainWindow, StudioSplashScreen
from core.services.service_manager import service_manager
from core.engines.separator.realtime_suppressor import RealtimeVocalSuppressor
from config import settings


def wait(milliseconds):
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def javascript(page, source):
    result = []
    loop = QEventLoop()
    def completed(value):
        result.append(value)
        loop.quit()
    page.runJavaScript(source, completed)
    QTimer.singleShot(10000, loop.quit)
    loop.exec()
    assert result, 'JavaScript timed out'
    return result[0]


def video_state(page):
    return json.loads(javascript(page, '''JSON.stringify((() => {
        const video = document.getElementById('video-player');
        return {readyState: video.readyState, duration: video.duration,
            width: video.videoWidth, height: video.videoHeight,
            time: video.currentTime, paused: video.paused, source: video.currentSrc,
            error: video.error ? {code: video.error.code, message: video.error.message} : null,
            playError: window.__smokePlayError || null};
    })())'''))


def check_media_playback(page, folder):
    """Play actual H264/AAC source via automatic WebM fallback when needed."""
    video = Path(folder) / 'video thử H264 & AAC.mp4'
    subprocess.run([
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
        '-f', 'lavfi', '-i', 'color=c=blue:s=180x320:r=24:d=8',
        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=8',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
        '-movflags', '+faststart', '-shortest', str(video),
    ], check=True, capture_output=True, timeout=30)
    javascript(page, f'window.loadDroppedLocalVideo({json.dumps(str(video))});')
    for _ in range(200):
        state = video_state(page)
        if state['readyState'] >= 2:
            break
        wait(100)
    assert state['error'] is None, f'Qt failed decoding H264/AAC: {state}'
    assert state['readyState'] >= 2, f'Qt never loaded the MP4: {state}'
    assert (state['width'], state['height']) == (180, 320), state
    assert 7.9 <= state['duration'] <= 8.2, state
    assert '/api/preview/' in state['source'] or '/api/local-file?path=' in state['source'], state
    javascript(page, '''
        window.__smokePlayError = null;
        document.getElementById('video-player').muted = true;
        document.getElementById('video-player').play().catch(error => {
            window.__smokePlayError = String(error);
        });
    ''')
    for _ in range(40):
        state = video_state(page)
        if state['playError'] or state['time'] >= 0.3:
            break
        wait(100)
    assert not state['playError'] and not state['paused'] and state['time'] >= 0.3, state
    javascript(page, "document.getElementById('video-player').pause()")
    paused = video_state(page)
    wait(400)
    after_pause = video_state(page)
    assert after_pause['paused'], after_pause
    assert abs(after_pause['time'] - paused['time']) < 0.05, (paused, after_pause)
    javascript(page, "document.getElementById('video-player').currentTime = 4")
    wait(250)
    seek = video_state(page)
    assert abs(seek['time'] - 4) < 0.1 and seek['paused'], seek
    javascript(page, "document.getElementById('video-player').play()")
    wait(600)
    resumed = video_state(page)
    assert resumed['time'] > 4.2 and not resumed['paused'], resumed
    javascript(page, "document.getElementById('video-player').pause()")
    return {'source_codec': 'H264/AAC', 'fallback': '/api/preview/' in state['source'], 'duration': state['duration'],
            'played_seconds': round(state['time'], 3), 'pause_stable': True, 'seek_resume': True}


def check_minimum_layout(window):
    window.resize(1024, 640)
    wait(300)
    results = {}
    for tab in ('studio', 'tasks', 'models', 'diagnostics', 'settings'):
        javascript(window.web_view.page(), f'document.getElementById("tab-{tab}").click()')
        wait(250)
        geometry = json.loads(javascript(window.web_view.page(), '''JSON.stringify({
            width: innerWidth,
            scrollWidth: document.documentElement.scrollWidth,
            headerRight: document.querySelector('header').getBoundingClientRect().right,
            viewRight: [...document.querySelectorAll('.app-view')]
                .find(element => !element.classList.contains('hidden')).getBoundingClientRect().right
        })'''))
        assert geometry['scrollWidth'] <= geometry['width'] + 1, (tab, geometry)
        assert geometry['headerRight'] <= geometry['width'] + 1, (tab, geometry)
        assert geometry['viewRight'] <= geometry['width'] + 1, (tab, geometry)
        results[tab] = geometry['scrollWidth']
    javascript(window.web_view.page(), "document.getElementById('tab-studio').click()")
    return results


def check_bgm_playback(page, folder, task_id):
    source = Path(folder) / 'video thử H264 & AAC.mp4'
    cache = settings.BASE_DIR / 'workspace' / 'cache' / task_id
    cache.mkdir(parents=True)
    try:
        output = cache / 'bgm_suppressed.ogg'
        RealtimeVocalSuppressor().process_file(source, output)
        javascript(page, f'''
            window.__bgmSmoke = new Audio('/api/streaming/bgm/{task_id}');
            window.__bgmSmoke.muted = true;
            window.__bgmSmokeError = null;
            window.__bgmSmoke.play().catch(error => window.__bgmSmokeError = String(error));
        ''')
        for _ in range(60):
            state = json.loads(javascript(page, '''JSON.stringify({
                time: window.__bgmSmoke.currentTime, paused: window.__bgmSmoke.paused,
                readyState: window.__bgmSmoke.readyState,
                error: window.__bgmSmoke.error?.message || window.__bgmSmokeError
            })'''))
            if state['error'] or state['time'] >= 0.3:
                break
            wait(100)
        assert not state['error'] and not state['paused'] and state['time'] >= 0.3, state
        javascript(page, "window.__bgmSmoke.pause(); window.__bgmSmoke.removeAttribute('src'); window.__bgmSmoke.load();")
        return {'codec': 'Ogg Opus', 'played_seconds': round(state['time'], 3)}
    finally:
        import shutil
        shutil.rmtree(cache)


def check_progress_and_logs(page):
    """Use the real DOM and Qt renderer with deterministic download events."""
    share_text = r'3.53 复制打开抖音，看看【斩漫的作品】《千金垂爱》被千金拉着结婚了 # ai动漫 # a... [**https://v.douyin.com/\_lAiSDH0bK8/**](https://v.douyin.com/_lAiSDH0bK8/) Rxf:/ 01/17 J@V.Lw :1pm'
    javascript(page, 'window.__shareTextFixture = ' + json.dumps(share_text) + ';')
    javascript(page, '''
        window.__progressFetch = window.fetch;
        window.__progressSocket = window.WebSocket;
        window.__logFixture = '2026-10-05 INFO First line\\n2026-10-05 ERROR Download timed out\\n    second detail line\\n';
        window.__progressTask = {task_id: 'qt-progress-fixture', task_type: 'Dịch video', phase: 'download',
            stage: 'Đang tải video', progress_pct: null, status: 'RUNNING', can_pause: false, can_stop: true};
        window.fetch = async (url, options) => {
            if (url === '/api/streaming/start-url') {
                window.__submittedShareUrl = JSON.parse(options.body).url;
                return new Response(JSON.stringify({task_id: 'qt-progress-fixture', video_url: null}));
            }
            if (url === '/api/tasks') return new Response(JSON.stringify({tasks: [window.__progressTask]}));
            if (url === '/api/tasks/qt-progress-fixture/stop') return new Response(JSON.stringify({status: 'ok'}));
            if (url.startsWith('/api/diagnostics/logs?')) return new Response(JSON.stringify({logs: window.__logFixture}));
            return window.__progressFetch(url, options);
        };
        window.WebSocket = class {
            static OPEN = 1;
            constructor() { this.readyState = 1; window.__downloadSocket = this; }
            send() {}
            close() { this.readyState = 3; }
        };
    ''')
    # Trigger the native Qt Paste action so the actual ClipboardEvent, not an
    # input-event approximation, exercises immediate normalization.
    from PySide6.QtCore import QMimeData
    clipboard = QApplication.clipboard()
    saved_clipboard = QMimeData()
    current_mime = clipboard.mimeData()
    if current_mime is not None:
        for mime_type in current_mime.formats():
            saved_clipboard.setData(mime_type, current_mime.data(mime_type))
    try:
        clipboard.setText(share_text)
        javascript(page, '''
            const urlInput = document.getElementById('video-url');
            urlInput.value = '';
            urlInput.focus();
        ''')
        page.triggerAction(QWebEnginePage.WebAction.Paste)
        wait(100)
        pasted = json.loads(javascript(page, '''JSON.stringify({
            value: document.getElementById('video-url').value,
            recognized: document.getElementById('video-url-status').textContent,
            invalid: document.getElementById('video-url').getAttribute('aria-invalid'),
            started: !!window.__submittedShareUrl
        })'''))
        assert pasted['value'] == 'https://v.douyin.com/_lAiSDH0bK8/', pasted
        assert pasted['value'] in pasted['recognized'], pasted
        assert pasted['invalid'] == 'false' and not pasted['started'], pasted
    finally:
        if saved_clipboard.formats():
            clipboard.setMimeData(saved_clipboard)
        else:
            clipboard.clear()
    javascript(page, "document.getElementById('btn-start').click()")
    wait(200)
    javascript(page, '''window.__downloadSocket.onmessage({data: JSON.stringify({type: 'progress',
        ...window.__progressTask, progress_pct: 42.4})});''')
    measured = json.loads(javascript(page, '''JSON.stringify({
        percent: document.getElementById('task-progress-value').textContent,
        aria: document.getElementById('task-progress-track').getAttribute('aria-valuenow'),
        recognized: document.getElementById('video-url-status').textContent,
        sourceType: document.getElementById('video-url').tagName,
        extractedUrl: window.__submittedShareUrl,
        pauseHidden: document.getElementById('btn-pause-worker').classList.contains('hidden'),
        exportDisabled: document.getElementById('btn-export-hq').disabled
    })'''))
    assert measured['percent'] == '42%' and measured['aria'] == '42.4', measured
    assert 'Đã nhận link Douyin' in measured['recognized'], measured
    assert measured['sourceType'] == 'TEXTAREA', measured
    assert measured['extractedUrl'] == 'https://v.douyin.com/_lAiSDH0bK8/', measured
    assert measured['pauseHidden'] and measured['exportDisabled'], measured
    javascript(page, '''
        window.__downloadSocket.onmessage({data: JSON.stringify({type: 'progress', ...window.__progressTask})});
        document.getElementById('tab-tasks').click();
    ''')
    wait(150)
    tasks = javascript(page, "document.getElementById('tasks-table-body').textContent")
    assert 'Đang tải video' in tasks and 'Chưa có số liệu %' in tasks and 'null%' not in tasks, tasks
    assert javascript(page, "document.getElementById('task-progress-track').hasAttribute('aria-valuenow')") is False
    javascript(page, "document.getElementById('tab-diagnostics').click()")
    wait(150)
    logs = json.loads(javascript(page, '''JSON.stringify((() => {
        const log = document.getElementById('log-console-output');
        const spans = [...log.querySelectorAll('span')];
        return {whiteSpace: getComputedStyle(log).whiteSpace, rawMatches: log.textContent === window.__logFixture,
            separateLines: spans[1].getBoundingClientRect().top > spans[0].getBoundingClientRect().top,
            errors: log.querySelectorAll('.log-line-error').length,
            copyPresent: !document.getElementById('btn-copy-log').disabled};
    })())'''))
    assert logs['whiteSpace'] == 'pre-wrap' and logs['rawMatches'] and logs['separateLines'], logs
    assert logs['errors'] == 1 and logs['copyPresent'], logs
    from PySide6.QtCore import QMimeData
    clipboard = QApplication.clipboard()
    previous_clipboard = QMimeData()
    previous_mime = clipboard.mimeData()
    if previous_mime is not None:
        for mime_type in previous_mime.formats():
            previous_clipboard.setData(mime_type, previous_mime.data(mime_type))
    try:
        javascript(page, "document.getElementById('btn-copy-log').click()")
        wait(250)
        assert clipboard.text() == javascript(page, "window.__logFixture")
        assert 'Đã sao chép' in javascript(page, "document.getElementById('log-copy-status').textContent")
    finally:
        if previous_clipboard.formats():
            clipboard.setMimeData(previous_clipboard)
        else:
            clipboard.clear()
    javascript(page, '''
        document.getElementById('tab-studio').click();
        window.studioStop();
    ''')
    wait(150)
    javascript(page, '''
        window.fetch = window.__progressFetch;
        window.WebSocket = window.__progressSocket;
        document.getElementById('video-url').value = '';
        document.getElementById('video-url').dispatchEvent(new Event('input', {bubbles: true}));
    ''')
    return {'download_percent': measured['percent'], 'whole_share_text': True, 'native_paste_canonical_url': True,
            'unknown_progress': True, 'task_visible': True,
            'logs_preserve_lines': logs['separateLines'], 'logs_safe_copy': logs['rawMatches'],
            'native_clipboard': True}


def check_voice_catalog_and_preview(page, folder):
    """Use the real catalog, HTTP preview route and Qt audio decoding.

    Synthesis alone is replaced by a deterministic tiny WAV; no paid service,
    network voice provider or model initialization is required by this smoke.
    """
    from unittest.mock import patch
    import math
    import struct
    import wave

    javascript(page, '''
        window.__voiceSmokeCatalog = null;
        fetch('/api/voices').then(response => response.json()).then(data => window.__voiceSmokeCatalog = data);
    ''')
    for _ in range(100):
        catalog = json.loads(javascript(page, '''JSON.stringify((() => {
            const select = document.getElementById('voice-select');
            return {api: window.__voiceSmokeCatalog, selected: select.value,
                inputType: select.type,
                rows: [...document.querySelectorAll('#voice-list .voice-row')].map(row => ({
                    id: row.dataset.voiceId, text: row.textContent,
                    disabled: row.querySelector('.voice-choose-button').disabled,
                    previewDisabled: row.querySelector('.voice-preview-button').disabled,
                    previewLabel: row.querySelector('.voice-preview-button').getAttribute('aria-label')})),
                groups: [...document.querySelectorAll('#voice-list .voice-group')].map(group => group.dataset.source)};
        })())'''))
        if catalog['api'] and len(catalog['rows']) == len(catalog['api']['voices']):
            break
        wait(100)
    voices = catalog['api']['voices']
    assert len(voices) >= 27, catalog
    assert catalog['inputType'] == 'hidden', catalog
    assert [row['id'] for row in catalog['rows']] == [voice['id'] for voice in voices], catalog
    assert set(catalog['groups']) == {voice['source'] for voice in voices}, catalog
    assert all(voice['name'] in row['text'] and row['disabled'] == (not voice['available'])
               and row['previewDisabled'] == (not voice['available']) and voice['name'] in row['previewLabel']
               for row, voice in zip(catalog['rows'], voices)), catalog
    selected = next(voice for voice in voices if voice['id'] == catalog['selected'])
    assert selected['available'], catalog
    local_voice = next(voice for voice in voices if voice['id'] == 'vieneu:Trúc Ly' and voice['available'])
    javascript(page, '''
        window.__voiceSmokeSelected = document.getElementById('voice-select').value;
        window.__voiceSmokePreference = localStorage.getItem('studio.voice-id');
        window.__voiceSmokeFetch = window.fetch;
        window.__voiceSmokeRequests = [];
        window.__voiceSmokePreviewId = null;
        window.fetch = async (url, options = {}) => {
            if (url === '/api/streaming/start-local-file') {
                window.__voiceSmokeRequests.push(JSON.parse(options.body));
                return new Response(JSON.stringify({detail: 'VOICE_SMOKE_EXPECTED'}), {status: 400});
            }
            const response = await window.__voiceSmokeFetch(url, options);
            if (url === '/api/voices/preview' && options.method === 'POST' && response.ok) {
                window.__voiceSmokePreviewId = (await response.clone().json()).preview_id;
            }
            return response;
        };
        window.__voiceSmokeRow = id => [...document.querySelectorAll('#voice-list .voice-row')]
            .find(row => row.dataset.voiceId === id);
        window.__voiceSmokeRow('vieneu:Trúc Ly').querySelector('.voice-choose-button').click();
    ''')
    synth_calls = []
    try:
        source = javascript(page, "document.getElementById('voice-source').textContent")
        assert local_voice['source'] in source and 'Chạy trên máy' in source, source
        # Backend startup is intercepted; the real handler must dispatch the
        # selected catalog ID without the configured Edge engine overriding it.
        source_video = Path(folder) / 'voice-fixture.mp4'
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                        '-f', 'lavfi', '-i', 'color=c=black:s=32x32:r=1:d=1',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source_video)],
                       check=True, capture_output=True, timeout=20)
        javascript(page, f'window.loadDroppedLocalVideo({json.dumps(str(source_video))});')
        javascript(page, "document.getElementById('btn-start').click()")
        wait(200)
        requests = json.loads(javascript(page, 'JSON.stringify(window.__voiceSmokeRequests)'))
        assert len(requests) == 1 and requests[0]['voice_id'] == local_voice['id'], requests
        assert 'voice' not in requests[0] and 'tts_engine' not in requests[0], requests

        javascript(page, '''
            window.__voiceSmokeRow('edge:vi-VN-HoaiMyNeural').querySelector('.voice-choose-button').click();
            const search = document.getElementById('voice-search');
            search.value = 'truc ly';
            search.dispatchEvent(new Event('input', {bubbles: true}));
        ''')
        visible_rows = json.loads(javascript(page, '''JSON.stringify(
            [...document.querySelectorAll('#voice-list .voice-row')]
                .filter(row => row.getClientRects().length && getComputedStyle(row).display !== 'none')
                .map(row => row.dataset.voiceId))'''))
        assert visible_rows == ['vieneu:Trúc Ly'], visible_rows

        def synthesize_fixture(text, output, voice=None, **kwargs):
            synth_calls.append(voice)
            rate = 16000
            frames = b''.join(struct.pack('<h', int(3000 * math.sin(2 * math.pi * 440 * i / rate)))
                              for i in range(rate * 2))
            with wave.open(str(output), 'wb') as sample:
                sample.setnchannels(1)
                sample.setsampwidth(2)
                sample.setframerate(rate)
                sample.writeframes(frames)
            return output

        with patch('core.voice_preview.VieNeuEngine.synthesize', side_effect=synthesize_fixture):
            javascript(page, '''
                document.getElementById('voice-preview-audio').muted = true;
                window.__voiceSmokeRow('vieneu:Trúc Ly').querySelector('.voice-preview-button').click();
            ''')
            for _ in range(100):
                preview = json.loads(javascript(page, '''JSON.stringify((() => {
                    const audio = document.getElementById('voice-preview-audio');
                    return {id: window.__voiceSmokePreviewId, time: audio.currentTime, paused: audio.paused,
                        readyState: audio.readyState, source: audio.currentSrc, visible: !audio.classList.contains('hidden'),
                        error: audio.error?.message || null, status: document.getElementById('voice-preview-status').textContent};
                })())'''))
                if preview['error'] or preview['time'] >= 0.2:
                    break
                wait(100)
        assert synth_calls == ['Trúc Ly'], synth_calls
        assert preview['id'] and preview['visible'] and not preview['error'], preview
        assert not preview['paused'] and preview['time'] >= 0.2, preview
        assert f"/api/voices/preview/{preview['id']}/audio" in preview['source'], preview
        assert javascript(page, "document.getElementById('voice-select').value") == 'edge:vi-VN-HoaiMyNeural'
        javascript(page, '''
            window.__voiceSmokeRow('vieneu:Trúc Ly').querySelector('.voice-choose-button').click();
        ''')
        wait(100)
        javascript(page, '''
            window.__voiceSmokeDeletedStatus = null;
            fetch('/api/voices/preview/' + window.__voiceSmokePreviewId + '/audio')
                .then(response => window.__voiceSmokeDeletedStatus = response.status)
                .catch(error => window.__voiceSmokeDeletedStatus = String(error));
        ''')
        for _ in range(30):
            deleted_status = json.loads(javascript(page, 'JSON.stringify({value: window.__voiceSmokeDeletedStatus})'))['value']
            if deleted_status is not None:
                break
            wait(100)
        assert deleted_status == 404, deleted_status
        assert javascript(page, "document.getElementById('voice-preview-audio').paused") is True
        return {'catalog_count': len(voices), 'source_groups': len(catalog['groups']),
                'local_voice_id': requests[0]['voice_id'], 'preview_http_audio': True,
                'preview_played_seconds': round(preview['time'], 3), 'preview_deleted_on_change': True,
                'row_preview_keeps_selection': True, 'accent_insensitive_search': True}
    finally:
        javascript(page, '''
            const search = document.getElementById('voice-search');
            search.value = '';
            search.dispatchEvent(new Event('input', {bubbles: true}));
            window.__voiceSmokeRow(window.__voiceSmokeSelected).querySelector('.voice-choose-button').click();
            if (window.__voiceSmokePreference === null) localStorage.removeItem('studio.voice-id');
            else localStorage.setItem('studio.voice-id', window.__voiceSmokePreference);
            window.fetch = window.__voiceSmokeFetch;
        ''')


def check_transcript_editor(window, screenshot_path=None):
    """Exercise actual Qt DOM caret placement, drafts, revision save and wide layouts."""
    page = window.web_view.page()
    javascript(page, """
        window.__transcriptFetch = window.fetch;
        window.__transcriptSocket = window.WebSocket;
        window.__transcriptErrors = [];
        window.__transcriptErrorHandler = event => window.__transcriptErrors.push({message:event.message, file:event.filename, line:event.lineno, stack:event.error?.stack});
        window.addEventListener('error', window.__transcriptErrorHandler);
        window.__transcriptSegment = {id: 0, start: 0, end: 10, duration: 10,
            status: 'READY', text_zh: '字幕测试', final_vi: 'Bấm vào chữ cần sửa trong câu này.', audio_url: null, revision: 0};
        window.fetch = async (url, options) => {
            if (url === '/api/streaming/start-url') return new Response(JSON.stringify({task_id: 'qt-transcript'}));
            if (url === '/api/tasks') return new Response(JSON.stringify({tasks: []}));
            if (url === '/api/tasks/qt-transcript/stop') return new Response(JSON.stringify({status: 'ok'}));
            if (url === '/api/streaming/qt-transcript/segments/0') {
                window.__transcriptSegment = {...window.__transcriptSegment, ...JSON.parse(options.body), revision: 1};
                return new Response(JSON.stringify({segment: window.__transcriptSegment}));
            }
            return window.__transcriptFetch(url, options);
        };
        window.WebSocket = class {
            static OPEN = 1;
            constructor() {this.readyState = 1; window.__transcriptWs = this;}
            send() {}
            close() {this.readyState = 3;}
        };
        document.getElementById('video-url').value = 'https://www.douyin.com/video/7688769264395767049';
        document.getElementById('btn-start').click();
    """)
    wait(200)
    javascript(page, """
        window.__transcriptWs.onmessage({data: JSON.stringify({type: 'init', duration: 10,
            segments_count: 1, segments: [window.__transcriptSegment]})});
        document.getElementById('buffering-alert').classList.add('hidden');
    """)
    geometry = {}
    try:
        for width, height in ((1920, 1080), (1024, 640)):
            window.resize(width, height)
            wait(300)
            measure = json.loads(javascript(page, """JSON.stringify((() => {
                const r = selector => {
                    const b = document.querySelector(selector).getBoundingClientRect();
                    return {x:b.x,y:b.y,w:b.width,h:b.height,right:b.right};
                };
                return {width:innerWidth,scrollWidth:document.documentElement.scrollWidth,
                    preview:r('.studio-preview'),transcript:r('.transcript-panel'),player:r('#player-container'),
                    playerStyle:document.getElementById('player-container').getAttribute('style'),
                    aspect:getComputedStyle(document.getElementById('player-container')).aspectRatio,
                    sourceRatio:document.getElementById('video-player').videoWidth / document.getElementById('video-player').videoHeight || 9/16,
                    stage:r('.studio-video-stage')};
            })())"""))
            assert measure['scrollWidth'] <= measure['width'] + 1, measure
            assert abs(measure['preview']['y'] - measure['transcript']['y']) <= 1, measure
            assert measure['transcript']['x'] >= measure['preview']['right'], measure
            assert abs(measure['player']['w'] / measure['player']['h'] - measure['sourceRatio']) < 0.02, measure
            geometry[str(width)] = measure
        window.resize(1920,1080)
        wait(200)
        javascript(page, """
            const button = document.getElementById('seg-vi-0');
            const range = document.createRange();
            range.setStart(button.firstChild, 8); range.setEnd(button.firstChild, 9);
            const bounds = range.getBoundingClientRect();
            button.dispatchEvent(new MouseEvent('click', {bubbles: true,
                clientX: bounds.left + 1, clientY: bounds.top + bounds.height / 2}));
        """)
        state = json.loads(javascript(page, """JSON.stringify({
            hidden:document.querySelector('.transcript-editor').hidden,
            display:getComputedStyle(document.querySelector('.transcript-editor')).display,
            textHidden:document.getElementById('seg-vi-0').hidden,
            caret:document.getElementById('seg-input-0').selectionStart,
            errors:window.__transcriptErrors})"""))
        assert not state['hidden'] and state['display'] != 'none', state
        assert state['textHidden'] and 7 <= state['caret'] <= 10, state
        assert not state['errors'], state
        if screenshot_path:
            wait(200)
            window.grab().save(str(screenshot_path))
        javascript(page, """(() => {
            const input = document.getElementById('seg-input-0');
            input.value = 'Đã sửa trực tiếp cạnh video.';
            input.dispatchEvent(new Event('input', {bubbles: true}));
            document.querySelector('.transcript-editor-actions button').click();
        })()""")
        wait(200)
        saved = json.loads(javascript(page, """JSON.stringify({
            hidden:document.querySelector('.transcript-editor').hidden,
            text:document.getElementById('seg-vi-0').textContent,
            subtitle:document.getElementById('subtitle-text').textContent,
            exportDisabled:document.getElementById('btn-export-hq').disabled,
            message:document.querySelector('.transcript-edit-message').textContent,
            saveDisabled:document.querySelector('.transcript-editor-actions button').disabled,
            input:document.getElementById('seg-input-0').value,
            errors:window.__transcriptErrors})"""))
        assert saved['hidden'] and saved['text'] == saved['subtitle'] == 'Đã sửa trực tiếp cạnh video.', saved
        assert not saved['exportDisabled'] and not saved['errors'], saved
        return {'caret_edit':True, 'server_save':True, 'subtitle_sync':True, 'layouts':geometry}
    finally:
        javascript(page, 'window.studioStop();')
        wait(200)
        javascript(page, """
            window.fetch = window.__transcriptFetch;
            window.WebSocket = window.__transcriptSocket;
            window.removeEventListener('error', window.__transcriptErrorHandler);
            document.getElementById('video-url').value = '';
            document.getElementById('video-url').dispatchEvent(new Event('input', {bubbles: true}));
        """)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    window = None
    media_folder = tempfile.TemporaryDirectory(prefix='qt-media-smoke-', dir=settings.TEMP_DIR)
    try:
        port = service_manager.start_backend(timeout=30)
        splash = StudioSplashScreen()
        splash.set_progress(100, 'Ready')
        splash.close()
        window = StudioMainWindow(port)
        loaded = []
        window.web_view.loadFinished.connect(loaded.append)
        window.show()
        for _ in range(100):
            if loaded:
                break
            wait(200)
        assert loaded and loaded[-1], 'Desktop page did not load'
        wait(500)
        voice_smoke = check_voice_catalog_and_preview(window.web_view.page(), media_folder.name)
        result = json.loads(javascript(window.web_view.page(), '''JSON.stringify({
            asr: document.body.dataset.asrEngine,
            tts: document.body.dataset.ttsEngine,
            voice: document.getElementById('voice-select').value,
            title: document.title,
            bridge: !!window.desktopBridge
        })'''))
        assert result['asr'] == 'faster-whisper', result
        assert result['tts'] == 'edge-tts', result
        assert result['voice'].startswith(('edge:', 'vieneu:', 'piper:')), result
        assert result['bridge'], 'Native file picker bridge unavailable'

        # Check actual input/select behavior against public backend settings.
        # A DOM mock can accidentally give HTMLInputElement a select.options.
        javascript(window.web_view.page(), '''
            window.__settingsSmoke = null;
            fetch('/api/settings').then(response => response.json()).then(cfg => {
                const fields = {
                    'settings-gemini-model': cfg.gemini_model,
                    'settings-muse-browser-mode': cfg.muse_browser_mode,
                    'settings-openrouter-model': cfg.openrouter_model,
                    'settings-buffer-target': cfg.buffer_target,
                    'buffer-select': cfg.buffer_target,
                    'settings-ducking-level': cfg.ducking_level
                };
                window.__settingsSmoke = Object.entries(fields).map(([id, expected]) => ({
                    id, matches: document.getElementById(id)?.value === String(expected)
                }));
            });
        ''')
        for _ in range(50):
            form = json.loads(javascript(window.web_view.page(), 'JSON.stringify(window.__settingsSmoke)'))
            if form is not None:
                break
            wait(100)
        assert form and all(field['matches'] for field in form), form

        # Exercise the real upload handler without uploading media or launching AI.
        javascript(window.web_view.page(), '''
            window.__smokeRequests = [];
            window.__smokeAlerts = [];
            window.alert = text => window.__smokeAlerts.push(String(text));
            const originalFetch = window.fetch;
            window.fetch = async (url, options) => {
                if (url === '/api/streaming/start-upload') {
                    window.__smokeRequests.push({
                        tts: options.body.get('tts_engine'),
                        asr: options.body.get('asr_engine'),
                        voice: options.body.get('voice'),
                        voice_id: options.body.get('voice_id')
                    });
                    return new Response(JSON.stringify({detail: 'SMOKE_EXPECTED'}), {status: 400});
                }
                return originalFetch(url, options);
            };
            const files = new DataTransfer();
            files.items.add(new File(['test'], 'test.mp4', {type: 'video/mp4'}));
            const input = document.getElementById('video-file');
            input.files = files.files;
            input.dispatchEvent(new Event('change', {bubbles: true}));
            document.getElementById('btn-start').click();
        ''')
        wait(300)
        upload = json.loads(javascript(window.web_view.page(),
                                      '''JSON.stringify({requests: window.__smokeRequests, alerts: window.__smokeAlerts,
                                          error: document.getElementById('task-progress-stage').textContent,
                                          status: document.getElementById('task-progress').dataset.status})'''))
        assert len(upload['requests']) == 1, upload
        assert upload['requests'][0]['tts'] is None and upload['requests'][0]['voice'] is None, upload
        assert upload['requests'][0]['voice_id'] == result['voice'], upload
        assert not upload['alerts'] and upload['error'] == 'SMOKE_EXPECTED' and upload['status'] == 'FAILED', upload
        progress_logs = check_progress_and_logs(window.web_view.page())
        transcript_smoke = check_transcript_editor(window)
        layout = check_minimum_layout(window)
        playback = check_media_playback(window.web_view.page(), media_folder.name)
        bgm = check_bgm_playback(window.web_view.page(), media_folder.name, Path(media_folder.name).name)
        javascript(window.web_view.page(), "document.getElementById('tab-models').click()")
        wait(300)
        rows = javascript(window.web_view.page(), "document.querySelectorAll('#models-full-table-body tr').length")
        assert rows >= 1
        javascript(window.web_view.page(), "document.getElementById('tab-settings').click()")
        wait(400)
        config = json.loads(javascript(window.web_view.page(), '''JSON.stringify({
            provider: document.getElementById('settings-llm-provider').value,
            model: document.getElementById('settings-opencode-model').value,
            freeModel: document.getElementById('settings-openrouter-model').value,
            museMode: document.getElementById('settings-muse-browser-mode').value,
            modelCount: document.getElementById('settings-opencode-model').options.length,
            status: document.getElementById('opencode-auth-status').textContent,
            geminiKey: document.getElementById('settings-gemini-key').value,
            deepseekKey: document.getElementById('settings-deepseek-key').value
        })'''))
        assert config['provider'] == settings.LLM_PROVIDER
        assert config['model'] == settings.OPENCODE_MODEL
        assert config['freeModel'] == settings.OPENROUTER_MODEL
        assert config['museMode'] == settings.MUSE_BROWSER_MODE
        assert config['modelCount'] >= 1
        assert config['status'] and 'Đang kiểm tra' not in config['status']
        assert not config['geminiKey'] and not config['deepseekKey']
        javascript(window.web_view.page(), '''
            const settingsFetch = window.fetch;
            window.fetch = async (url, options) => ['/api/test-opencode', '/api/test-openrouter', '/api/test-gemini', '/api/test-muse'].includes(url)
                ? new Response(JSON.stringify({ok: true, model: 'big-pickle', latency_ms: 10}))
                : settingsFetch(url, options);
            document.getElementById('btn-test-opencode').click();
            document.getElementById('btn-test-openrouter').click();
            document.getElementById('btn-test-gemini').click();
            document.getElementById('btn-test-muse').click();
        ''')
        wait(200)
        assert 'Kết nối OK' in javascript(window.web_view.page(),
            "document.getElementById('opencode-test-result').textContent")
        assert 'Kết nối OK' in javascript(window.web_view.page(),
            "document.getElementById('openrouter-test-result').textContent")
        assert 'Kết nối OK' in javascript(window.web_view.page(),
            "document.getElementById('gemini-test-result').textContent")
        assert 'Muse đã trả lời' in javascript(window.web_view.page(),
            "document.getElementById('muse-status').textContent")
        if len(sys.argv) > 1:
            window.grab().save(sys.argv[1])
        javascript(window.web_view.page(), "document.getElementById('tab-studio').click()")
        wait(200)
        print(json.dumps({'result': 'PASS', 'desktop': result, 'upload': upload['requests'][0],
                          'playback': playback, 'bgm': bgm, 'layout_1024': layout,
                          'progress_and_logs': progress_logs, 'voices': voice_smoke, 'transcript': transcript_smoke}, ensure_ascii=False))
    finally:
        if window:
            window.web_view.stop()
            window.close()
            from shiboken6 import delete
            delete(window)
            wait(200)
        service_manager.shutdown_all()
        media_folder.cleanup()


if __name__ == '__main__':
    main()
