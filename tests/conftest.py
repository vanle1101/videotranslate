"""Qt test ownership: destroy native widgets on the GUI thread, before workers."""
import gc
import logging
import os
import threading

import pytest


_TEST_LOG_HANDLERS = []


@pytest.fixture(autouse=True)
def isolate_startup_recovery(monkeypatch, request, tmp_path):
    # Offline suites may launch the actual HTTP server. Never let that startup
    # discover and mutate a user's active projects. Dedicated coordinator tests
    # override this with their own explicitly controlled candidate manifests.
    if request.node.path.name not in {"test_session_store.py", "test_retry_schedule.py",
            "test_restart_coordinator.py", "test_restart_history_race.py", "test_shared_glossary.py"}:
        monkeypatch.setattr("core.streaming.session_store.list_restart_candidates", lambda **kwargs: [])
    # ASGITransport-only tests intentionally omit lifespan. A previous test's
    # shutdown fence must not masquerade as their application's running state.
    monkeypatch.setattr("main._backend_shutting_down", False)
    if request.node.path.name in {
            "test_backend_loop.py", "test_desktop_e2e.py", "test_desktop_app.py",
            "test_douyin_cookie_api.py", "test_history_thumbnail.py", "test_local_runtime.py",
            "test_media_preview.py", "test_muse_api.py", "test_opencode_api.py",
            "test_openrouter_api.py", "test_random_ports.py", "test_static_asset_versions.py",
            "test_streaming_audio_api.py", "test_translation_worker_settings.py",
            "test_url_progress.py", "test_voice_api.py", "test_workspace_lease_lifecycle.py"}:
        # Lifespan tests exercise the real lock, but never acquire ownership of
        # the user's live workspace. More specific fixtures may replace this.
        monkeypatch.setattr("config.settings.WORKSPACE_DIR", tmp_path / "workspace")


def pytest_sessionstart(session):
    # Install before collection imports ServiceManager. Offline provider/error
    # fixtures must never pollute the user's live Diagnostics files. Records
    # still propagate to pytest/caplog; real acceptance scripts run separately.
    for name in ("app", "ai", "pipeline", "errors"):
        logger = logging.getLogger(name)
        handler = logging.NullHandler()
        logger.addHandler(handler)
        _TEST_LOG_HANDLERS.append((logger, handler))


def pytest_sessionfinish(session, exitstatus):
    for logger, handler in _TEST_LOG_HANDLERS:
        logger.removeHandler(handler)
        handler.close()
    _TEST_LOG_HANDLERS.clear()


@pytest.fixture(scope="session")
def qt_app():
    # Every collected Qt test uses this fixture before constructing QApplication.
    # Keep one strong application reference throughout the suite; a fixture-local
    # wrapper must not become collectible on an unrelated backend worker.
    original_platform = os.environ.get("QT_QPA_PLATFORM")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QCoreApplication, QEvent, QThread
    from PySide6.QtWidgets import QApplication

    assert threading.current_thread() is threading.main_thread()
    app = QApplication.instance() or QApplication([])
    assert app.platformName() == "offscreen", "Qt tests must never use a visible desktop"
    assert app.thread() == QThread.currentThread()
    old_quit_policy = app.quitOnLastWindowClosed()
    app.setQuitOnLastWindowClosed(False)
    yield app
    assert app.thread() == QThread.currentThread()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    gc.collect()
    app.setQuitOnLastWindowClosed(old_quit_policy)
    if original_platform is None:
        os.environ.pop("QT_QPA_PLATFORM", None)
    else:
        os.environ["QT_QPA_PLATFORM"] = original_platform


@pytest.fixture
def qt_objects(qt_app):
    """Register test-owned roots; assert all native children die before return."""
    from PySide6.QtCore import QCoreApplication, QEvent, QObject, QThread
    from shiboken6 import isValid

    roots = []

    def register(obj):
        roots.append(obj)
        return obj

    yield register
    assert qt_app.thread() == QThread.currentThread()
    owned = []
    for obj in reversed(roots):
        if not isValid(obj):
            continue
        owned.extend([obj, *obj.findChildren(QObject)])
        if hasattr(obj, "web_view"):
            obj.web_view.stop()
        if hasattr(obj, "hide"):
            obj.hide()
        # close() usually only hides, and processEvents() alone does not deliver
        # DeferredDelete. Python cycles otherwise destroy WebEngine during a
        # later allocation/GC in Uvicorn's worker thread.
        obj.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qt_app.processEvents()
    assert all(not isValid(obj) for obj in owned), "Test-owned native Qt objects leaked"
    roots.clear()
    owned.clear()
    gc.collect()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
