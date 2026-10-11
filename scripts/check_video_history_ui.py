"""Read-only acceptance of video history in the real offscreen production UI.

Uses real saved manifests and the normal HTTP/Qt page. No provider calls,
desktop input, project restores, transcript edits or execution starts.
"""
import argparse
import hashlib
import json
from pathlib import Path

from check_production_ui import (
    QApplication, QCoreApplication, QEvent, QUrl, ROOT, StudioMainWindow,
    backend, click, evaluate, event, service_manager, until, wait,
)


SOURCE_PROJECTS = {"94f80114", "d5820a71", "76f5aa6b", "5a7b66a9"}


def fingerprints():
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (ROOT / "workspace/projects").glob("*.json")}


def launch(app):
    port = service_manager.start_backend(timeout=30)
    window = StudioMainWindow(port)
    window.resize(1366, 860)
    page = window.web_view.page()
    page.setAudioMuted(True)
    window.show()  # Offscreen QPA: no native desktop window.
    try:
        until(page, "document.readyState==='complete' && !!window.openTaskInStudio")
        evaluate(page, """(() => {window.__acceptanceErrors=[];
            window.addEventListener('error',e=>window.__acceptanceErrors.push(e.message));
            window.addEventListener('unhandledrejection',e=>window.__acceptanceErrors.push(String(e.reason)));
            return true;})()""")
        assert click(page, "tab-tasks")
        until(page, "document.querySelectorAll('#tasks-table-body .history-video-row').length>0", timeout=60)
    except BaseException:
        close(app, window)
        raise
    return window, page, f"http://127.0.0.1:{port}"


def close(app, window):
    window.web_view.stop()
    window.web_view.setUrl(QUrl("about:blank"))
    wait(200)
    window.hide()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    service_manager.shutdown_all()
    assert not service_manager.is_running and service_manager.server_thread is None


def inspect_history(page, base):
    response = backend(base, "/api/tasks")
    tasks, videos = response["tasks"], response["videos"]
    expected = SOURCE_PROJECTS & {task["task_id"] for task in tasks}
    assert len(expected) >= 3, "The named real source projects are required for this acceptance"
    matches = [video for video in videos
               if expected & {version["task_id"] for version in video["versions"]}]
    assert len(matches) == 1, matches
    video = matches[0]
    assert expected <= {version["task_id"] for version in video["versions"]}, video
    assert len(videos) < len(tasks), (len(tasks), len(videos))
    expected_images = sum(bool(row.get("thumbnail_url")) for row in videos)
    assert expected_images > 0, "Actual retained videos must have thumbnail URLs"
    until(page, "(() => {const images=Array.from(document.querySelectorAll('#tasks-table-body .history-thumbnail img'));" +
          "return images.length===" + str(expected_images) +
          " && images.every(i=>i.complete && i.naturalWidth>0 && i.naturalHeight>0);})()", timeout=60)
    thumbnails = evaluate(page, """Array.from(document.querySelectorAll('#tasks-table-body .history-video-row'), row=>{
        const image=row.querySelector('.history-thumbnail img');
        const container=row.querySelector('.history-thumbnail');
        return {video_id:row.dataset.videoId,url:image?.getAttribute('src') || '',
          width:image?.naturalWidth || 0,height:image?.naturalHeight || 0,
          fit:image?getComputedStyle(image).objectFit:'',
          display_width:container?.getBoundingClientRect().width || 0,
          missing:!!row.querySelector('.history-thumbnail-empty'),
          title:row.querySelector('.history-video-title')?.textContent || ''};})""")
    for thumb in thumbnails:
        metadata = next(row for row in videos if row['video_id'] == thumb['video_id'])
        assert thumb['display_width'] >= 80, thumb
        if metadata.get('thumbnail_url'):
            assert thumb['url'] == metadata['thumbnail_url'], thumb
            assert max(thumb['width'], thumb['height']) <= 320, thumb
            assert thumb['fit'] == 'contain', thumb
        else:
            assert thumb['missing'] and not thumb['url'], thumb
    event('REAL_HISTORY_THUMBNAILS', {'loaded_images':expected_images,'rows':thumbnails})
    state = evaluate(page, """(() => {const id=""" + json.dumps(video["video_id"]) + """;
        const rows=Array.from(document.querySelectorAll('#tasks-table-body .history-video-row'));
        const row=rows.find(r=>r.dataset.videoId===id);
        if(!row)return null;
        return {rows:rows.length,matches:rows.filter(r=>r.dataset.videoId===id).length,
          title:row.querySelector('.history-video-title').textContent,
          status:row.querySelector('.history-status').dataset.status,
          task:row.dataset.taskId,session:row.dataset.sessionId,
          stage:row.querySelector('.history-progress-label').textContent,
          columns:row.cells.length,
          buttons:Array.from(row.querySelectorAll('button'),b=>b.getAttribute('onclick')),
          versions:Array.from(row.querySelectorAll('.history-version'),v=>v.dataset.sessionId),
          errors:window.__acceptanceErrors};})()""")
    assert state and state["rows"] == len(videos) and state["matches"] == 1, state
    expected_title = "Video Douyin" if video.get("source_url") else video["title"]
    assert state["title"] == expected_title and state["task"] == video["task_id"], state
    assert state["session"] == video["session_id"] and state["columns"] == 4, state
    assert video.get("stage", "") in state["stage"] and not state["errors"], state
    if video["can_open"]:
        assert f"window.openTaskInStudio('{video['session_id']}')" in state["buttons"], state
    assert expected - {video["session_id"]} <= set(state["versions"]), state
    for task_id in expected - {video["session_id"]}:
        version = next(v for v in video["versions"] if v["task_id"] == task_id)
        if version["can_open"]:
            assert f"window.openTaskInStudio('{task_id}')" in state["buttons"], state
    event("REAL_VIDEO_HISTORY", {"raw_tasks":len(tasks), "videos":len(videos),
        "same_source_projects":sorted(expected), "view":state})
    return video["video_id"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshot", type=Path, help="Optional explicit screenshot destination.")
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    assert app.platformName() == "offscreen"
    app.setQuitOnLastWindowClosed(False)
    before = fingerprints()
    window = None
    try:
        window, page, base = launch(app)
        identity = inspect_history(page, base)
        # Use the real details and refresh handlers without opening a project.
        evaluate(page, """(() => {const row=Array.from(document.querySelectorAll('.history-video-row'))
            .find(r=>r.dataset.videoId===""" + json.dumps(identity) + """);
            row.querySelector('details').open=true;return true;})()""")
        wait(100)
        assert click(page, "btn-refresh-tasks")
        until(page, "!!Array.from(document.querySelectorAll('.history-video-row')).find(r=>r.dataset.videoId===" +
              json.dumps(identity) + " && r.querySelector('details').open)")
        wait(500)
        if args.screenshot:
            evaluate(page, "(() => {document.querySelectorAll('.history-versions').forEach(e=>e.open=false);return true;})()")
            wait(100)
            assert window.web_view.grab().save(str(args.screenshot))
        close(app, window)
        window = None
        assert fingerprints() == before, "Read-only history must not rewrite any saved project"
        window, page, base = launch(app)
        assert inspect_history(page, base) == identity
        close(app, window)
        window = None
        assert fingerprints() == before, "Restarting history QA must preserve saved manifests"
        event("PASS", {"projects_unchanged":len(before), "provider_calls":0,
            "grouping_survives_restart":True, "desktop_input":False})
    finally:
        if window is not None:
            close(app, window)
        event("QA_STOPPED", True)


if __name__ == "__main__":
    main()
