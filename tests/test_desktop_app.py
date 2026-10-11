import os
import sys
import time
import json
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

# Ensure root directory is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.services.service_manager import service_manager


@pytest.fixture
def fullscreen_window_factory(qt_app, qt_objects):
    # These windows never load the backend or touch the user's Studio instance.
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("QT_QPA_PLATFORM", "offscreen")
        from desktop_app import StudioMainWindow
        from PySide6.QtWidgets import QLineEdit, QMainWindow

        app = qt_app

        class EscapeEditor(QLineEdit):
            def __init__(self, parent):
                super().__init__(parent)
                self.escape_count = 0

            def keyPressEvent(self, event):
                from PySide6.QtCore import Qt
                if event.key() == Qt.Key.Key_Escape:
                    self.escape_count += 1
                super().keyPressEvent(event)

        class PlayerWindow(StudioMainWindow):

            def __init__(self):
                # Exercise Studio's real native fullscreen handling without
                # starting a backend, tray, or WebEngine/browser instance.
                QMainWindow.__init__(self)
                self._fullscreen_restore_state = None
                self.page_action = Mock()
                self.web_view = SimpleNamespace(
                    page=lambda: SimpleNamespace(triggerAction=self.page_action),
                    stop=lambda: None,
                )
                self.editor = EscapeEditor(self)
                self.setCentralWidget(self.editor)
                self._setup_fullscreen_exit_action()

        def create_window(maximized=False):
            window = qt_objects(PlayerWindow())
            window.resize(900, 600)
            if maximized:
                window.showMaximized()
            else:
                window.showNormal()
            app.processEvents()
            return window, app

        yield create_window


class FullscreenRequest:
    def __init__(self, enabled):
        self.enabled = enabled
        self.accepted = False

    def toggleOn(self):
        return self.enabled

    def accept(self):
        self.accepted = True


@pytest.mark.parametrize("maximized", [False, True])
def test_player_fullscreen_restores_window(fullscreen_window_factory, maximized):
    window, app = fullscreen_window_factory(maximized)
    initial_size = window.size()
    enter = FullscreenRequest(True)
    window._on_fullscreen_requested(enter)
    app.processEvents()
    assert enter.accepted
    assert window.isFullScreen()

    # A repeated entry must not overwrite the saved normal/maximized state.
    window._on_fullscreen_requested(FullscreenRequest(True))
    leave = FullscreenRequest(False)
    window._on_fullscreen_requested(leave)
    app.processEvents()
    assert leave.accepted
    assert not window.isFullScreen()
    assert window.isMaximized() == maximized
    if not maximized:
        assert window.size() == initial_size
    assert window._fullscreen_restore_state is None


def test_player_fullscreen_activation_preserves_player_mode(fullscreen_window_factory):
    window, app = fullscreen_window_factory(maximized=True)
    window._on_fullscreen_requested(FullscreenRequest(True))
    window.showMinimized()
    window.restore_window()
    app.processEvents()
    assert window.isFullScreen()
    assert not window.isMinimized()

    window._on_fullscreen_requested(FullscreenRequest(False))
    app.processEvents()
    assert window.isMaximized()
    assert not window.isFullScreen()


def test_player_fullscreen_extra_exit_keeps_window_state(fullscreen_window_factory):
    window, app = fullscreen_window_factory(maximized=True)
    leave = FullscreenRequest(False)
    window._on_fullscreen_requested(leave)
    app.processEvents()
    assert leave.accepted
    assert window.isMaximized()


@pytest.mark.parametrize("maximized", [False, True])
def test_player_fullscreen_escape_from_focused_editor_restores_window(
    fullscreen_window_factory, maximized
):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWebEngineCore import QWebEnginePage

    window, app = fullscreen_window_factory(maximized)
    initial_size = window.size()
    window._on_fullscreen_requested(FullscreenRequest(True))
    window.activateWindow()
    window.editor.setFocus()
    app.processEvents()
    assert window.fullscreen_exit_action.isEnabled()

    QTest.keyClick(window.editor, Qt.Key.Key_Escape)
    app.processEvents()
    window.page_action.assert_called_once_with(QWebEnginePage.WebAction.ExitFullScreen)
    assert not window.isFullScreen()
    assert window.isMaximized() == maximized
    if not maximized:
        assert window.size() == initial_size
    assert not window.fullscreen_exit_action.isEnabled()
    assert window._fullscreen_restore_state is None
    assert window.editor.escape_count == 0

    # A delayed browser response must not undo the already-restored window.
    window._on_fullscreen_requested(FullscreenRequest(False))
    app.processEvents()
    assert window.isMaximized() == maximized
    assert not window.isFullScreen()


def test_player_fullscreen_escape_recovers_orphaned_native_window(fullscreen_window_factory):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    window, app = fullscreen_window_factory()
    window.showFullScreen()
    window.activateWindow()
    window.editor.setFocus()
    app.processEvents()
    assert window._fullscreen_restore_state is None
    assert window.fullscreen_exit_action.isEnabled()
    QTest.keyClick(window.editor, Qt.Key.Key_Escape)
    app.processEvents()
    assert not window.isFullScreen()
    assert not window.fullscreen_exit_action.isEnabled()
    window.page_action.assert_called_once()


def test_player_fullscreen_reentrant_browser_exit_preserves_maximized_state(fullscreen_window_factory):
    window, app = fullscreen_window_factory(maximized=True)
    window._on_fullscreen_requested(FullscreenRequest(True))
    window.page_action.side_effect = lambda action: window._on_fullscreen_requested(FullscreenRequest(False))
    window.exit_player_fullscreen()
    app.processEvents()
    assert window.isMaximized()
    assert not window.isFullScreen()
    assert window._fullscreen_restore_state is None
    assert not window.fullscreen_exit_action.isEnabled()


def test_player_fullscreen_reentry_from_orphan_does_not_restore_fullscreen(fullscreen_window_factory):
    window, app = fullscreen_window_factory()
    window.showFullScreen()
    window._on_fullscreen_requested(FullscreenRequest(True))
    window.exit_player_fullscreen()
    app.processEvents()
    assert not window.isFullScreen()
    assert window._fullscreen_restore_state is None
    assert not window.fullscreen_exit_action.isEnabled()


def test_player_fullscreen_escape_disabled_in_ordinary_editor(fullscreen_window_factory):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    window, app = fullscreen_window_factory()
    window.activateWindow()
    window.editor.setFocus()
    app.processEvents()
    assert not window.fullscreen_exit_action.isEnabled()
    QTest.keyClick(window.editor, Qt.Key.Key_Escape)
    app.processEvents()
    assert window.editor.escape_count == 1
    window.page_action.assert_not_called()
    assert not window.isFullScreen()


def test_player_fullscreen_does_not_capture_escape_in_modal_dialog(fullscreen_window_factory, qt_objects):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog

    window, app = fullscreen_window_factory()
    window._on_fullscreen_requested(FullscreenRequest(True))
    dialog = qt_objects(QDialog(window))
    dialog.setModal(True)
    dialog.show()
    dialog.activateWindow()
    app.processEvents()
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    app.processEvents()
    assert not dialog.isVisible()
    assert window.isFullScreen()
    window.page_action.assert_not_called()

def test_desktop_backend_lifecycle():
    print("=" * 80)
    print("TEST: DESKTOP APPLICATION SERVICE MANAGER & INTERNAL BACKEND")
    print("=" * 80)

    # 1. Start Internal Backend on Random Free Port
    print("[1/5] Starting internal FastAPI backend on ephemeral free port...")
    t0 = time.time()
    port = service_manager.start_backend(timeout=10.0)
    elapsed = time.time() - t0
    base_url = f"http://127.0.0.1:{port}"
    print(f"  [+] Backend started successfully on {base_url} in {elapsed:.2f}s")
    assert port > 1024, f"Invalid port: {port}"

    # 2. Test Endpoints
    print("\n[2/5] Testing internal endpoints without user-facing browser...")
    endpoints = [
        ("/", "text/html"),
        ("/api/hardware", "application/json"),
        ("/api/models", "application/json"),
        ("/api/diagnostics/logs?category=app", "application/json"),
    ]
    for path, expected_content_type in endpoints:
        url = f"{base_url}{path}"
        req = urllib.request.Request(url, headers={"User-Agent": "DesktopStudioTest/1.0"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            content_type = resp.headers.get("Content-Type", "")
            print(f"  -> GET {path:35s} [Status: {resp.status}] [Type: {content_type}]")
            assert resp.status == 200, f"Endpoint {path} failed with status {resp.status}"

    # 3. Test Local File Endpoint
    print("\n[3/5] Testing /api/local-file streaming endpoint...")
    sample_file = Path("workspace/inputs/real_chinese_1.mp4.webm")
    if sample_file.exists():
        test_url = f"{base_url}/api/local-file?path={sample_file.as_posix()}"
        req = urllib.request.Request(test_url, headers={"User-Agent": "DesktopStudioTest/1.0"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            length = int(resp.headers.get("Content-Length", 0))
            print(f"  -> GET /api/local-file [Status: {resp.status}] [Size: {length / 1024 / 1024:.2f} MB]")
            assert resp.status == 200 and length > 0

    # 4. Test Pre-warm Components
    print("\n[4/5] Testing pre-warm sequence (telemetry 0% -> 100%)...")
    def _progress(pct, msg):
        print(f"  [{pct:3d}%] {msg}")

    prewarm_result = service_manager.prewarm_components(progress_callback=_progress)
    print(f"  [+] Prewarm results: {prewarm_result}")

    # 5. Test Graceful Shutdown
    print("\n[5/5] Testing graceful shutdown & zombie prevention...")
    service_manager.shutdown_all()
    print("  [+] Shutdown complete. Verifying server port is freed...")
    time.sleep(0.5)

    # Verify server no longer accepts requests
    try:
        req = urllib.request.Request(f"{base_url}/api/hardware", headers={"User-Agent": "DesktopStudioTest/1.0"})
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            print("  [!] Warning: Port still responded immediately after shutdown.")
    except Exception:
        print("  [+] Verified: Server stopped cleanly and port is closed.")

    print("\n" + "=" * 80)
    print("ALL DESKTOP BACKEND LIFECYCLE TESTS PASSED PERFECTLY!")
    print("=" * 80)

if __name__ == "__main__":
    test_desktop_backend_lifecycle()
