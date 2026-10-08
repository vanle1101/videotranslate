import os
import sys
import json
import logging
import shutil
from pathlib import Path

# Ensure application root is in sys.path
APP_ROOT = Path(__file__).resolve().parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

# Initialize safe stdout/stderr streams immediately (critical for pythonw.exe)
from config import settings, SafeStream

from PySide6.QtCore import (
    Qt, QThread, Signal, Slot, QObject, QTimer, QSize, QUrl
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QProgressBar, QMessageBox, QSystemTrayIcon, QMenu,
    QFileDialog, QGraphicsDropShadowEffect
)
from PySide6.QtGui import (
    QIcon, QPixmap, QPainter, QColor, QFont, QLinearGradient, QAction, QPen,
    QKeySequence
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from config import settings
from core.services.service_manager import service_manager

SINGLE_INSTANCE_SERVER = "Douyin2TikTok_AI_Studio_SingleInstance_Port"
WINDOWS_APP_ID = "Douyin2TikTok.AIStudio.Desktop"
logger = logging.getLogger("app")

def configure_windows_identity():
    """Give the taskbar a Studio identity instead of grouping under Python."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_ID)
        except (AttributeError, OSError):
            logger.warning("Could not set the Windows taskbar application identity.")

def create_app_icon() -> QIcon:
    """Generates a high-resolution dark studio icon for window and system tray."""
    brand_icon = QIcon(str(APP_ROOT / "static" / "studio-mark.svg"))
    if not brand_icon.isNull():
        return brand_icon
    size = 128
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Gradient background circle
    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0.0, QColor("#ec4899")) # Pink-500
    grad.setColorAt(0.5, QColor("#f43f5e")) # Rose-500
    grad.setColorAt(1.0, QColor("#8b5cf6")) # Violet-500
    painter.setBrush(grad)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(8, 8, size - 16, size - 16, 28, 28)

    # Inner lightning bolt
    painter.setBrush(QColor("#ffffff"))
    pen = QPen(QColor("#ffffff"))
    painter.setPen(pen)
    from PySide6.QtGui import QPolygonF
    from PySide6.QtCore import QPointF
    points = [
        QPointF(size * 0.56, size * 0.22),
        QPointF(size * 0.32, size * 0.52),
        QPointF(size * 0.50, size * 0.52),
        QPointF(size * 0.44, size * 0.78),
        QPointF(size * 0.70, size * 0.45),
        QPointF(size * 0.52, size * 0.45),
    ]
    painter.drawPolygon(QPolygonF(points))
    painter.end()
    return QIcon(pix)

# -------------------------------------------------------------
# JAVASCRIPT <-> PYTHON DESKTOP BRIDGE
# -------------------------------------------------------------

class DesktopBridge(QObject):
    """Bridge exposed to Frontend JS through QWebChannel."""
    videoSelected = Signal(str)
    exportSaved = Signal(str)

    def __init__(self, main_window):
        # The bridge's native lifetime belongs to the window just like its
        # WebEngine view and channel; registerObject() does not take ownership.
        super().__init__(main_window)
        self.main_window = main_window

    @Slot(result=str)
    def pickVideoFile(self) -> str:
        """Opens native Windows file picker for video selection."""
        file_path, _ = QFileDialog.getOpenFileName(
            self.main_window,
            "Chọn video Douyin / MP4",
            str(settings.INPUT_DIR),
            "Video files (*.mp4 *.mov *.webm *.mkv);;All files (*.*)"
        )
        if file_path:
            logger.info(f"Native file selected: {file_path}")
            self.main_window.notify_js_file_selected(file_path)
            return file_path
        return ""

    @Slot(str, result=str)
    def saveVideoAs(self, default_name: str) -> str:
        """Opens native Windows save file picker for video export."""
        source = settings.OUTPUT_DIR / Path(default_name).name
        if not source.is_file():
            QMessageBox.warning(self.main_window, "Chưa có video", "Hãy xuất video hoàn tất trước khi lưu.")
            return ""
        file_path, _ = QFileDialog.getSaveFileName(
            self.main_window,
            "Lưu video đã lồng tiếng",
            str(settings.OUTPUT_DIR / (default_name or "translated_video.mp4")),
            "MP4 Video (*.mp4);;All files (*.*)"
        )
        if file_path:
            try:
                if source.resolve() != Path(file_path).resolve():
                    shutil.copy2(source, file_path)
                return file_path
            except OSError as exc:
                QMessageBox.warning(self.main_window, "Không thể lưu", str(exc))
        return ""

    @Slot(str, str)
    def showNotification(self, title: str, message: str):
        """Displays native Windows tray notification."""
        if hasattr(self.main_window, "tray_icon") and self.main_window.tray_icon:
            self.main_window.tray_icon.showMessage(
                title or "Douyin2TikTok AI Studio",
                message or "Thông báo từ hệ thống",
                QSystemTrayIcon.MessageIcon.Information,
                4000
            )

    @Slot()
    def openLogsFolder(self):
        """Opens logs directory in Windows Explorer."""
        log_dir = settings.WORKSPACE_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(log_dir))
        except Exception as e:
            logger.error(f"Cannot open logs folder: {e}")

    @Slot(str, result=bool)
    def copyText(self, text: str) -> bool:
        """Copy plain diagnostic text through Qt when web clipboard is unavailable."""
        try:
            clipboard = QApplication.clipboard()
            if clipboard is None:
                return False
            clipboard.setText(text)
            return clipboard.text() == text
        except Exception:
            logger.warning("Could not copy diagnostic text to clipboard.")
            return False

# -------------------------------------------------------------
# STARTUP PRE-WARM WORKER THREAD
# -------------------------------------------------------------

class StartupWorker(QThread):
    progress = Signal(int, str)
    finished = Signal(int)
    error = Signal(str)

    def run(self):
        try:
            self.progress.emit(5, "Khởi động Internal Backend...")
            # 1. Start FastAPI backend on random free port
            port = service_manager.start_backend()
            self.progress.emit(15, "Backend đã kết nối thành công")

            # 2. Prewarm models & components
            def _cb(pct, text):
                self.progress.emit(pct, text)

            service_manager.prewarm_components(progress_callback=_cb)
            self.progress.emit(100, "Douyin2TikTok AI Studio Sẵn sàng!")
            self.finished.emit(port)
        except Exception as e:
            logger.exception("Startup worker failed")
            self.error.emit(str(e))

# -------------------------------------------------------------
# CUSTOM SPLASH SCREEN (DARK PREMIUM STUDIO STYLE)
# -------------------------------------------------------------

class StudioSplashScreen(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(520, 320)

        # Center on screen
        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - self.width()) // 2
        y = (screen.height() - self.height()) // 2
        self.move(x, y)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # Container card
        container = QWidget()
        container.setObjectName("splashCard")
        container.setStyleSheet("""
            QWidget#splashCard {
                background-color: #0d111a;
                border: 1px solid #1f293d;
                border-radius: 20px;
            }
        """)

        # Add drop shadow
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(35)
        shadow.setColor(QColor(0, 0, 0, 180))
        shadow.setOffset(0, 10)
        container.setGraphicsEffect(shadow)

        c_layout = QVBoxLayout(container)
        c_layout.setContentsMargins(32, 32, 32, 32)
        c_layout.setSpacing(12)

        # Title row
        title_box = QHBoxLayout()
        logo_label = QLabel()
        logo_label.setPixmap(create_app_icon().pixmap(52, 52))
        title_box.addWidget(logo_label)

        titles = QVBoxLayout()
        title = QLabel("Douyin2TikTok AI Studio")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #ffffff; font-family: 'Segoe UI', sans-serif;")
        subtitle = QLabel(f"{settings.ASR_ENGINE} {settings.WHISPER_MODEL_SIZE} · {settings.DEVICE.upper()} · {settings.TTS_ENGINE}")
        subtitle.setStyleSheet("font-size: 11px; color: #94a3b8; font-family: 'Segoe UI', sans-serif;")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        title_box.addLayout(titles)
        title_box.addStretch()
        c_layout.addLayout(title_box)

        c_layout.addSpacing(16)

        # Status & Progress
        self.status_label = QLabel("Đang khởi tạo studio...")
        self.status_label.setStyleSheet("font-size: 12px; color: #38bdf8; font-weight: 500; font-family: 'Consolas', monospace;")
        c_layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(5)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: #1e293b;
                border-radius: 4px;
                border: none;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ec4899, stop:0.5 #f43f5e, stop:1 #8b5cf6);
                border-radius: 4px;
            }
        """)
        c_layout.addWidget(self.progress_bar)

        # Bottom detail
        footer = QHBoxLayout()
        ver_label = QLabel("Phiên bản Desktop v2.0 · Localhost Hidden")
        ver_label.setStyleSheet("font-size: 10px; color: #475569;")
        self.pct_label = QLabel("5%")
        self.pct_label.setStyleSheet("font-size: 11px; color: #cbd5e1; font-weight: bold; font-family: monospace;")
        footer.addWidget(ver_label)
        footer.addStretch()
        footer.addWidget(self.pct_label)
        c_layout.addLayout(footer)

        layout.addWidget(container)

    def set_progress(self, val: int, msg: str):
        self.progress_bar.setValue(val)
        self.status_label.setText(msg)
        self.pct_label.setText(f"{val}%")

# -------------------------------------------------------------
# MAIN DESKTOP APPLICATION WINDOW
# -------------------------------------------------------------

class StudioMainWindow(QMainWindow):
    def __init__(self, port: int):
        super().__init__()
        self.port = port
        self.backend_url = f"http://127.0.0.1:{self.port}"
        self.app_icon = create_app_icon()
        self._fullscreen_restore_state = None
        self._exit_requested = False
        self._tray_notice_shown = False

        self.setWindowTitle("Douyin2TikTok AI Studio")
        self.setWindowIcon(self.app_icon)
        available = QApplication.primaryScreen().availableGeometry()
        self.setMinimumSize(min(1024, available.width()), min(640, available.height()))
        self.resize(min(1366, available.width()), min(860, available.height()))

        # Enable Drag & Drop
        self.setAcceptDrops(True)

        # Central Web Engine
        self.web_view = QWebEngineView(self)
        self.setCentralWidget(self.web_view)

        # Configure Web Settings
        settings_web = self.web_view.settings()
        settings_web.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings_web.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
        # Translation/TTS finishes asynchronously after the Start click. Qt's
        # user-gesture gate otherwise rejects the delayed audio.play() request.
        settings_web.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        settings_web.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings_web.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings_web.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
        settings_web.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, True)

        # Setup WebChannel Bridge
        self.bridge = DesktopBridge(self)
        self.channel = QWebChannel(self.web_view.page())
        self.channel.registerObject("desktopBridge", self.bridge)
        self.web_view.page().setWebChannel(self.channel)
        self.web_view.page().fullScreenRequested.connect(self._on_fullscreen_requested)

        # Keep explicit exit accessible even when the tray is hidden/unavailable.
        self._setup_exit_action()

        # Setup System Tray
        self._setup_system_tray()

        # Load Studio URL
        logger.info(f"Loading Desktop UI from internal backend: {self.backend_url}")
        self.web_view.load(QUrl(self.backend_url))

    def _setup_exit_action(self):
        self.exit_action = QAction("Thoát hoàn toàn", self)
        self.exit_action.setObjectName("exitStudioAction")
        self.exit_action.setShortcut(QKeySequence("Ctrl+Q"))
        self.exit_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.exit_action.triggered.connect(self.request_exit)
        self.addAction(self.exit_action)

    def _setup_system_tray(self):
        """Creates system tray icon and context menu."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon = None
            return

        self.tray_icon = QSystemTrayIcon(self.app_icon, self)
        self.tray_icon.setToolTip("Douyin2TikTok AI Studio")

        menu = QMenu(self)
        act_open = QAction("Mở Studio", self)
        act_open.triggered.connect(self.restore_window)
        menu.addAction(act_open)

        act_pause = QAction("Tạm dừng xử lý", self)
        act_pause.triggered.connect(lambda: self.run_js("if(window.studioPause) window.studioPause();"))
        menu.addAction(act_pause)

        act_resume = QAction("Tiếp tục xử lý", self)
        act_resume.triggered.connect(lambda: self.run_js("if(window.studioResume) window.studioResume();"))
        menu.addAction(act_resume)

        menu.addSeparator()

        menu.addAction(self.exit_action)

        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick or reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.restore_window()

    def request_exit(self):
        """Explicit exit (tray or Ctrl+Q) uses the normal close confirmation."""
        self._exit_requested = True
        self.close()

    def restore_window(self):
        if self._fullscreen_restore_state is not None:
            self.showFullScreen()
        elif self.isMaximized():
            self.showMaximized()
        else:
            self.showNormal()
        self.activateWindow()
        self.raise_()

    def _on_fullscreen_requested(self, request):
        """Keep the native window in step with the player's Fullscreen API."""
        if request.toggleOn():
            if self._fullscreen_restore_state is None:
                self._fullscreen_restore_state = (
                    self.windowState() & ~Qt.WindowState.WindowMinimized
                )
            request.accept()
            self.showFullScreen()
            return

        previous_state = self._fullscreen_restore_state
        self._fullscreen_restore_state = None
        request.accept()
        if previous_state is None:
            return
        if previous_state & Qt.WindowState.WindowFullScreen:
            self.showFullScreen()
        elif previous_state & Qt.WindowState.WindowMaximized:
            self.showMaximized()
        else:
            self.showNormal()

    def run_js(self, script: str):
        self.web_view.page().runJavaScript(script)

    def notify_js_file_selected(self, file_path: str):
        safe_path = json.dumps(file_path)
        self.run_js(f"if(window.loadDroppedLocalVideo) window.loadDroppedLocalVideo({safe_path});")

    # Drag & Drop Support
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            local_path = url.toLocalFile()
            if local_path and local_path.lower().endswith(('.mp4', '.mov', '.webm', '.mkv', '.avi')):
                logger.info(f"Video dropped from Windows Explorer: {local_path}")
                self.notify_js_file_selected(local_path)
                event.acceptProposedAction()
                return
        event.ignore()

    # Closing the window leaves the backend and its active page in the tray.
    def closeEvent(self, event):
        if (not self._exit_requested and self.tray_icon is not None
                and self.tray_icon.isVisible() and QSystemTrayIcon.isSystemTrayAvailable()):
            event.ignore()
            self.hide()
            if not self._tray_notice_shown:
                self._tray_notice_shown = True
                self.tray_icon.showMessage(
                    "Studio vẫn chạy ngầm",
                    "Tác vụ tiếp tục chạy. Bấm biểu tượng Studio để mở lại; chọn Thoát hoàn toàn để tắt.",
                    QSystemTrayIcon.MessageIcon.Information,
                    4000,
                )
            return

        from core.streaming.pipeline import active_streaming_sessions
        from main import active_export_tasks
        running_sessions = [s for s in active_streaming_sessions.values()
                            if getattr(s, "is_running", False) or getattr(s, "is_editing", False)]
        is_processing = bool(running_sessions) or any(
            task.get("status") in {"RUNNING", "CANCELLING"}
            for task in active_export_tasks.values()
        )

        if is_processing:
            reply = QMessageBox.question(
                self,
                "Xác nhận đóng Douyin2TikTok AI Studio",
                "Tiến trình dịch, lồng tiếng hoặc xuất video đang hoạt động.\nBạn có muốn dừng mọi tác vụ và thoát sạch ứng dụng?",
                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
                QMessageBox.StandardButton.Cancel
            )
            if reply != QMessageBox.StandardButton.Yes:
                self._exit_requested = False
                event.ignore()
                self.restore_window()
                return

        logger.info("Main window closing, shutting down all services...")
        if self.tray_icon:
            self.tray_icon.hide()
        service_manager.shutdown_all()
        event.accept()
        app = QApplication.instance()
        if app is not None:
            app.quit()

# -------------------------------------------------------------
# MAIN ENTRY POINT
# -------------------------------------------------------------

def main():
    configure_windows_identity()
    app = QApplication(sys.argv)
    app.setApplicationName("Douyin2TikTok AI Studio")
    app.setOrganizationName("Douyin2TikTok")
    app.setWindowIcon(create_app_icon())
    app.setQuitOnLastWindowClosed(False)

    # 1. Single Instance Check via QLocalSocket / QLocalServer
    local_socket = QLocalSocket()
    local_socket.connectToServer(SINGLE_INSTANCE_SERVER)
    if local_socket.waitForConnected(400):
        # Already running: signal existing instance to activate
        local_socket.write(b"ACTIVATE\n")
        local_socket.flush()
        local_socket.waitForBytesWritten(500)
        local_socket.disconnectFromServer()
        sys.exit(0)

    # First instance: start local server to listen for duplicate instances
    local_server = QLocalServer()
    local_server.removeServer(SINGLE_INSTANCE_SERVER)
    local_server.listen(SINGLE_INSTANCE_SERVER)

    # 2. Show Splash Screen
    splash = StudioSplashScreen()
    splash.show()

    # 3. Launch Startup Worker
    worker = StartupWorker()
    main_window_holder = {}

    def on_progress(pct, msg):
        splash.set_progress(pct, msg)

    def on_finished(port):
        logger.info(f"Startup completed. Launching main studio window on port {port}...")
        main_win = StudioMainWindow(port)
        main_window_holder["window"] = main_win
        splash.close()
        main_win.show()

        # Listen for secondary instance activations
        def handle_new_connection():
            client_socket = local_server.nextPendingConnection()
            if client_socket:
                client_socket.waitForReadyRead(300)
                msg = bytes(client_socket.readAll()).decode('utf-8', errors='ignore').strip()
                if msg == "ACTIVATE":
                    main_win.restore_window()
                client_socket.disconnectFromServer()

        local_server.newConnection.connect(handle_new_connection)

    def on_error(err_msg):
        service_manager.shutdown_all()
        splash.close()
        QMessageBox.critical(
            None,
            "Lỗi khởi động Douyin2TikTok AI Studio",
            f"Không thể khởi động hệ thống:\n{err_msg}\n\nVui lòng kiểm tra lại cấu hình hoặc xem file workspace/logs/errors.log."
        )
        sys.exit(1)

    worker.progress.connect(on_progress)
    worker.finished.connect(on_finished)
    worker.error.connect(on_error)
    worker.start()

    app.aboutToQuit.connect(service_manager.shutdown_all)
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
