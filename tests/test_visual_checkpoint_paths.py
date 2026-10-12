"""Real filesystem coverage of checkpoint paths beyond Windows MAX_PATH."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from config import settings
from core.video_intelligence import VideoIntelligence, _checkpoint_fs_path


def test_long_checkpoint_roundtrip_and_atomic_replacement(tmp_path, monkeypatch):
    # Keep the source short; the two digest components push only the cache over
    # MAX_PATH, as happens with a nested user workspace or pytest directory.
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source identity; no provider or media operation needed")
    workspace = tmp_path / ("workspace_" + "x" * 90) / ("cache_root_" + "y" * 90)
    monkeypatch.setattr(settings, "WORKSPACE_DIR", workspace)
    intelligence = VideoIntelligence()
    intelligence._checkpoint_context = intelligence._checkpoint_identity(
        source, [SimpleNamespace(id=0, start=0., end=1., text_zh="你好")], 1.)
    context = intelligence._checkpoint_context
    assert context is not None
    stage = ["translation", 0, 1]
    path = context["directory"] / (intelligence._checkpoint_digest(stage) + ".json")
    assert len(str(path)) > 260

    first = {"text": "Xin chào.", "revision": 1}
    intelligence._write_checkpoint(stage, first)
    assert _checkpoint_fs_path(path).is_file()
    assert intelligence._read_checkpoint(stage) == first

    second = {"text": "Chào bạn.", "revision": 2}
    intelligence._write_checkpoint(stage, second)
    restored = VideoIntelligence()
    restored._checkpoint_context = context
    assert restored._read_checkpoint(stage) == second
    assert len(list(_checkpoint_fs_path(context["directory"]).glob("*.json"))) == 1
    assert not list(_checkpoint_fs_path(context["directory"]).glob("*.tmp"))

    def failed_replace(*_args):
        raise PermissionError("injected atomic replacement failure")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", failed_replace)
        intelligence._write_checkpoint(stage, {"text": "uncommitted", "revision": 3})
    assert restored._read_checkpoint(stage) == second
    assert not list(_checkpoint_fs_path(context["directory"]).glob("*.tmp"))

    # A path workaround must not bypass the existing content integrity gate.
    _checkpoint_fs_path(path).write_text('{"payload": "incomplete"}', encoding="utf-8")
    assert restored._read_checkpoint(stage) is None


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-path representation")
def test_windows_checkpoint_path_preserves_absolute_and_unc_locations(tmp_path):
    ordinary = tmp_path / "cache" / "part.json"
    extended = _checkpoint_fs_path(ordinary)
    assert str(extended) == "\\\\?\\" + str(ordinary)
    assert _checkpoint_fs_path(extended) == extended
    assert str(_checkpoint_fs_path(Path(r"\\server\share\cache\part.json"))) == (
        r"\\?\UNC\server\share\cache\part.json")


@pytest.mark.skipif(os.name == "nt", reason="Non-Windows path behavior")
def test_non_windows_checkpoint_path_is_unchanged():
    relative = Path("cache") / "part.json"
    assert _checkpoint_fs_path(relative) == relative
