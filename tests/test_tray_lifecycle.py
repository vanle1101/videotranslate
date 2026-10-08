"""Exercise native close events without starting Studio's backend or WebEngine."""

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def tray_window(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    import desktop_app
    from PySide6.QtWidgets import QApplication, QMainWindow

    app = QApplication.instance() or QApplication([])
    previous_quit_policy = app.quitOnLastWindowClosed()
    app.setQuitOnLastWindowClosed(False)

    # The close handler imports these registries lazily. Keep real pipelines,
    # API routes, model loading, and user-owned Studio processes out of the test.
    pipeline = ModuleType("core.streaming.pipeline")
    pipeline.active_streaming_sessions = {}
    backend = ModuleType("main")
    backend.active_export_tasks = {}
    monkeypatch.setitem(sys.modules, "core.streaming.pipeline", pipeline)
    monkeypatch.setitem(sys.modules, "main", backend)
    shutdown = Mock()
    monkeypatch.setattr(
        desktop_app, "service_manager", SimpleNamespace(shutdown_all=shutdown)
    )
    quit_app = Mock()
    fake_app = SimpleNamespace(quit=quit_app)
    monkeypatch.setattr(
        desktop_app,
        "QApplication",
        SimpleNamespace(instance=lambda: fake_app, quit=quit_app),
    )
    tray_available = Mock(return_value=True)
    monkeypatch.setattr(
        desktop_app.QSystemTrayIcon, "isSystemTrayAvailable", tray_available
    )
    question = Mock(return_value=desktop_app.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(desktop_app.QMessageBox, "question", question)

    class LifecycleWindow(QMainWindow):
        _setup_exit_action = desktop_app.StudioMainWindow._setup_exit_action
        request_exit = desktop_app.StudioMainWindow.request_exit
        restore_window = desktop_app.StudioMainWindow.restore_window
        _on_tray_activated = desktop_app.StudioMainWindow._on_tray_activated

        def __init__(self):
            super().__init__()
            self._exit_requested = False
            self._tray_notice_shown = False
            self._fullscreen_restore_state = None
            self.tray_icon = Mock()
            self.tray_icon.isVisible.return_value = True
            self.last_close_accepted = None
            self._setup_exit_action()

        def closeEvent(self, event):
            desktop_app.StudioMainWindow.closeEvent(self, event)
            self.last_close_accepted = event.isAccepted()

    window = LifecycleWindow()
    window.show()
    app.processEvents()
    state = SimpleNamespace(
        window=window,
        app=app,
        desktop=desktop_app,
        sessions=pipeline.active_streaming_sessions,
        exports=backend.active_export_tasks,
        shutdown=shutdown,
        quit_app=quit_app,
        question=question,
        tray_available=tray_available,
    )
    yield state
    window.hide()
    window.deleteLater()
    app.processEvents()
    app.setQuitOnLastWindowClosed(previous_quit_policy)


def add_work(state, kind):
    if kind in {"RUNNING", "CANCELLING"}:
        state.exports["export"] = {"status": kind}
        return None
    session = SimpleNamespace(
        is_running=kind in {"running", "paused"},
        is_editing=kind == "editing",
        is_paused=kind == "paused",
        stop=Mock(),
    )
    state.sessions["translation"] = session
    return session


@pytest.mark.parametrize("work", ["idle", "running", "editing", "paused", "RUNNING"])
def test_close_hides_and_restore_keeps_work_alive(tray_window, work):
    state = tray_window
    session = add_work(state, work) if work != "idle" else None
    window = state.window
    tray = window.tray_icon

    for _ in range(2):
        # QMainWindow.close() delivers the same event as the title-bar X.
        assert window.close() is False
        state.app.processEvents()
        assert window.last_close_accepted is False
        assert not window.isVisible()
        assert not window._exit_requested
        tray.hide.assert_not_called()
        state.question.assert_not_called()
        state.shutdown.assert_not_called()
        state.quit_app.assert_not_called()

        window._on_tray_activated(
            state.desktop.QSystemTrayIcon.ActivationReason.DoubleClick
        )
        state.app.processEvents()
        assert window.isVisible()

    tray.showMessage.assert_called_once()
    if session is not None:
        session.stop.assert_not_called()
        assert session.is_running == (work in {"running", "paused"})
        assert session.is_editing == (work == "editing")
        assert session.is_paused == (work == "paused")
    if work == "RUNNING":
        assert state.exports["export"]["status"] == "RUNNING"


@pytest.mark.parametrize("work", ["running", "editing", "paused", "RUNNING", "CANCELLING"])
def test_cancel_exit_restores_window_and_next_close_only_hides(tray_window, work):
    state = tray_window
    add_work(state, work)
    state.question.return_value = state.desktop.QMessageBox.StandardButton.Cancel
    window = state.window
    window.close()
    assert not window.isVisible()

    window.request_exit()
    state.app.processEvents()
    assert window.last_close_accepted is False
    assert not window._exit_requested
    assert window.isVisible()
    state.question.assert_called_once()
    state.shutdown.assert_not_called()
    state.quit_app.assert_not_called()

    window.close()
    assert not window.isVisible()
    assert window.last_close_accepted is False
    state.question.assert_called_once()
    state.shutdown.assert_not_called()
    state.quit_app.assert_not_called()


@pytest.mark.parametrize("work", ["idle", "running", "editing", "paused", "RUNNING", "CANCELLING"])
def test_explicit_exit_from_tray_shuts_down_and_quits(tray_window, work):
    state = tray_window
    if work != "idle":
        add_work(state, work)
    window = state.window
    window.close()
    assert not window.isVisible()

    window.request_exit()
    assert window.last_close_accepted is True
    window.tray_icon.hide.assert_called_once()
    state.shutdown.assert_called_once_with()
    state.quit_app.assert_called_once_with()
    if work == "idle":
        state.question.assert_not_called()
    else:
        state.question.assert_called_once()


@pytest.mark.parametrize("tray_state", ["missing", "unavailable", "invisible"])
def test_close_without_usable_tray_shuts_down_and_quits(tray_window, tray_state):
    state = tray_window
    if tray_state == "missing":
        state.window.tray_icon = None
    elif tray_state == "unavailable":
        state.tray_available.return_value = False
    else:
        state.window.tray_icon.isVisible.return_value = False

    assert state.window.close() is True
    assert state.window.last_close_accepted is True
    assert not state.window.isVisible()
    state.shutdown.assert_called_once_with()
    state.quit_app.assert_called_once_with()


def test_close_without_tray_can_cancel_active_work(tray_window):
    state = tray_window
    state.window.tray_icon = None
    add_work(state, "running")
    state.question.return_value = state.desktop.QMessageBox.StandardButton.Cancel

    assert state.window.close() is False
    assert state.window.isVisible()
    state.question.assert_called_once()
    state.shutdown.assert_not_called()
    state.quit_app.assert_not_called()


@pytest.mark.parametrize("work", ["idle", "running", "editing", "RUNNING"])
def test_quit_shortcut_uses_normal_exit_with_child_focused(tray_window, work):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLineEdit

    state = tray_window
    window = state.window
    if work != "idle":
        add_work(state, work)
    editor = QLineEdit(window)
    window.setCentralWidget(editor)
    window.activateWindow()
    editor.setFocus()
    state.app.processEvents()

    QTest.keyClick(editor, Qt.Key.Key_Q, Qt.KeyboardModifier.ControlModifier)
    state.app.processEvents()

    assert window.last_close_accepted is True
    assert window._exit_requested
    state.shutdown.assert_called_once_with()
    state.quit_app.assert_called_once_with()
    if work == "idle":
        state.question.assert_not_called()
    else:
        state.question.assert_called_once()


def test_quit_action_keeps_running_work_when_exit_cancelled(tray_window):
    state = tray_window
    session = add_work(state, "running")
    state.question.return_value = state.desktop.QMessageBox.StandardButton.Cancel

    state.window.exit_action.trigger()
    state.app.processEvents()

    assert state.window.last_close_accepted is False
    assert not state.window._exit_requested
    assert state.window.isVisible()
    state.question.assert_called_once()
    state.shutdown.assert_not_called()
    state.quit_app.assert_not_called()
    session.stop.assert_not_called()

    # The next title-bar close still only hides; cancelled exit is not sticky.
    assert state.window.close() is False
    assert not state.window.isVisible()
    state.shutdown.assert_not_called()


def test_quit_action_available_without_system_tray(tray_window):
    state = tray_window
    state.window.tray_icon = None

    assert state.window.exit_action in state.window.actions()
    assert state.window.exit_action.shortcut().toString() == "Ctrl+Q"
    state.window.exit_action.trigger()

    assert state.window.last_close_accepted is True
    state.shutdown.assert_called_once_with()
    state.quit_app.assert_called_once_with()
