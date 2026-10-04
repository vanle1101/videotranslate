"""Headless QtWebEngine smoke test, including the browser file-upload workflow."""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
from desktop_app import StudioMainWindow, StudioSplashScreen
from core.services.service_manager import service_manager


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


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    window = None
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
        javascript(window.web_view.page(), "document.getElementById('tab-models').click()")
        wait(300)
        rows = javascript(window.web_view.page(), "document.querySelectorAll('#models-full-table-body tr').length")
        assert rows >= 1
        javascript(window.web_view.page(), "document.getElementById('tab-studio').click()")
        wait(200)
        if len(sys.argv) > 1:
            window.grab().save(sys.argv[1])
        print(json.dumps({'result': 'PASS', 'desktop': result, 'upload': upload['requests'][0]}, ensure_ascii=False))
    finally:
        if window:
            window.web_view.stop()
            window.close()
            from shiboken6 import delete
            delete(window)
            wait(200)
        service_manager.shutdown_all()


if __name__ == '__main__':
    main()
