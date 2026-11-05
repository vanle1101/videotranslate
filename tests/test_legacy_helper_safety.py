"""Retired helper entry points cannot bypass the live application's gates."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPERS = ("complete_and_export", "finalize_all_segments", "synthesize_remaining", "run_export", "run_task_e2e", "test_seg_24", "test_restore")


@pytest.fixture
def protected_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    for name in ("inputs", "outputs", "projects", "cache", "temp"):
        (workspace / name).mkdir(parents=True)
    (tmp_path / ".env").write_bytes(b"preserve-user-settings\n")
    (workspace / "projects/douyin_7692745161054506290.json").write_bytes(b"preserve-checkpoint\n")
    (workspace / "outputs/douyin_translated_douyin_7692745161054506290_hq.mp4").write_bytes(b"preserve-prior-output")
    (workspace / "cache/seg_49.wav").write_bytes(b"preserve-tts-audio")
    # Loaded before an entry point. A regression must fail before it could
    # import configuration, restore a real session or issue an external request.
    (tmp_path / "sitecustomize.py").write_text('''
import importlib.abc
import socket
import subprocess
import sys
sys.dont_write_bytecode = True
class RejectProductionImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'config', 'core', 'main', 'edge_tts'}:
            raise AssertionError('Retired helper attempted production import: ' + fullname)
sys.meta_path.insert(0, RejectProductionImports())
def forbidden(*args, **kwargs):
    raise AssertionError('Retired helper attempted network/process execution')
socket.create_connection = forbidden
socket.socket.connect = forbidden
subprocess.Popen = forbidden
''', encoding="utf-8")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
               PYTHONPATH=str(tmp_path) + os.pathsep + str(ROOT), BASE_DIR=str(tmp_path),
               WORKSPACE_DIR=str(workspace), INPUT_DIR=str(workspace / "inputs"),
               OUTPUT_DIR=str(workspace / "outputs"), TEMP_DIR=str(workspace / "temp"))
    snapshot = {str(path.relative_to(tmp_path)): path.read_bytes()
                for path in tmp_path.rglob("*") if path.is_file()}
    return tmp_path, env, snapshot


def assert_workspace_preserved(root, snapshot):
    assert {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file()} == snapshot


@pytest.mark.parametrize("name", HELPERS)
@pytest.mark.parametrize("options", [[], ["--force", "--task-id", "douyin_7692745161054506290"]])
def test_retired_cli_fails_closed_without_mutating_checkpoint_or_output(name, options, protected_workspace):
    root, env, snapshot = protected_workspace
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / (name + ".py")), *options],
                            cwd=root, env=env, capture_output=True, text=True,
                            encoding="utf-8", timeout=15)
    assert result.returncode == 2
    assert "LEGACY_HELPER_DISABLED: " + name + ".py" in result.stderr
    assert "AI kiểm tra lại" in result.stderr and "Xuất video MP4" in result.stderr
    assert not result.stdout
    assert_workspace_preserved(root, snapshot)


@pytest.mark.parametrize("name", HELPERS)
def test_importing_retired_helper_is_inert(name, protected_workspace):
    root, env, snapshot = protected_workspace
    result = subprocess.run([sys.executable, "-c",
                             "import importlib; importlib.import_module('scripts." + name + "'); print('IMPORT_SAFE')"],
                            cwd=root, env=env, capture_output=True, text=True,
                            encoding="utf-8", timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "IMPORT_SAFE"
    assert not result.stderr
    assert_workspace_preserved(root, snapshot)
