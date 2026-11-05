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


def test_health_retains_started_revision_when_source_changes(monkeypatch):
    import asyncio
    import main
    monkeypatch.setattr(main, "STARTED_SOURCE_REVISION", "started")
    monkeypatch.setattr("core.runtime_revision.source_revision", lambda: "changed")
    assert asyncio.run(main.health())["source_revision"] == "started"
