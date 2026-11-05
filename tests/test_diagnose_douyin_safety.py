"""Full diagnostics never delete a prior benchmark or its resume checkpoint."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import diagnose_douyin as diagnostic


@pytest.fixture(autouse=True)
def offline_only(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("Preservation tests must not access network or run media tools")
    monkeypatch.setattr(diagnostic.resolver, "resolve_douyin", forbidden)
    monkeypatch.setattr(diagnostic.resolver, "open_public_media", forbidden)
    monkeypatch.setattr(diagnostic.subprocess, "run", forbidden)
    monkeypatch.setattr(diagnostic, "ROOT", tmp_path)


@pytest.mark.parametrize("prefix", ["video_baseline_live", "video_current_live"])
def test_existing_benchmark_files_are_preserved_before_worker_loading(tmp_path, monkeypatch, prefix):
    output = tmp_path / "owned-prior-run"
    output.mkdir()
    before = {}
    for suffix in (".mp4", ".mp4.part", ".resume.json", ".resume.json.tmp"):
        path = output / (prefix + suffix)
        path.write_bytes(("previous" + suffix).encode())
        before[path.name] = path.read_bytes()
    monkeypatch.setattr(diagnostic, "_load_snapshot", lambda *args: pytest.fail("Must refuse before loading a worker"))

    with pytest.raises(FileExistsError, match="existing videos and checkpoints were preserved"):
        diagnostic.run_full_baseline("https://example.test/video", {}, output, tmp_path / "snapshot.py", prefix)

    assert {path.name: path.read_bytes() for path in output.iterdir()} == before


def test_empty_existing_directory_is_not_claimed_as_owned(tmp_path):
    output = tmp_path / "another-process-directory"
    output.mkdir()
    with pytest.raises(FileExistsError, match="new, nonexistent"):
        diagnostic.run_full_baseline("https://example.test/video", {}, output, None, "video_current_live")
    assert list(output.iterdir()) == []


@pytest.mark.parametrize("mode", ["--full-baseline", "--full-current"])
def test_cli_preflight_refuses_existing_output_before_resolving_source(tmp_path, monkeypatch, mode):
    output = tmp_path / "prior-run"
    output.mkdir()
    prior = output / "video_current_live.mp4"
    prior.write_bytes(b"existing benchmark must remain")
    audit = tmp_path / "not-yet-created-audit"
    monkeypatch.setattr(diagnostic.sys, "argv", ["diagnose_douyin.py", mode,
                        "--output-dir", str(output), "--audit-dir", str(audit)])
    with pytest.raises(SystemExit) as error:
        diagnostic.main()
    assert error.value.code == 2
    assert prior.read_bytes() == b"existing benchmark must remain"
    assert not audit.exists()


@pytest.mark.parametrize("prefix", ["../outside", "subdir/video", ""])
def test_prefix_cannot_escape_new_benchmark_directory(tmp_path, prefix):
    output = tmp_path / "new-run"
    with pytest.raises(ValueError, match="plain file name"):
        diagnostic.run_full_baseline("https://example.test/video", {}, output, None, prefix)
    assert not output.exists()


def test_failed_offline_worker_retains_new_owned_workspace_for_diagnostics(tmp_path, monkeypatch):
    output = tmp_path / "new-run"
    calls = []
    def fail(info, prefix, send):
        assert output.is_dir() and list(output.iterdir()) == []
        assert Path(prefix).parent == output
        calls.append(prefix)
        # A synthetic worker failure exercises ownership without a transfer.
        raise RuntimeError("offline test failure")
    monkeypatch.setattr(diagnostic, "_load_snapshot", lambda path: SimpleNamespace(download_resolved_video=fail))
    info = {"formats": [{"urls": ["https://example.test/source"]}]}

    result = diagnostic.run_full_baseline("https://example.test/video", info, output,
                                          tmp_path / "snapshot.py", "video_baseline_live")

    assert calls == [str(output / "video_baseline_live")]
    assert result["error"]["type"] == "RuntimeError"
    assert result["media"]["exists"] is False
    assert list(output.iterdir()) == []
