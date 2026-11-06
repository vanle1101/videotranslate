from core.runtime_revision import source_revision


def test_revision_tracks_source_not_user_data(tmp_path):
    (tmp_path / "core").mkdir()
    for name in ("main.py", "config.py", "core/worker.py"):
        (tmp_path / name).write_text("original", encoding="utf-8")
    baseline = source_revision(tmp_path)
    (tmp_path / ".env").write_text("private setting", encoding="utf-8")
    (tmp_path / "video.mp4").write_bytes(b"user video")
    assert source_revision(tmp_path) == baseline
    (tmp_path / "core/worker.py").write_text("repaired", encoding="utf-8")
    assert source_revision(tmp_path) != baseline


def test_revision_tracks_desktop_and_frontend_build_files(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "templates").mkdir()
    (tmp_path / "static").mkdir()
    for name in ("main.py", "config.py", "desktop_app.py", "core/worker.py",
                 "templates/index.html", "static/app.js", "static/style.css"):
        (tmp_path / name).write_text("original", encoding="utf-8")
    baseline = source_revision(tmp_path)
    (tmp_path / "static/app.js").write_text("frontend changed", encoding="utf-8")
    assert source_revision(tmp_path) != baseline


def test_health_retains_started_revision_when_source_changes(monkeypatch):
    import asyncio
    import main
    monkeypatch.setattr(main, "STARTED_SOURCE_REVISION", "started")
    monkeypatch.setattr(main, "current_source_revision", lambda: "changed")
    health = asyncio.run(main.health())
    assert health["source_revision"] == health["started_source_revision"] == "started"
    assert health["disk_source_revision"] == "changed"
    assert health["source_matches_disk"] is False


def test_studio_page_embeds_disk_and_started_fingerprints(monkeypatch):
    import asyncio
    from starlette.requests import Request
    import main
    monkeypatch.setattr(main, "STARTED_SOURCE_REVISION", "backend-revision")
    monkeypatch.setattr(main, "current_source_revision", lambda: "disk-revision")
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": [],
                       "query_string": b"", "server": ("test", 80), "client": ("test", 1),
                       "scheme": "http"})
    response = asyncio.run(main.index(request))
    body = response.body.decode("utf-8")
    assert 'name="studio-source-revision" content="disk-revision"' in body
    assert 'name="studio-backend-revision" content="backend-revision"' in body
