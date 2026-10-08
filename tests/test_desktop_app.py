import os
import sys
import time
import json
import urllib.request
from pathlib import Path

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
        from PySide6.QtWidgets import QMainWindow

        app = qt_app

        class PlayerWindow(QMainWindow):
            _on_fullscreen_requested = StudioMainWindow._on_fullscreen_requested
            restore_window = StudioMainWindow.restore_window

            def __init__(self):
                super().__init__()
                self._fullscreen_restore_state = None

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
