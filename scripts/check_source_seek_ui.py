"""Read-only production Qt seeking acceptance using an existing local source."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.check_production_ui import (
    QApplication, StudioMainWindow, service_manager, until, evaluate, wait,
    click, event, QUrl, QCoreApplication, QEvent,
)
from core.media_process import run_media


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--port', type=int, help='Attach read-only to an already running local QA backend')
    args = parser.parse_args()
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error('--port must be a valid local port')
    source = args.source.resolve(strict=True)
    probe = json.loads(run_media(['ffprobe','-v','error','-show_format','-of','json',str(source)],capture_output=True))
    duration = float(probe['format']['duration'])
    assert duration > 241, 'This acceptance exercises several distant source windows'
    app = QApplication.instance() or QApplication([])
    app.setApplicationName('StudioSourceSeekAcceptance')
    app.setQuitOnLastWindowClosed(False)
    window = None
    state = """(() => {let v=document.getElementById('video-player');return {
      time:v.currentTime,ready:v.readyState,paused:v.paused,src:v.currentSrc,
      clock:document.getElementById('bar-current-time').textContent,
      duration:v.duration,error:v.error?.message,buffer:document.getElementById('buffering-text').textContent};})()"""
    def seek(seconds):
        evaluate(page, """(() => {const t=document.getElementById('segments-timeline-track'), r=t.getBoundingClientRect();
          t.dispatchEvent(new MouseEvent('click',{bubbles:true,clientX:r.left+r.width*"""+
          str(seconds+.3)+"/"+str(duration)+"}));return true;})()")
    try:
        port = args.port or service_manager.start_backend(timeout=30)
        window = StudioMainWindow(port)
        window.resize(1366,860)
        page = window.web_view.page()
        page.setAudioMuted(True)
        window.show()
        until(page,"document.readyState==='complete' && !!window.loadDroppedLocalVideo")
        wait(1000)
        evaluate(page,"(() => {window.loadDroppedLocalVideo("+json.dumps(str(source))+");return true;})()")
        until(page,"document.getElementById('video-player').readyState>=2",timeout=120)
        event('INITIAL',evaluate(page,state))
        for target in [160,162,5,160]:
            started=time.monotonic()
            seek(target)
            until(page,"(() => {const parts=document.getElementById('bar-current-time').textContent.split(':').map(Number);return Math.abs(parts[0]*60+parts[1]-"+str(target)+")<=1 && document.getElementById('video-player').readyState>=2;})()",timeout=60)
            event('SEEK',{'target':target,'elapsed':round(time.monotonic()-started,3),'player':evaluate(page,state)})
        # Read-ahead starts at Play; allow the next slot to prepare, then play
        # across a real decoder boundary. No provider/task is started.
        seek(160)
        click(page,'player-play-toggle')
        started=time.monotonic()
        until(page,"document.getElementById('bar-current-time').textContent==='02:49'",timeout=30)
        event('BOUNDARY',{'elapsed':round(time.monotonic()-started,3),'player':evaluate(page,state)})
        assert not evaluate(page,state)['paused']
        click(page,'player-play-toggle')
        seek(240);seek(20);seek(60)
        until(page,"['00:59','01:00','01:01'].includes(document.getElementById('bar-current-time').textContent) && document.getElementById('video-player').readyState>=2",timeout=60)
        wait(1000)
        event('LATEST_INTENT',evaluate(page,state))
    except Exception:
        if window: event('FAIL_STATE',evaluate(page,state))
        raise
    finally:
        if window:
            window.web_view.stop();window.web_view.setUrl(QUrl('about:blank'));wait(200)
            window.hide();window.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
            app.processEvents()
        if args.port is None:
            service_manager.shutdown_all()


if __name__ == '__main__':
    main()
