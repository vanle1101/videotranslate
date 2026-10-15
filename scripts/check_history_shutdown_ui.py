"""Real production Qt Exit during History reads; offscreen, no fake work."""
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.check_production_ui import (
    QApplication, StudioMainWindow, service_manager, until, evaluate, wait,
    event, QUrl, QCoreApplication, QEvent,
)
from core import history_work


def main():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    assert app.platformName() == 'offscreen'
    window = None
    try:
        port = service_manager.start_backend(timeout=30)
        window = StudioMainWindow(port)
        page = window.web_view.page()
        page.setAudioMuted(True)
        window.show()
        until(page, "document.readyState==='complete' && !!window.openTaskInStudio")
        # Actual HTTP endpoints and retained manifests. No delayed/mocked reader.
        evaluate(page, "(() => {for(let i=0;i<2;i++) fetch('/api/tasks').catch(()=>{});return true;})()")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with history_work._active_lock:
                active = len(history_work._active)
            if active:
                break
            wait(10)
        else:
            raise AssertionError('No real history request was active at Exit')
        event('HISTORY_READS_AT_EXIT', active)
        from core.streaming.pipeline import active_streaming_sessions
        assert not any(s.is_running or s.is_editing for s in active_streaming_sessions.values())
        started = time.monotonic()
        # Same action as the tray's explicit Exit; no translation is running.
        window.request_exit()
        elapsed = time.monotonic() - started
        stopped = service_manager.server_thread is None
        with history_work._active_lock:
            remaining = len(history_work._active)
        event('EXIT_RESULT', {'seconds': round(elapsed, 3),
            'backend_stopped': stopped, 'remaining_history_workers': remaining})
        assert stopped and remaining == 0
    finally:
        if window:
            window.web_view.stop()
            window.web_view.setUrl(QUrl('about:blank'))
            wait(100)
            window.hide()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
        service_manager.shutdown_all()
        assert service_manager.server_thread is None


if __name__ == '__main__':
    main()
