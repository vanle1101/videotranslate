import json
import logging
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest

from core.ai_execution import AIExecutionLayer, AIExecutionPolicy
from core.engines.translation import opencode_client as oc


@pytest.fixture
def isolated_client(monkeypatch, tmp_path):
    temporary_directory = oc.tempfile.TemporaryDirectory
    directories = []

    def create_directory(*args, **kwargs):
        kwargs["dir"] = tmp_path
        directory = temporary_directory(*args, **kwargs)
        directories.append(directory)
        return directory

    monkeypatch.setattr(oc.tempfile, "TemporaryDirectory", create_directory)
    monkeypatch.setattr(oc, "find_opencode_executable", lambda: "offline-opencode.exe")
    monkeypatch.setattr(oc, "_managed_configuration_present", lambda: False)
    monkeypatch.setattr(oc, "_IS_WINDOWS", False)
    monkeypatch.setattr(oc.subprocess, "Popen", Mock(side_effect=AssertionError("Unexpected process")))
    monkeypatch.setattr(oc.subprocess, "run", Mock(side_effect=AssertionError("Unexpected process")))
    layer = AIExecutionLayer(AIExecutionPolicy(concurrency=20, queue_limit=24))
    client = oc.OpenCodeZenClient(api_key="fixture-key", execution_layer=layer)
    yield client, directories
    for directory in directories:
        temporary_directory.cleanup(directory)


@pytest.fixture
def windows_compiler(monkeypatch, tmp_path):
    windows = tmp_path / "Windows"
    compiler = windows / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    compiler.parent.mkdir(parents=True)
    compiler.touch()
    monkeypatch.setenv("WINDIR", str(windows))
    monkeypatch.setattr(oc.sys, "platform", "win32")
    return compiler


def test_twenty_parallel_requests_keep_shell_and_environment_request_local(
        isolated_client, windows_compiler, monkeypatch):
    client, directories = isolated_client
    barrier = threading.Barrier(20)
    starts = []
    lock = threading.Lock()

    def run(argv, **options):
        assert options["shell"] is False
        assert options["stdin"] == options["stdout"] == options["stderr"] == subprocess.DEVNULL
        if argv[0] == str(windows_compiler):
            root = Path(options["cwd"])
            assert argv[-2:] == [f"/out:{root / 'translation-shell.exe'}", str(root / "translation-shell.cs")]
            assert (root / "translation-shell.cs").read_text(encoding="utf-8") == (
                "internal static class TranslationShell { private static int Main() { return 1; } }")
            barrier.wait(timeout=10)
            (root / "translation-shell.exe").write_bytes(b"MZ")
            return subprocess.CompletedProcess(argv, 0)
        assert Path(argv[0]).name == "translation-shell.exe"
        assert Path(argv[0]).read_bytes() == b"MZ"
        return subprocess.CompletedProcess(argv, 1)

    def start(argv, **options):
        root = Path(options["cwd"]).parent
        env = options["env"]
        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        assert options["shell"] is False
        assert "--auto" not in argv
        assert config["shell"] == str(root / "translation-shell.exe")
        assert config["permission"] == {"*": "ask"}
        assert config["enabled_providers"] == ["opencode"]
        assert config["model"] == config["small_model"] == f"opencode/{client.model}"
        assert config["agent"]["plan"]["permission"] == {"*": "ask"}
        assert env["OPENCODE_TEST_HOME"] == str(root)
        assert env["XDG_DATA_HOME"] == str(root / "data")
        assert env["OPENCODE_CONFIG_DIR"] == str(root / "config" / "opencode")
        assert "fixture-key" not in env["OPENCODE_CONFIG_CONTENT"]
        with lock:
            starts.append(root)
        process = Mock(returncode=0)
        process.communicate.return_value = (json.dumps({"type": "text", "part": {"text": "translated"}}), "")
        return process

    monkeypatch.setattr(oc.subprocess, "run", run)
    monkeypatch.setattr(oc.subprocess, "Popen", start)
    with ThreadPoolExecutor(max_workers=20) as pool:
        answers = list(pool.map(lambda i: client.translate(f"request-{i}", use_cache=False), range(20)))
    assert answers == ["translated"] * 20
    assert len(directories) == len(set(starts)) == 20
    assert all(not root.exists() for root in starts)


@pytest.mark.parametrize("failure", ["compile_timeout", "compile_permission", "probe_permission", "probe_exit"])
def test_concurrent_shell_failure_never_substitutes_a_real_shell_or_launches_cli(
        isolated_client, windows_compiler, monkeypatch, caplog, failure):
    client, directories = isolated_client
    barrier = threading.Barrier(20)

    def run(argv, **options):
        assert options["shell"] is False
        if argv[0] == str(windows_compiler):
            barrier.wait(timeout=10)
            if failure == "compile_timeout":
                raise subprocess.TimeoutExpired("private-compiler", 15, output="private-output")
            if failure == "compile_permission":
                raise PermissionError("private-path")
            (Path(options["cwd"]) / "translation-shell.exe").write_bytes(b"MZ")
            return subprocess.CompletedProcess(argv, 0)
        assert Path(argv[0]).name == "translation-shell.exe"
        if failure == "probe_permission":
            raise PermissionError("private-path")
        return subprocess.CompletedProcess(argv, 0)

    def translate(index):
        with pytest.raises(oc.OpenCodeConfigurationError) as caught:
            client.translate(f"private-prompt-{index}", use_cache=False)
        assert "private-" not in str(caught.value)

    monkeypatch.setattr(oc.subprocess, "run", run)
    with ThreadPoolExecutor(max_workers=20) as pool:
        list(pool.map(translate, range(20)))
    oc.subprocess.Popen.assert_not_called()
    assert len(directories) == 20
    assert all(not Path(directory.name).exists() for directory in directories)
    assert "private-" not in caplog.text


@pytest.mark.parametrize("primary_type", [oc.OpenCodeTimeoutError, oc.OpenCodeCancelledError])
@pytest.mark.parametrize("cleanup_type", [PermissionError, NotADirectoryError])
def test_cleanup_failure_keeps_original_timeout_or_cancellation(
        isolated_client, monkeypatch, tmp_path, caplog, primary_type, cleanup_type):
    client, directories = isolated_client
    primary = primary_type("original failure")
    cleanup_calls = []
    terminated = []
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    marker = unrelated / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    process = Mock(returncode=0)
    process.communicate.side_effect = primary

    def fail_cleanup(directory):
        cleanup_calls.append(directory.name)
        assert terminated == [process]
        raise cleanup_type("private-path private-key")

    def environment(root, selected, max_tokens):
        directory = next(item for item in directories if item.name == str(root))
        directory.cleanup = lambda: fail_cleanup(directory)
        return {}

    monkeypatch.setattr(client, "_environment", environment)
    monkeypatch.setattr(oc.subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(oc, "_terminate_process_tree", terminated.append)
    with caplog.at_level(logging.INFO), pytest.raises(primary_type) as caught:
        client.translate("private-prompt", use_cache=False)
    assert caught.value is primary
    assert cleanup_calls == [directories[0].name]
    assert marker.read_text(encoding="utf-8") == "keep"
    assert Path(directories[0].name).is_dir()
    assert "private-" not in caplog.text
    assert "OPENCODE_CLEANUP_FAILED" in caplog.text
    if primary_type is oc.OpenCodeTimeoutError:
        assert caught.value.code == "provider_timeout" and caught.value.retryable
        assert "code=provider_timeout" in caplog.text
    else:
        assert "PROVIDER_CANCELLED" in caplog.text


def test_concurrent_cleanup_failures_keep_each_requests_original_error(isolated_client, monkeypatch):
    client, directories = isolated_client
    barrier = threading.Barrier(20)
    lock = threading.Lock()
    failures = {}

    def environment(root, selected, max_tokens):
        directory = next(item for item in directories if item.name == str(root))
        directory.cleanup = Mock(side_effect=PermissionError("private-path"))
        return {}

    def start(argv, **options):
        root = Path(options["cwd"]).parent
        with lock:
            index = len(failures)
            failure = (oc.OpenCodeTimeoutError("timeout") if index % 2
                       else oc.OpenCodeCancelledError("cancelled"))
            failures[root] = failure
        process = Mock(returncode=0)

        def communicate(request, timeout):
            barrier.wait(timeout=10)
            raise failure

        process.communicate.side_effect = communicate
        return process

    def translate(index):
        try:
            client.translate(f"request-{index}", use_cache=False)
        except (oc.OpenCodeTimeoutError, oc.OpenCodeCancelledError) as error:
            return error
        pytest.fail("Missing primary failure")

    monkeypatch.setattr(client, "_environment", environment)
    monkeypatch.setattr(oc.subprocess, "Popen", start)
    monkeypatch.setattr(oc, "_terminate_process_tree", Mock())
    with ThreadPoolExecutor(max_workers=20) as pool:
        errors = list(pool.map(translate, range(20)))
    assert len(directories) == len(failures) == 20
    assert {id(error) for error in errors} == {id(error) for error in failures.values()}
    for directory in directories:
        directory.cleanup.assert_called_once()
        assert Path(directory.name).is_dir()


def test_timeout_cleanup_failure_retains_retry_and_uses_new_directory(isolated_client, monkeypatch):
    client, directories = isolated_client
    client.max_retries = 1
    processes = []
    terminated = []

    def environment(root, selected, max_tokens):
        if len(directories) == 1:
            directories[0].cleanup = Mock(side_effect=NotADirectoryError("private-path"))
        return {}

    def start(argv, **options):
        process = Mock(returncode=0)
        if not processes:
            process.communicate.side_effect = subprocess.TimeoutExpired("offline-command", 1)
        else:
            assert terminated == [processes[0]]
            assert Path(directories[0].name).exists()
            assert directories[0].name != directories[1].name
            process.communicate.return_value = (json.dumps({"type": "text", "part": {"text": "translated"}}), "")
        processes.append(process)
        return process

    monkeypatch.setattr(client, "_environment", environment)
    monkeypatch.setattr(oc.subprocess, "Popen", start)
    monkeypatch.setattr(oc, "_terminate_process_tree", terminated.append)
    monkeypatch.setattr(oc, "_retry_delay", lambda seconds, context: None)
    assert client.translate("request", use_cache=False) == "translated"
    assert len(processes) == 2
    directories[0].cleanup.assert_called_once()
    assert not Path(directories[1].name).exists()


def test_cleanup_failure_without_primary_error_is_not_silenced(isolated_client, monkeypatch, caplog):
    client, directories = isolated_client
    process = Mock(returncode=0)
    process.communicate.return_value = (json.dumps({"type": "text", "part": {"text": "translated"}}), "")

    def environment(root, selected, max_tokens):
        directory = next(item for item in directories if item.name == str(root))
        directory.cleanup = Mock(side_effect=PermissionError("private-path"))
        return {}

    monkeypatch.setattr(client, "_environment", environment)
    monkeypatch.setattr(oc.subprocess, "Popen", Mock(return_value=process))
    try:
        raise ValueError("outer handled error")
    except ValueError:
        with pytest.raises(oc.OpenCodeRequestError):
            client.translate("private-prompt", use_cache=False)
    directories[0].cleanup.assert_called_once()
    assert "PROVIDER_COMPLETED" not in caplog.text
    assert "private-" not in caplog.text
