"""Real Qt/Chromium fullscreen acceptance, offscreen/muted and read-only.

Uses a retained source video without restoring/editing a user's active project.
All pointer/keyboard events target the offscreen QA widget, never the desktop.
"""
import argparse
import hashlib
import json

from check_production_ui import (
    QApplication, QCoreApplication, QEvent, QUrl, ROOT, StudioMainWindow,
    click, evaluate, event, service_manager, until, wait,
)
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from core.streaming.session_store import _read


def pointer_click(window, page, element_id):
    point = evaluate(page, """(() => {const e=document.getElementById(""" + json.dumps(element_id) + """);
        e.scrollIntoView({block:'center'});const r=e.getBoundingClientRect();
        return {x:r.x+r.width/2,y:r.y+r.height/2};})()""")
    wait(100)
    QTest.mouseClick(window.web_view.focusProxy(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, QPoint(round(point['x']), round(point['y'])))


def state(window, page):
    return {"native_fullscreen":window.isFullScreen(), "native_maximized":window.isMaximized(),
        "restore_state":None if window._fullscreen_restore_state is None else window._fullscreen_restore_state.value,
        **evaluate(page, """({dom_fullscreen:!!document.fullscreenElement,
            studio_visible:!document.getElementById('view-studio').classList.contains('hidden'),
            settings_visible:!document.getElementById('view-settings').classList.contains('hidden'),
            player_time:document.getElementById('video-player').currentTime,
            paused:document.getElementById('video-player').paused,
            source:document.getElementById('video-player').currentSrc,
            fullscreen_label:document.getElementById('player-fullscreen').getAttribute('aria-label'),
            errors:window.__acceptanceErrors})""")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reproduce', action='store_true', help='Record the current failure before fixing.')
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    assert app.platformName() == 'offscreen'
    app.setQuitOnLastWindowClosed(False)
    window = None
    project_path = ROOT / 'workspace/projects/d5820a71.json'
    project_before = hashlib.sha256(project_path.read_bytes()).hexdigest()
    try:
        port = service_manager.start_backend(timeout=30)
        window = StudioMainWindow(port)
        window.resize(1366, 860)
        page = window.web_view.page()
        page.setAudioMuted(True)
        window.show()
        until(page, "document.readyState==='complete' && !!window.loadDroppedLocalVideo")
        evaluate(page, """(() => {window.__acceptanceErrors=[];
            window.addEventListener('error',e=>window.__acceptanceErrors.push(e.message));
            window.addEventListener('unhandledrejection',e=>window.__acceptanceErrors.push(String(e.reason)));
            return true;})()""")
        source = _read('d5820a71')['session']['video_path']
        evaluate(page, '(() => {window.loadDroppedLocalVideo(' + json.dumps(source) + ');return true;})()')
        until(page, "document.getElementById('video-player').readyState>=2", timeout=120)
        pointer_click(window, page, 'player-play-toggle')
        until(page, "!document.getElementById('video-player').paused && document.getElementById('video-player').currentTime>=.25")
        pointer_click(window, page, 'player-fullscreen')
        until(page, '!!document.fullscreenElement')
        wait(250)
        entered = state(window, page)
        assert entered['native_fullscreen'] and entered['dom_fullscreen'], entered
        event('FULLSCREEN_ENTERED_REAL_UI', entered)
        pointer_click(window, page, 'player-settings')
        until(page, "!document.getElementById('view-settings').classList.contains('hidden')")
        wait(500)
        opened = state(window, page)
        event('FULLSCREEN_SETTINGS_REAL_UI', opened)
        if args.reproduce:
            assert opened['native_fullscreen'] and not opened['studio_visible'], opened
            QTest.keyClick(window.web_view.focusProxy(), Qt.Key.Key_Escape)
            wait(300)
            escaped = state(window, page)
            event('ESCAPE_AFTER_SETTINGS_REAL_UI', escaped)
            assert escaped['native_fullscreen'], escaped
            event('FAILURE_REPRODUCED', True)
        else:
            assert not opened['native_fullscreen'] and not opened['dom_fullscreen'], opened
            assert opened['restore_state'] is None and not opened['errors'], opened
            assert not opened['paused'] and opened['source'] == entered['source'], opened
            assert opened['player_time'] >= entered['player_time'], (entered, opened)
            assert click(page, 'btn-settings-back')
            until(page, "!document.getElementById('view-studio').classList.contains('hidden')")
            pointer_click(window, page, 'player-fullscreen')
            until(page, '!!document.fullscreenElement')
            QTest.keyClick(window.web_view.focusProxy(), Qt.Key.Key_Escape)
            until(page, '!document.fullscreenElement')
            wait(300)
            escaped = state(window, page)
            event('ESCAPE_RECOVERY_REAL_UI', escaped)
            assert not escaped['native_fullscreen'] and escaped['restore_state'] is None, escaped
            # Preserve the native maximized state, not merely a non-fullscreen DOM.
            window.showMaximized()
            wait(150)
            pointer_click(window, page, 'player-fullscreen')
            until(page, '!!document.fullscreenElement')
            pointer_click(window, page, 'player-settings')
            until(page, "!document.fullscreenElement && !document.getElementById('view-settings').classList.contains('hidden')")
            wait(150)
            maximized = state(window, page)
            assert not maximized['native_fullscreen'] and maximized['native_maximized'], maximized
            event('MAXIMIZED_SETTINGS_RESTORED_REAL_UI', maximized)
            # Inject the old broken hiding operation to verify independent native
            # Escape from a focused Settings form even if navigation regresses.
            assert click(page, 'btn-settings-back')
            pointer_click(window, page, 'player-fullscreen')
            until(page, '!!document.fullscreenElement')
            evaluate(page, """(() => {document.getElementById('view-studio').classList.add('hidden');
                document.getElementById('view-settings').classList.remove('hidden');
                document.getElementById('settings-gemini-model').focus();return true;})()""")
            QTest.keyClick(window.web_view.focusProxy(), Qt.Key.Key_Escape)
            until(page, '!document.fullscreenElement')
            wait(200)
            recovered = state(window, page)
            assert not recovered['native_fullscreen'] and recovered['native_maximized'], recovered
            event('OLD_HIDDEN_PLAYER_ESCAPE_FAULT_RECOVERED', recovered)
            # Normal form Escape must remain a form/modal event, with the native
            # action disabled instead of capturing it globally.
            assert not window.fullscreen_exit_action.isEnabled()
            assert click(page, 'btn-settings-back')
            window.showNormal()
            wait(150)
            event('PASS', {'native_input_to_desktop':False, 'provider_calls':0,
                'real_playback':True,'maximized_restore':True,'settings_form_escape':True})
    finally:
        if window is not None:
            window.web_view.stop()
            window.web_view.setUrl(QUrl('about:blank'))
            wait(200)
            window.hide()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
        service_manager.shutdown_all()
        assert not service_manager.is_running and service_manager.server_thread is None
        assert hashlib.sha256(project_path.read_bytes()).hexdigest() == project_before
        event('QA_STOPPED', True)


if __name__ == '__main__':
    main()
