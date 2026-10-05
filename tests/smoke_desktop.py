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
        result = json.loads(javascript(window.web_view.page(), '''JSON.stringify({
            asr: document.body.dataset.asrEngine,
            tts: document.body.dataset.ttsEngine,
            voice: document.getElementById('voice-select').value,
            title: document.title,
            bridge: !!window.desktopBridge
        })'''))
        assert result['asr'] == 'faster-whisper', result
        assert result['tts'] == 'edge-tts', result
        assert result['voice'].startswith('vi-VN-'), result
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
                        voice: options.body.get('voice')
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
                                      'JSON.stringify({requests: window.__smokeRequests, alerts: window.__smokeAlerts})'))
        assert len(upload['requests']) == 1, upload
        assert upload['requests'][0]['tts'] == 'edge-tts', upload
        assert upload['alerts'] == ['Lỗi: SMOKE_EXPECTED'], upload
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
                          'playback': playback, 'bgm': bgm, 'layout_1024': layout}, ensure_ascii=False))
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
