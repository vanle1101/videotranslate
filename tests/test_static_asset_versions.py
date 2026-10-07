"""A normal desktop reload must pick up changed JavaScript and styles."""
import re
from fastapi.testclient import TestClient

import main
from config import settings


def test_index_versions_assets_from_current_file_metadata(tmp_path, monkeypatch):
    static = tmp_path / "static"
    static.mkdir()
    for name in ("app.js", "style.css"):
        (static / name).write_text("first", encoding="utf-8")
    monkeypatch.setattr(settings, "BASE_DIR", tmp_path)
    with TestClient(main.app) as client:
        first = client.get("/")
        assert first.status_code == 200
        for name in ("app.js", "style.css"):
            assert f"/static/{name}?v={(static / name).stat().st_mtime_ns}" in first.text
        script = static / "app.js"
        import os
        stamp = script.stat().st_mtime_ns + 1_000_000_000
        os.utime(script, ns=(stamp, stamp))
        second = client.get("/")
        assert re.search(r'/static/app.js\?v=\d+', first.text).group() != re.search(r'/static/app.js\?v=\d+', second.text).group()
