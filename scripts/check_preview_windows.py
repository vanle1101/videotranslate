"""Real Qt acceptance for long local-file compatibility preview windows.

This deliberately does not start translation or mock HTTP/media.  It creates a
small owned 21-minute H.264/AAC source, selects it through the production
desktop bridge, waits for the native codec fallback, and seeks the real Studio
timeline at 15 minutes and back.  The temporary source and all preview jobs
are removed in ``finally``.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from core.media_preview import preview_manager
from core.services.service_manager import service_manager
from desktop_app import StudioMainWindow


SOURCE_SECONDS = 21 * 60
SEEK_SECONDS = 15 * 60


def wait(milliseconds=100):
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def evaluate(page, expression):
    values = []
    loop = QEventLoop()
    page.runJavaScript("JSON.stringify(" + expression + ")", lambda value: (values.append(value), loop.quit()))
    QTimer.singleShot(10000, loop.quit)
    loop.exec()
    assert values and values[0], "JavaScript did not return a serializable result"
    return json.loads(values[0])


def until(page, expression, timeout=60):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = evaluate(page, expression)
        if last:
            return last
        wait()
    raise AssertionError(f"Timed out: {expression}; last={last!r}")


def emit(name, value):
    print(name, json.dumps(value, ensure_ascii=False), flush=True)


def make_source(directory):
    source = Path(directory) / "long-preview-window.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-f", "lavfi", "-i", f"color=c=blue:s=160x240:r=24:d={SOURCE_SECONDS}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=16000:d={SOURCE_SECONDS}",
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p", "-b:v", "96k", "-c:a", "aac", "-b:a", "24k",
        "-shortest", str(source),
    ], check=True, timeout=120)
    assert source.is_file() and source.stat().st_size < 100_000_000
    return source


def jobs():
    with preview_manager._lock:
        return [{key: value for key, value in job.items()
                 if key in {"status", "start_seconds", "end_seconds", "coverage_seconds",
                            "source_duration", "partial", "error"}}
                | {"preview_id": key} for key, job in preview_manager._jobs.items()]


def click_timeline(page, seconds):
    return evaluate(page, """((target) => {
      const t = document.getElementById('segments-timeline-track');
      if (!t) return false;
      const max = Number(t.getAttribute('aria-valuemax'));
      const r = t.getBoundingClientRect();
      if (!Number.isFinite(max) || !r.width) return false;
      t.dispatchEvent(new MouseEvent('click', {bubbles:true, clientX:r.left + r.width * target / max,
                                               clientY:r.top + r.height / 2}));
      return {max, target};
    })(""" + str(float(seconds)) + ")")


def current_window(page, target, timeout=90):
    deadline = time.monotonic() + timeout
    state = {}
    while time.monotonic() < deadline:
        state = evaluate(page, """(() => {const v=document.getElementById('video-player');
            return {src:v.currentSrc,time:v.currentTime,duration:v.duration,ready:v.readyState,
                    error:v.error?.message||null};})()""")
        matching = [job for job in jobs() if job['status'] == 'READY'
                    and '/api/preview/' + job['preview_id'] + '/media' in state['src']]
        if matching and state['ready'] >= 2:
            job = matching[0]
            global_time = job['start_seconds'] + state['time']
            # Native MouseEvent coordinates round to physical CSS pixels. A
            # 21-minute timeline has around a second of time per pixel here.
            if abs(global_time - target) < 2:
                assert not state['error'], state
                assert abs(state['duration'] - (job['end_seconds'] - job['start_seconds'])) < .2, (state, job)
                return {'ui': state, 'job': job, 'global_time': global_time}
        wait(100)
    raise AssertionError({'target': target, 'ui': state, 'jobs': jobs()})


def main():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window = None
    temp = tempfile.mkdtemp(prefix="preview-window-", dir=ROOT / "workspace" / "temp")
    source = None
    try:
        source = make_source(temp)
        port = service_manager.start_backend(timeout=30)
        base = f"http://127.0.0.1:{port}"
        emit("BACKEND", {"port": port, "source": str(source), "bytes": source.stat().st_size})
        window = StudioMainWindow(port)
        window.resize(1280, 800)
        page = window.web_view.page()
        page.setAudioMuted(True)
        window.show()
        until(page, "document.readyState==='complete' && !!window.loadDroppedLocalVideo")
        window.notify_js_file_selected(str(source))
        until(page, "document.getElementById('file-name-display').textContent.includes('long-preview-window.mp4')")

        # The native H.264/AAC path must fail in this Qt build and trigger the
        # real compatibility request.  Do not invoke the API directly here.
        until(page, "document.getElementById('video-player').currentSrc.includes('/api/preview/')", timeout=90)
        initial = until(page, "(() => {const v=document.getElementById('video-player'); const t=document.getElementById('segments-timeline-track'); return v.readyState>=2 && Number.isFinite(v.duration) && Number(t.getAttribute('aria-valuemax'))>120 ? {src:v.currentSrc,duration:v.duration,ready:v.readyState,max:t.getAttribute('aria-valuemax'),bar:document.getElementById('bar-total-time').textContent} : false;})()")
        emit("INITIAL_COMPATIBILITY_PASS", {"ui": initial, "jobs": jobs()})
        assert initial["src"].find("/api/preview/") >= 0
        assert abs(float(initial["duration"]) - 24) < 2, initial
        assert initial["max"] == str(SOURCE_SECONDS), initial

        click = click_timeline(page, SEEK_SECONDS)
        assert click and click["max"] == SOURCE_SECONDS, click
        deadline = time.monotonic() + 90
        later = None
        while time.monotonic() < deadline:
            later = [job for job in jobs() if job.get("start_seconds") is not None and
                     float(job.get("start_seconds") or 0) > 800]
            if later and any(job.get("status") == "READY" for job in later):
                break
            wait(200)
        # The preview worker marks the job READY before the browser's media
        # element has fetched metadata for the new URL.  Waiting only for the
        # backend status creates a false failure (the old 24-second source is
        # still visible for a short interval).  Wait for the real UI source
        # replacement and metadata before asserting the result.
        located = current_window(page, SEEK_SECONDS)
        after_seek = located['ui']
        emit("SEEK_15M", {"click": click, "jobs": later, "ui": after_seek})
        assert later and any(job.get("status") == "READY" for job in later), {"jobs": jobs(), "ui": after_seek}
        assert abs(after_seek["duration"] - 120) < 3 and after_seek["time"] >= 0, after_seek

        # Return near the prefix so the existing 0-second compatibility
        # window can be reused (a click at 120 seconds intentionally creates a
        # 108–228-second window and is not a return-to-start check).
        click_timeline(page, 5)
        deadline = time.monotonic() + 90
        earlier = None
        while time.monotonic() < deadline:
            earlier = [job for job in jobs() if float(job.get("start_seconds") or 0) < 5]
            if len(earlier) >= 2 and any(job.get("status") == "READY" for job in earlier):
                break
            wait(200)
        # Returning to 5s may produce the 0–120s window rather than reuse the
        # initial 0–24s window. Assert the actual global playhead and decoded
        # media identified by currentSrc, not one assumed cache duration.
        returned = current_window(page, 5)
        back = returned['ui']
        emit("SEEK_BACK", {"jobs": earlier, "ui": back})
        assert earlier and any(job.get("status") == "READY" for job in earlier), {"jobs": jobs(), "ui": back}
        emit("PASS", {"source_duration": SOURCE_SECONDS, "initial": initial,
                       "seek_15m": located, "seek_back": returned})
    finally:
        try:
            service_manager.shutdown_all()
        finally:
            if window is not None:
                window.web_view.stop()
                window.hide()
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                app.processEvents()
            shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    main()
