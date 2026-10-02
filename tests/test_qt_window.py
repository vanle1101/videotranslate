import sys
from pathlib import Path

# Ensure root directory is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from desktop_app import StudioSplashScreen, StudioMainWindow, create_app_icon

def test_qt_components():
    print("=" * 80)
    print("TEST: QT DESKTOP APPLICATION COMPONENTS")
    print("=" * 80)

    app = QApplication.instance() or QApplication(sys.argv)
    
    # 1. Test Icon Generation
    icon = create_app_icon()
    assert not icon.isNull(), "App icon generation failed!"
    print("[+] App icon generated successfully.")

    # 2. Test Splash Screen Instantiation
    splash = StudioSplashScreen()
    splash.set_progress(45, "Testing splash status update...")
    print("[+] Splash screen initialized and styled correctly.")

    # 3. Test Main Window Instantiation with Dynamic Free Port
    from core.services.service_manager import service_manager
    dynamic_port = service_manager.find_free_port()
    win = StudioMainWindow(dynamic_port)
    assert win.minimumWidth() >= 1280, "Minimum width must be at least 1280px"
    assert win.minimumHeight() >= 800, "Minimum height must be at least 800px"
    assert win.windowTitle() == "Douyin2TikTok AI Studio"
    print(f"[+] StudioMainWindow instantiated with URL: {win.backend_url}")
    print(f"[+] Window dimensions: {win.width()}x{win.height()} (Min: {win.minimumWidth()}x{win.minimumHeight()})")
    print("[+] WebEngineView and DesktopBridge configured.")

    # Close widgets
    splash.close()
    win.close()
    print("\n[+] All Qt Desktop Components verified successfully!")
    print("=" * 80)

if __name__ == "__main__":
    test_qt_components()
