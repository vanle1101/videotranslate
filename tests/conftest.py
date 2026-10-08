"""Qt test ownership: destroy native widgets on the GUI thread, before workers."""
import gc
import logging
import os
import threading

import pytest


_TEST_LOG_HANDLERS = []


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
