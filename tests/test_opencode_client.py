"""Free-only OpenCode CLI adapter regression tests (never call the network)."""
import json
import asyncio
import logging
import subprocess
import asyncio
import contextvars
import logging
import re
import threading
import sys
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from core.engines.translation import opencode_client as oc
from config import settings
from core.runtime_context import execution_context, current_execution_context
from core.runtime_context import current_execution_context, execution_context, ExecutionContext
from core.ai_execution import AIExecutionLayer, AIExecutionPolicy


def test_shared_queue_diagnostics_never_resolve_filesystem_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(oc, "_execution_layers", {})
    def exhausted(*args, **kwargs):
        raise OSError(1450, "Insufficient system resources")
    monkeypatch.setattr(Path, "resolve", exhausted)
    first = oc._shared_execution_layer()
    assert oc._shared_execution_layer() is first
    assert first.snapshot()["active_requests"] == 0


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    layer = AIExecutionLayer(AIExecutionPolicy(retry_jitter=0), cache_root=tmp_path / "cache")
    monkeypatch.setattr(oc, "_shared_execution_layer", lambda: layer)
    monkeypatch.setattr(settings, "OPENCODE_API_KEY", "")
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "existing-data"))
    monkeypatch.setattr(oc, "find_opencode_executable", lambda: "opencode.exe")
    monkeypatch.setattr(oc, "_managed_configuration_present", lambda: False)
    monkeypatch.setattr(oc, "_blocked_shell", lambda root: str(root / "translation-shell.exe"))
    return tmp_path


def make_process(stdout=None, stderr="", returncode=0):
    process = Mock(returncode=returncode)
    process.communicate.return_value = (
        stdout if stdout is not None else json.dumps({"type": "text", "part": {"text": "Xin chào"}}),
        stderr,
    )
    return process


def test_credential_precedence_and_existing_auth_file(isolated, monkeypatch):
    auth = isolated / "existing-data" / "opencode" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text(json.dumps({"opencode": {"type": "api", "key": "auth-key"}}))
    assert oc.resolve_api_key() == "auth-key"
    monkeypatch.setenv("OPENCODE_API_KEY", "env-key")
    assert oc.resolve_api_key() == "env-key"
    monkeypatch.setattr(settings, "OPENCODE_API_KEY", "settings-key")
    assert oc.resolve_api_key() == "settings-key"
    assert oc.resolve_api_key("explicit-key") == "explicit-key"


@pytest.mark.parametrize("payload", [
    {"opencode": {"type": "oauth", "key": "not-an-api-key"}},
    {"other-provider": {"type": "api", "key": "wrong-provider"}},
    [], {"opencode": {"type": "api", "key": None}},
])
def test_does_not_borrow_other_credentials(isolated, payload):
    auth = isolated / "existing-data" / "opencode" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text(json.dumps(payload))
    assert oc.resolve_api_key() is None


def test_invalid_auth_file_is_not_printed_or_raised(isolated, capsys):
    auth = isolated / "existing-data" / "opencode" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text('{ private-key: malformed')
    assert oc.resolve_api_key() is None
    assert not capsys.readouterr().out


@pytest.mark.parametrize("model", ["gpt-5", "opencode/gpt-5", "big-pickle; rm file", "", "unknown-free"])
def test_paid_unknown_or_injected_model_never_starts_cli(isolated, monkeypatch, model):
    start = Mock()
    monkeypatch.setattr(oc.subprocess, "Popen", start)
    with pytest.raises(oc.OpenCodeModelError):
        oc.OpenCodeZenClient(api_key="fixture-key", model=model).translate("hello")
    start.assert_not_called()


def test_missing_login_and_missing_executable_are_actionable(isolated, monkeypatch):
    with pytest.raises(oc.OpenCodeConfigurationError, match="API key"):
        oc.OpenCodeZenClient().translate("hello")
    monkeypatch.setattr(oc, "find_opencode_executable", lambda: None)
    with pytest.raises(oc.OpenCodeConfigurationError, match="CLI"):
        oc.OpenCodeZenClient(api_key="fixture-key").translate("hello")


def test_translation_is_isolated_secret_safe_and_shell_free(isolated, monkeypatch):
    monkeypatch.setenv("OPENCODE_CONFIG", "user-config.json")
    monkeypatch.setenv("OPENCODE_PERMISSION", '{"*":"allow"}')
    monkeypatch.setenv("GEMINI_API_KEY", "unrelated-key")
    monkeypatch.setenv("HF_TOKEN", "unrelated-hf")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "unrelated-cloud")
    monkeypatch.setenv("NODE_OPTIONS", "--require unsafe.js")
    monkeypatch.setenv("BUN_OPTIONS", "--preload unsafe.js")
    process = make_process()
    start = Mock(return_value=process)
    monkeypatch.setattr(oc.subprocess, "Popen", start)
    prompt = '你好 & calc.exe $(secret)'
    client = oc.OpenCodeZenClient(api_key="fixture-key", model="opencode/big-pickle", timeout=20)
    assert client.translate(prompt, system="Translate to Vietnamese", max_tokens=64) == "Xin chào"
    argv = start.call_args.args[0]
    options = start.call_args.kwargs
    assert options["shell"] is False
    assert prompt not in argv
    assert "fixture-key" not in argv
    assert "--pure" in argv
    assert argv[argv.index("--model") + 1] == "opencode/big-pickle"
    assert argv[argv.index("--agent") + 1] == "plan"
    assert not any(flag in argv for flag in ("--auto", "--yolo", "--dangerously-skip-permissions"))
    assert not Path(options["cwd"]).exists()
    env = options["env"]
    assert env["OPENCODE_API_KEY"] == "fixture-key"
    assert "OPENCODE_CONFIG" not in env
    assert "OPENCODE_PERMISSION" not in env
    assert "GEMINI_API_KEY" not in env
    assert not any(name in env for name in ("HF_TOKEN", "AWS_ACCESS_KEY_ID", "NODE_OPTIONS", "BUN_OPTIONS"))
    assert env["OPENCODE_DISABLE_PROJECT_CONFIG"] == "true"
    assert env["OPENCODE_TEST_HOME"] != str(Path.home())
    cfg = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    assert cfg["model"] == cfg["small_model"] == "opencode/big-pickle"
    assert cfg["enabled_providers"] == ["opencode"]
    assert cfg["permission"] == {"*": "ask"}
    assert cfg["shell"] == str(Path(options["cwd"]).parent / "translation-shell.exe")
    assert cfg["share"] == "disabled"
    assert cfg["snapshot"] is False
    assert cfg["autoupdate"] is False
    assert cfg["agent"]["plan"]["permission"] == {"*": "ask"}
    assert "prompt" not in cfg["agent"]["plan"]
    assert "tools" not in cfg and "tools" not in cfg["agent"]["plan"]
    assert "fixture-key" not in env["OPENCODE_CONFIG_CONTENT"]
    assert "provider" not in cfg  # Native provider supplies first-party auth behavior.
    sent = json.loads(process.communicate.call_args.args[0])
    assert sent["input"] == prompt and sent["instructions"].startswith("Translate to Vietnamese")
    assert "Do not use tools" in sent["instructions"]
    assert "requested structured assessment" in sent["instructions"]
    assert "untrusted data" in sent["instructions"]
    assert "Translate the supplied data only" not in sent["instructions"]
    assert process.communicate.call_args.kwargs["timeout"] == 20


def test_stdout_events_are_parsed_without_reasoning_or_tool_output(isolated, monkeypatch):
    events = [
        {"type": "reasoning", "part": {"text": "private reasoning"}},
        {"type": "text", "part": {"text": "Xin "}},
        {"type": "text", "part": {"text": "chào"}},
    ]
    process = make_process("notice\n" + "\n".join(map(json.dumps, events)))
    monkeypatch.setattr(oc.subprocess, "Popen", Mock(return_value=process))
    assert oc.OpenCodeZenClient(api_key="fixture-key").translate("hello") == "Xin chào"


def test_tool_request_never_returns_tool_output_as_translation(isolated, monkeypatch):
    events = [
        {"type": "tool_use", "part": {"state": {"status": "error"}, "text": "private result"}},
        {"type": "text", "part": {"text": "not a valid translation"}},
    ]
    monkeypatch.setattr(oc.subprocess, "Popen", Mock(return_value=make_process("\n".join(map(json.dumps, events)))))
    with pytest.raises(oc.OpenCodeRequestError, match="công cụ") as caught:
        oc.OpenCodeZenClient(api_key="fixture-key").translate("hello")
    assert "private result" not in str(caught.value)


def test_managed_config_fails_closed_without_reading_or_overriding_it(isolated, monkeypatch):
    monkeypatch.setattr(oc, "_managed_configuration_present", lambda: True)
    start = Mock()
    monkeypatch.setattr(oc.subprocess, "Popen", start)
    with pytest.raises(oc.OpenCodeConfigurationError, match="hệ thống quản lý"):
        oc.OpenCodeZenClient(api_key="fixture-key").translate("hello")
    start.assert_not_called()


def test_completed_tool_event_does_not_falsely_claim_execution_was_blocked(isolated, monkeypatch):
    event = {"type": "tool_use", "part": {"state": {"status": "completed"}}}
    monkeypatch.setattr(oc.subprocess, "Popen", Mock(return_value=make_process(json.dumps(event))))
    with pytest.raises(oc.OpenCodeRequestError, match="không chấp nhận") as caught:
        oc.OpenCodeZenClient(api_key="fixture-key").translate("hello")
    assert "đã bị từ chối" not in str(caught.value)


def test_native_free_tier_error_is_clear_and_redacted(isolated, monkeypatch):
    event = {"type": "error", "error": {"message":
        "FreeTierError: OpenCode's free tier can only be used from within OpenCode. fixture-key private-prompt"}}
    process = make_process(json.dumps(event), returncode=1)
    monkeypatch.setattr(oc.subprocess, "Popen", Mock(return_value=process))
    with pytest.raises(oc.OpenCodeRequestError, match="FreeTierError") as caught:
        oc.OpenCodeZenClient(api_key="fixture-key").translate("private-prompt")
    assert "fixture-key" not in str(caught.value)
    assert "private-prompt" not in str(caught.value)


def test_timeout_terminates_request_process_and_cleans_temporary_files(isolated, monkeypatch):
    process = make_process()
    process.communicate.side_effect = subprocess.TimeoutExpired("private-command", 1)
    start = Mock(return_value=process)
    terminate = Mock()
    monkeypatch.setattr(oc.subprocess, "Popen", start)
    monkeypatch.setattr(oc, "_terminate_process_tree", terminate)
    with pytest.raises(oc.OpenCodeRequestError, match="quá lâu"):
        oc.OpenCodeZenClient(api_key="fixture-key", timeout=1).translate("hello")
    terminate.assert_called_once_with(process)
    assert not Path(start.call_args.kwargs["cwd"]).exists()


@pytest.mark.parametrize("windows_pipe", [False, True])
def test_timeout_retries_after_owned_process_cleanup_and_logs_each_attempt(
        isolated, monkeypatch, caplog, windows_pipe):
    monkeypatch.setattr(oc, "_IS_WINDOWS", windows_pipe)
    failed, completed = make_process(), make_process()
    failed.communicate.side_effect = subprocess.TimeoutExpired("private-command", 1, output="private-output")
    terminated = []
    starts = []

    def start(*args, **kwargs):
        starts.append(kwargs)
        if len(starts) == 1:
            return failed
        assert terminated == [failed]
        assert not any(thread.name == f"opencode-io-{failed.pid}" for thread in threading.enumerate())
        return completed

    monkeypatch.setattr(oc.subprocess, "Popen", start)
    monkeypatch.setattr(oc, "_terminate_process_tree", terminated.append)
    monkeypatch.setattr(oc.time, "sleep", lambda seconds: None)
    with caplog.at_level(logging.INFO, logger="ai"), execution_context("timeout-retry"):
        assert oc.OpenCodeZenClient(api_key="private-key", timeout=1, max_retries=1).translate("private-prompt") == "Xin chào"
    assert len(starts) == 2
    assert all(not Path(options["cwd"]).exists() for options in starts)
    lines = [record.getMessage() for record in caplog.records if record.name == "ai"]
    assert [line.split()[0] for line in lines] == ["PROVIDER_REQUEST", "PROVIDER_FAILED", "PROVIDER_REQUEST", "PROVIDER_COMPLETED"]
    assert [re.search(r"attempt=(\d)", line).group(1) for line in lines] == ["1", "1", "2", "2"]
    assert len({re.search(r"request_id=([a-f0-9]{32})", line).group(1) for line in lines}) == 1
    assert "code=provider_timeout" in lines[1]
    assert "private-" not in caplog.text


@pytest.mark.parametrize("max_retries", [0, 1, 2])
def test_timeout_exhaustion_is_typed_bounded_and_cleans_every_attempt(isolated, monkeypatch, caplog, max_retries):
    monkeypatch.setattr(oc, "_IS_WINDOWS", False)
    processes = [make_process() for _ in range(max_retries + 1)]
    for process in processes:
        process.communicate.side_effect = subprocess.TimeoutExpired("private-command", 1, stderr="private-key")
    start, terminate = Mock(side_effect=processes), Mock()
    monkeypatch.setattr(oc.subprocess, "Popen", start)
    monkeypatch.setattr(oc, "_terminate_process_tree", terminate)
    delays = []
    monkeypatch.setattr(oc.time, "sleep", delays.append)
    with caplog.at_level(logging.INFO, logger="ai"), execution_context("timeout-exhausted"):
        with pytest.raises(oc.OpenCodeTimeoutError, match="quá lâu") as caught:
            oc.OpenCodeZenClient(api_key="private-key", timeout=1, max_retries=max_retries).translate("private-prompt")
    assert isinstance(caught.value, oc.OpenCodeRequestError)
    assert start.call_count == max_retries + 1
    assert [call.args[0] for call in terminate.call_args_list] == processes
    assert delays == [1, 2][:max_retries]
    assert all(not Path(call.kwargs["cwd"]).exists() for call in start.call_args_list)
    lines = [record.getMessage() for record in caplog.records if record.name == "ai"]
    assert [line.split()[0] for line in lines] == ["PROVIDER_REQUEST", "PROVIDER_FAILED"] * (max_retries + 1)
    assert "PROVIDER_COMPLETED" not in caplog.text and "private-" not in caplog.text
    assert all("code=provider_timeout" in line for line in lines if line.startswith("PROVIDER_FAILED"))
    assert "private-" not in str(caught.value)


def test_cancellation_during_timeout_backoff_never_launches_another_cli(isolated, monkeypatch, caplog):
    monkeypatch.setattr(oc, "_IS_WINDOWS", False)
    clock = [0.0]
    cancelled = threading.Event()
    process = make_process()

    def wait(request, timeout):
        clock[0] += timeout
        raise subprocess.TimeoutExpired("private-command", timeout)

    process.communicate.side_effect = wait
    start, terminate = Mock(return_value=process), Mock()
    monkeypatch.setattr(oc.subprocess, "Popen", start)
    monkeypatch.setattr(oc, "_terminate_process_tree", terminate)
    monkeypatch.setattr(oc.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(oc.time, "sleep", lambda seconds: cancelled.set())
    with caplog.at_level(logging.INFO, logger="ai"), execution_context("timeout-stop", cancelled.is_set):
        with pytest.raises(oc.OpenCodeCancelledError):
            oc.OpenCodeZenClient(api_key="private-key", timeout=1, max_retries=2).translate("private-prompt")
    assert clock[0] == 1
    start.assert_called_once()
    terminate.assert_called_once_with(process)
    assert not Path(start.call_args.kwargs["cwd"]).exists()
    assert "PROVIDER_CANCELLED" in caplog.text and "PROVIDER_COMPLETED" not in caplog.text
    assert "private-" not in caplog.text


@pytest.mark.parametrize("returncode", [0, 1])
def test_tool_attempt_is_never_retried_even_after_cli_failure(isolated, monkeypatch, returncode):
    process = make_process(json.dumps({"type": "tool_use", "part": {"state": {"status": "error"}}}), returncode=returncode)
    start = Mock(return_value=process)
    monkeypatch.setattr(oc.subprocess, "Popen", start)
    with pytest.raises(oc.OpenCodeRequestError, match="công cụ"):
        oc.OpenCodeZenClient(api_key="fixture-key", max_retries=2).translate("hello")
    start.assert_called_once()
    assert not Path(start.call_args.kwargs["cwd"]).exists()


def test_cli_configuration_failure_is_not_eligible_for_timeout_retry(isolated, monkeypatch):
    start = Mock(side_effect=OSError("private-path"))
    monkeypatch.setattr(oc.subprocess, "Popen", start)
    with pytest.raises(oc.OpenCodeConfigurationError):
        oc.OpenCodeZenClient(api_key="fixture-key", max_retries=2).translate("hello")
    start.assert_called_once()
    assert not Path(start.call_args.kwargs["cwd"]).exists()


def test_real_timeout_retry_reaps_first_child_before_starting_successful_child(isolated, monkeypatch):
    real_popen = subprocess.Popen
    children = []
    roots = []

    def start(argv, **kwargs):
        roots.append(Path(kwargs["cwd"]))
        if children:
            assert children[0].poll() is not None
            assert all(stream.closed for stream in (children[0].stdin, children[0].stdout, children[0].stderr))
            assert not any(thread.name == f"opencode-io-{children[0].pid}" for thread in threading.enumerate())
            child = "import sys; sys.stdin.read(); print('{\"type\":\"text\",\"part\":{\"text\":\"verified\"}}')"
        else:
            child = "import time; time.sleep(30)"
        process = real_popen([sys.executable, "-u", "-c", child], **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(oc.subprocess, "Popen", start)
    monkeypatch.setattr(oc, "_retry_delay", lambda seconds, context: oc._check_cancelled(context))
    try:
        assert oc.OpenCodeZenClient(api_key="fixture-key", timeout=1, max_retries=1).translate("hello") == "verified"
        assert len(children) == 2
        assert all(child.poll() is not None for child in children)
        assert all(not root.exists() for root in roots)
    finally:
        for child in children:
            oc._terminate_process_tree(child)


def test_errors_do_not_include_raw_cli_outputs(isolated, monkeypatch):
    process = make_process("", "token fixture-key private-prompt", returncode=1)
    monkeypatch.setattr(oc.subprocess, "Popen", Mock(return_value=process))
    with pytest.raises(oc.OpenCodeRequestError) as caught:
        oc.OpenCodeZenClient(api_key="fixture-key").translate("private-prompt")
    assert "fixture-key" not in str(caught.value)
    assert "private-prompt" not in str(caught.value)
    assert "chưa xác định được nguyên nhân" in str(caught.value)
    assert "kiểm tra kết nối" not in str(caught.value).casefold()


def test_free_list_excludes_paid_models_and_non_chat_classifier():
    assert "big-pickle" in oc.OpenCodeZenClient.free_models()
    assert "muse-spark-1.3-contributor-free" in oc.OpenCodeZenClient.free_models()
    assert "muse-spark-1.3" not in oc.OpenCodeZenClient.free_models()
    assert "gpt-5" not in oc.OpenCodeZenClient.free_models()
    assert "jev-1.13-free" not in oc.OpenCodeZenClient.free_models()
    assert set(oc.OpenCodeZenClient.free_models()) == oc.FREE_CHAT_MODELS


@pytest.fixture
def windows_shell(monkeypatch, tmp_path):
    windows = tmp_path / "Windows"
    compiler = windows / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    compiler.parent.mkdir(parents=True)
    compiler.touch()
    root = tmp_path / "request"
    root.mkdir()
    monkeypatch.setattr(oc.sys, "platform", "win32")
    monkeypatch.setenv("WINDIR", str(windows))
    return root, compiler


def test_windows_shell_compiles_only_noop_in_request_and_probes_it(windows_shell, monkeypatch):
    root, compiler = windows_shell
    executable = root / "translation-shell.exe"

    def run(argv, **options):
        assert options["shell"] is False
        assert options["stdin"] == options["stdout"] == options["stderr"] == subprocess.DEVNULL
        assert options["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if argv[0] == str(compiler):
            assert argv == [str(compiler), "/nologo", "/noconfig", "/target:exe",
                            f"/out:{executable}", str(root / "translation-shell.cs")]
            assert options["timeout"] == 15
            assert options["cwd"] == str(root)
            assert (root / "translation-shell.cs").read_text() == (
                "internal static class TranslationShell { private static int Main() { return 1; } }")
            executable.write_bytes(b"MZ")
            return subprocess.CompletedProcess(argv, 0)
        assert argv == [str(executable)]
        assert options["timeout"] == 5
        return subprocess.CompletedProcess(argv, 1)

    execute = Mock(side_effect=run)
    monkeypatch.setattr(oc.subprocess, "run", execute)
    assert oc._blocked_shell(root) == str(executable)
    assert execute.call_count == 2


def test_windows_shell_uses_framework_fallback(windows_shell, monkeypatch):
    root, compiler = windows_shell
    compiler.unlink()
    fallback = compiler.parents[2] / "Framework" / "v4.0.30319" / "csc.exe"
    fallback.parent.mkdir(parents=True)
    fallback.touch()

    def run(argv, **options):
        if argv[0] == str(fallback):
            (root / "translation-shell.exe").write_bytes(b"MZ")
            return subprocess.CompletedProcess(argv, 0)
        return subprocess.CompletedProcess(argv, 1)

    execute = Mock(side_effect=run)
    monkeypatch.setattr(oc.subprocess, "run", execute)
    assert oc._blocked_shell(root) == str(root / "translation-shell.exe")
    assert execute.call_args_list[0].args[0][0] == str(fallback)


def test_windows_shell_missing_compiler_fails_before_starting_process(windows_shell, monkeypatch):
    root, compiler = windows_shell
    compiler.unlink()
    execute = Mock()
    monkeypatch.setattr(oc.subprocess, "run", execute)
    with pytest.raises(oc.OpenCodeConfigurationError, match="bộ chặn lệnh"):
        oc._blocked_shell(root)
    execute.assert_not_called()


@pytest.mark.parametrize("failure", ["compile_exit", "compile_timeout", "missing_exe", "probe_exit", "probe_oserror"])
def test_windows_shell_build_or_probe_failure_is_closed_and_sanitized(windows_shell, monkeypatch, failure):
    root, compiler = windows_shell

    def run(argv, **options):
        if argv[0] == str(compiler):
            if failure == "compile_timeout":
                raise subprocess.TimeoutExpired("private-compiler", 15, output="private-output")
            if failure != "missing_exe":
                (root / "translation-shell.exe").write_bytes(b"MZ")
            return subprocess.CompletedProcess(argv, 7 if failure == "compile_exit" else 0)
        if failure == "probe_oserror":
            raise OSError("private-path")
        return subprocess.CompletedProcess(argv, 0)

    execute = Mock(side_effect=run)
    monkeypatch.setattr(oc.subprocess, "run", execute)
    with pytest.raises(oc.OpenCodeConfigurationError, match="bộ chặn lệnh") as caught:
        oc._blocked_shell(root)
    assert "private" not in str(caught.value)
    assert execute.call_count == (2 if failure.startswith("probe") else 1)


@pytest.mark.parametrize("candidate", ["/usr/bin/false", "/bin/false", None])
def test_nonwindows_shell_uses_only_existing_false_binary(tmp_path, monkeypatch, candidate):
    monkeypatch.setattr(oc.sys, "platform", "linux")
    monkeypatch.setattr(Path, "is_file", lambda path: str(path).replace("\\", "/") == candidate)
    execute = Mock(return_value=subprocess.CompletedProcess([], 1))
    monkeypatch.setattr(oc.subprocess, "run", execute)
    if candidate is None:
        with pytest.raises(oc.OpenCodeConfigurationError, match="bộ chặn lệnh"):
            oc._blocked_shell(tmp_path)
        execute.assert_not_called()
    else:
        assert oc._blocked_shell(tmp_path).replace("\\", "/") == candidate
        assert execute.call_args.args[0] == [str(Path(candidate))]
        assert execute.call_args.kwargs["shell"] is False
        assert execute.call_args.kwargs["timeout"] == 5


def test_guard_failure_prevents_cli_launch_and_cleans_request(isolated, monkeypatch):
    seen = []

    def fail(root):
        seen.append(root)
        (root / "translation-shell.cs").write_text("temporary")
        raise oc.OpenCodeConfigurationError("bộ chặn lệnh")

    start = Mock()
    monkeypatch.setattr(oc, "_blocked_shell", fail)
    monkeypatch.setattr(oc.subprocess, "Popen", start)
    with pytest.raises(oc.OpenCodeConfigurationError, match="bộ chặn lệnh"):
        oc.OpenCodeZenClient(api_key="fixture-key").translate("hello")
    start.assert_not_called()
    assert len(seen) == 1 and not seen[0].exists()


def test_execution_context_propagates_to_thread_and_resets_without_raw_identifier():
    async def read_in_thread():
        return await asyncio.to_thread(current_execution_context)

    with execution_context("run_42", lambda: True):
        context = asyncio.run(read_in_thread())
        assert context.run_id == "run_42" and context.cancel_check()
        with execution_context("https://private.test/?token=secret"):
            assert current_execution_context().run_id == "unscoped"
        assert current_execution_context().run_id == "run_42"
    assert current_execution_context().run_id == "unscoped"
    assert current_execution_context().cancel_check is None


def test_provider_lifecycle_logs_correlate_safe_ids_without_content(isolated, monkeypatch, caplog):
    secret = "private-prompt-key-stdout"
    process = make_process(json.dumps({"type": "text", "part": {"text": secret}}))
    monkeypatch.setattr(oc.subprocess, "Popen", Mock(return_value=process))
    caplog.set_level(logging.INFO, logger="ai")
    with execution_context("run_42"):
        assert oc.OpenCodeZenClient(api_key=secret).translate(secret, system=secret) == secret
    records = [record.getMessage() for record in caplog.records if record.name == "ai"]
    assert len(records) == 2
    assert records[0].startswith("PROVIDER_REQUEST ")
    assert records[1].startswith("PROVIDER_COMPLETED ")
    assert all("run_id=run_42" in record and "attempt=1" in record for record in records)
    request_ids = [record.split("request_id=")[1].split()[0] for record in records]
    assert len(set(request_ids)) == 1 and len(request_ids[0]) == 32
    assert f"output_chars={len(secret)}" in records[1]
    assert secret not in caplog.text


def test_cancellation_polls_and_terminates_only_owned_request_without_retry(isolated, monkeypatch, caplog):
    state = {"cancelled": False}
    process = make_process()
    def communicate(request, timeout):
        assert timeout <= .25
        state["cancelled"] = True
        raise subprocess.TimeoutExpired("private-command", timeout, output="private-stdout")
    process.communicate.side_effect = communicate
    start = Mock(return_value=process)
    terminate = Mock()
    monkeypatch.setattr(oc.subprocess, "Popen", start)
    monkeypatch.setattr(oc, "_terminate_process_tree", terminate)
    caplog.set_level(logging.INFO, logger="ai")
    with execution_context("run_cancel", lambda: state["cancelled"]):
        with pytest.raises(oc.OpenCodeCancelledError, match="Đã hủy") as caught:
            oc.OpenCodeZenClient(api_key="private-key", timeout=120, max_retries=2).translate("private-prompt")
    terminate.assert_called_once_with(process)
    start.assert_called_once()
    assert "PROVIDER_CANCELLED run_id=run_cancel" in caplog.text
    assert "PROVIDER_COMPLETED" not in caplog.text and "private-" not in caplog.text
    assert "private-" not in str(caught.value)
    assert not Path(start.call_args.kwargs["cwd"]).exists()


def test_already_cancelled_task_never_launches_provider(isolated, monkeypatch, caplog):
    start = Mock()
    monkeypatch.setattr(oc.subprocess, "Popen", start)
    caplog.set_level(logging.INFO, logger="ai")
    with execution_context("run_cancel", lambda: True):
        with pytest.raises(oc.OpenCodeCancelledError):
            oc.OpenCodeZenClient(api_key="test").translate("hello")
    start.assert_not_called()
    assert "PROVIDER_CANCELLED" in caplog.text


def test_polling_submits_stdin_once_and_returns_whole_response(isolated, monkeypatch):
    process = make_process()
    answer = process.communicate.return_value
    process.communicate.side_effect = [subprocess.TimeoutExpired("opencode", .25),
                                       subprocess.TimeoutExpired("opencode", .25), answer]
    monkeypatch.setattr(oc.subprocess, "Popen", Mock(return_value=process))
    with execution_context("run_wait", lambda: False):
        assert oc.OpenCodeZenClient(api_key="test", timeout=30).translate("hello") == "Xin chào"
    calls = process.communicate.call_args_list
    assert json.loads(calls[0].args[0])["input"] == "hello"
    assert calls[1].args == calls[2].args == (None,)
    assert all(0 < call.kwargs["timeout"] <= .25 for call in calls)


def test_poll_timeouts_preserve_one_total_deadline_and_safe_failure_log(isolated, monkeypatch, caplog):
    now = [0.0]
    monkeypatch.setattr(oc.time, "monotonic", lambda: now[0])
    process = make_process()
    def communicate(request, timeout):
        now[0] += timeout
        raise subprocess.TimeoutExpired("private-command", timeout, output="private-token")
    process.communicate.side_effect = communicate
    start = Mock(return_value=process)
    terminate = Mock()
    monkeypatch.setattr(oc.subprocess, "Popen", start)
    monkeypatch.setattr(oc, "_terminate_process_tree", terminate)
    caplog.set_level(logging.INFO, logger="ai")
    with execution_context("run_timeout", lambda: False):
        with pytest.raises(oc.OpenCodeRequestError, match="quá lâu"):
            oc.OpenCodeZenClient(api_key="test", timeout=1).translate("private-prompt")
    assert now[0] == 1.0 and process.communicate.call_count == 4
    terminate.assert_called_once_with(process)
    assert "PROVIDER_FAILED run_id=run_timeout" in caplog.text
    assert "private-" not in caplog.text


def test_retry_lifecycle_records_separate_attempts(isolated, monkeypatch, caplog):
    processes = [make_process("private-output", "private-key", 1), make_process()]
    monkeypatch.setattr(oc.subprocess, "Popen", Mock(side_effect=processes))
    monkeypatch.setattr(oc.time, "sleep", lambda duration: None)
    caplog.set_level(logging.INFO, logger="ai")
    with execution_context("run_retry"):
        assert oc.OpenCodeZenClient(api_key="test", max_retries=1).translate("hello") == "Xin chào"
    records = [record.getMessage() for record in caplog.records if record.name == "ai"]
    assert [record.split()[0] for record in records] == ["PROVIDER_REQUEST", "PROVIDER_FAILED", "PROVIDER_REQUEST", "PROVIDER_COMPLETED"]
    assert "attempt=1" in records[1] and "attempt=2" in records[2] and "attempt=2" in records[3]
    assert "private-" not in caplog.text


def test_provider_logs_correlate_request_without_prompt_key_or_output(isolated, monkeypatch, caplog):
    process = make_process(json.dumps({"type": "text", "part": {"text": "private-output"}}))
    monkeypatch.setattr(oc.subprocess, "Popen", Mock(return_value=process))
    with caplog.at_level(logging.INFO, logger="ai"), execution_context("run-42"):
        assert oc.OpenCodeZenClient(api_key="private-key").translate("private-prompt") == "private-output"
    lines = [record.getMessage() for record in caplog.records if "PROVIDER_" in record.getMessage()]
    assert len(lines) == 2 and lines[0].startswith("PROVIDER_REQUEST") and lines[1].startswith("PROVIDER_COMPLETED")
    assert all("run_id=run-42" in line and "attempt=1" in line for line in lines)
    assert len({re.search(r"request_id=([a-f0-9]{32})", line).group(1) for line in lines}) == 1
    assert "output_chars=14" in lines[-1]
    assert not any(secret in caplog.text for secret in ("private-key", "private-prompt", "private-output"))
    assert current_execution_context().run_id == "unscoped"


def test_retry_logs_each_attempt_with_same_request_id(isolated, monkeypatch, caplog):
    failed, completed = make_process(returncode=1), make_process()
    monkeypatch.setattr(oc.subprocess, "Popen", Mock(side_effect=[failed, completed]))
    monkeypatch.setattr(oc.time, "sleep", lambda seconds: None)
    with caplog.at_level(logging.INFO, logger="ai"), execution_context("run-retry"):
        assert oc.OpenCodeZenClient(api_key="fixture-key", max_retries=1).translate("hello") == "Xin chào"
    lines = [record.getMessage() for record in caplog.records if "PROVIDER_" in record.getMessage()]
    assert [line.split()[0] for line in lines] == ["PROVIDER_REQUEST", "PROVIDER_FAILED", "PROVIDER_REQUEST", "PROVIDER_COMPLETED"]
    assert [re.search(r"attempt=(\d)", line).group(1) for line in lines] == ["1", "1", "2", "2"]
    assert len({re.search(r"request_id=([a-f0-9]{32})", line).group(1) for line in lines}) == 1


def lifecycle_fields(caplog):
    return [dict(event=record.getMessage().split()[0],
                 **dict(part.split("=", 1) for part in record.getMessage().split()[1:]))
            for record in caplog.records if record.name == "ai"]


def test_provider_attempt_timing_excludes_admission_wait(isolated, monkeypatch, caplog):
    clock = [0.0]
    monkeypatch.setattr(oc.time, "monotonic", lambda: clock[0])
    client = oc.OpenCodeZenClient(api_key="private-key")
    admit = client.execution_layer._admit
    def queued(cancel_check, key):
        admit(cancel_check, key)
        clock[0] += 102.328
    def provider(*args):
        clock[0] += 69.750
        return "private-output"
    monkeypatch.setattr(client.execution_layer, "_admit", queued)
    monkeypatch.setattr(client, "_translate", provider)
    with caplog.at_level(logging.INFO, logger="ai"):
        assert client.translate("private-prompt") == "private-output"
    submitted, completed = lifecycle_fields(caplog)
    assert submitted["event"] == "PROVIDER_REQUEST" and submitted["elapsed_ms"] == "0"
    assert submitted["admission_wait_ms"] == submitted["total_elapsed_ms"] == "102328"
    assert completed["event"] == "PROVIDER_COMPLETED" and completed["elapsed_ms"] == "69750"
    assert completed["total_elapsed_ms"] == "172078" and completed["admission_wait_ms"] == "102328"
    assert completed["backoff_elapsed_ms"] == "0"
    assert "private-" not in caplog.text


def test_failed_attempt_logged_before_backoff_and_retry_starts_after_wait(isolated, monkeypatch, caplog):
    clock = [0.0]
    monkeypatch.setattr(oc.time, "monotonic", lambda: clock[0])
    client = oc.OpenCodeZenClient(api_key="private-key", max_retries=1)
    calls = []
    def provider(*args):
        calls.append(clock[0])
        clock[0] += 3.0 if len(calls) == 1 else 2.0
        if len(calls) == 1:
            raise oc.OpenCodeRequestError("private-failure", retryable=True, code="provider_server")
        return "private-output"
    def backoff(seconds, context):
        records = lifecycle_fields(caplog)
        assert [record["event"] for record in records] == ["PROVIDER_REQUEST", "PROVIDER_FAILED"]
        assert records[-1]["elapsed_ms"] == records[-1]["total_elapsed_ms"] == "3000"
        assert records[-1]["backoff_elapsed_ms"] == "0"
        clock[0] += 1.25
    monkeypatch.setattr(client, "_translate", provider)
    monkeypatch.setattr(oc, "_retry_delay", backoff)
    with caplog.at_level(logging.INFO, logger="ai"):
        assert client.translate("private-prompt") == "private-output"
    records = lifecycle_fields(caplog)
    assert [record["event"] for record in records] == [
        "PROVIDER_REQUEST", "PROVIDER_FAILED", "PROVIDER_REQUEST", "PROVIDER_COMPLETED"]
    assert records[2]["attempt"] == "2" and records[2]["elapsed_ms"] == "0"
    assert records[2]["total_elapsed_ms"] == "4250"
    assert records[3]["elapsed_ms"] == "2000" and records[3]["total_elapsed_ms"] == "6250"
    assert records[3]["backoff_elapsed_ms"] == "1250"
    assert "private-" not in caplog.text


def test_cancelled_backoff_never_announces_unstarted_retry(isolated, monkeypatch, caplog):
    clock, stopped = [0.0], [False]
    monkeypatch.setattr(oc.time, "monotonic", lambda: clock[0])
    client = oc.OpenCodeZenClient(api_key="private-key", max_retries=1)
    provider = Mock(side_effect=oc.OpenCodeRequestError("private-failure", retryable=True,
                                                       code="provider_server"))
    monkeypatch.setattr(client, "_translate", provider)
    def backoff(seconds, context):
        clock[0] += .4
        stopped[0] = True
        oc._check_cancelled(context)
    monkeypatch.setattr(oc, "_retry_delay", backoff)
    with caplog.at_level(logging.INFO, logger="ai"), execution_context("backoff-stop", lambda: stopped[0]):
        with pytest.raises(oc.OpenCodeCancelledError):
            client.translate("private-prompt")
    records = lifecycle_fields(caplog)
    assert [record["event"] for record in records] == [
        "PROVIDER_REQUEST", "PROVIDER_FAILED", "PROVIDER_CANCELLED"]
    assert all(record["attempt"] == "1" and record["elapsed_ms"] == "0" for record in records)
    assert records[-1]["backoff_elapsed_ms"] == records[-1]["total_elapsed_ms"] == "400"
    provider.assert_called_once()
    assert "private-" not in caplog.text


@pytest.mark.parametrize("queued", [False, True])
def test_cached_result_never_logs_submitted_provider_attempt(isolated, monkeypatch, caplog, queued):
    clock = [0.0]
    monkeypatch.setattr(oc.time, "monotonic", lambda: clock[0])
    client = oc.OpenCodeZenClient(api_key="private-key")
    monkeypatch.setattr(client, "_translate", Mock(side_effect=AssertionError("must not submit")))
    monkeypatch.setattr(client.execution_layer.cache, "get", Mock(
        side_effect=[None, '{"text":"valid"}'] if queued else ['{"text":"valid"}']))
    admit = client.execution_layer._admit
    def admission(cancel_check, key):
        admit(cancel_check, key)
        clock[0] += 5.0
    monkeypatch.setattr(client.execution_layer, "_admit", admission)
    with caplog.at_level(logging.INFO, logger="ai"):
        assert client.translate("private-prompt", response_validator=json.loads,
                                schema_id="text-v1") == '{"text":"valid"}'
    records = lifecycle_fields(caplog)
    assert len(records) == 1 and records[0]["event"] == "AI_CACHE_HIT"
    assert records[0]["attempt"] == records[0]["elapsed_ms"] == "0"
    assert records[0]["admission_wait_ms"] == records[0]["total_elapsed_ms"] == ("5000" if queued else "0")
    assert "PROVIDER_REQUEST" not in caplog.text


@pytest.mark.parametrize("failure", [oc.AIQueueTimeoutError("private-queue"),
                                      oc.AICircuitOpenError(5)])
def test_admission_failure_reports_wait_without_a_provider_attempt(isolated, monkeypatch, caplog, failure):
    clock = [0.0]
    monkeypatch.setattr(oc.time, "monotonic", lambda: clock[0])
    client = oc.OpenCodeZenClient(api_key="private-key")
    provider = Mock(side_effect=AssertionError("must not submit"))
    monkeypatch.setattr(client, "_translate", provider)
    def rejected(*args):
        clock[0] += 30.0
        raise failure
    monkeypatch.setattr(client.execution_layer, "_admit", rejected)
    with caplog.at_level(logging.INFO, logger="ai"), pytest.raises(oc.OpenCodeRequestError):
        client.translate("private-prompt")
    record, = lifecycle_fields(caplog)
    assert record["event"] == "AI_ADMISSION_FAILED"
    assert record["attempt"] == record["elapsed_ms"] == "0"
    assert record["admission_wait_ms"] == record["total_elapsed_ms"] == "30000"
    provider.assert_not_called()
    assert "PROVIDER_REQUEST" not in caplog.text and "private-" not in caplog.text


def test_cancel_before_start_logs_cancellation_without_launching(isolated, monkeypatch, caplog):
    start = Mock()
    monkeypatch.setattr(oc.subprocess, "Popen", start)
    with caplog.at_level(logging.INFO, logger="ai"), execution_context("run-stop", lambda: True):
        with pytest.raises(oc.OpenCodeCancelledError):
            oc.OpenCodeZenClient(api_key="fixture-key").translate("private-prompt")
    start.assert_not_called()
    assert "PROVIDER_CANCELLED" in caplog.text and "PROVIDER_COMPLETED" not in caplog.text
    assert "private-prompt" not in caplog.text


def test_cancel_while_cli_is_waiting_terminates_and_cleans(isolated, monkeypatch, caplog):
    cancelled = threading.Event()
    process = make_process()
    def wait(request, timeout):
        cancelled.set()
        raise subprocess.TimeoutExpired("private-command", timeout)
    process.communicate.side_effect = wait
    terminate, start = Mock(), Mock(return_value=process)
    monkeypatch.setattr(oc, "_terminate_process_tree", terminate)
    monkeypatch.setattr(oc.subprocess, "Popen", start)
    with caplog.at_level(logging.INFO, logger="ai"), execution_context("run-stop", cancelled.is_set):
        with pytest.raises(oc.OpenCodeCancelledError):
            oc.OpenCodeZenClient(api_key="fixture-key").translate("private-prompt")
    terminate.assert_called_once_with(process)
    assert process.communicate.call_args.kwargs["timeout"] <= 0.25
    assert not Path(start.call_args.kwargs["cwd"]).exists()
    assert "PROVIDER_CANCELLED" in caplog.text and "PROVIDER_COMPLETED" not in caplog.text


def test_polling_communicate_sends_stdin_exactly_once(monkeypatch):
    process = make_process()
    process.communicate.side_effect = [subprocess.TimeoutExpired("opencode", 0.25), ("answer", "")]
    assert oc._communicate(process, "request", 5, ExecutionContext("run", lambda: False)) == ("answer", "")
    assert [call.args[0] for call in process.communicate.call_args_list] == ["request", None]


def test_polling_timeout_keeps_single_deadline(monkeypatch):
    clock = [0.0]
    process = make_process()
    def wait(request, timeout):
        clock[0] += timeout
        raise subprocess.TimeoutExpired("private-command", timeout)
    process.communicate.side_effect = wait
    terminate = Mock()
    monkeypatch.setattr(oc.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(oc, "_terminate_process_tree", terminate)
    with pytest.raises(oc.OpenCodeRequestError, match="quá lâu"):
        oc._communicate(process, "request", 1, ExecutionContext("run", lambda: False))
    assert clock[0] == 1 and process.communicate.call_count == 4
    terminate.assert_called_once_with(process)


def test_pipe_error_terminates_owned_cli_before_raising(monkeypatch):
    process = make_process()
    process.communicate.side_effect = BrokenPipeError("private-path")
    terminate = Mock()
    monkeypatch.setattr(oc, "_terminate_process_tree", terminate)
    with pytest.raises(BrokenPipeError):
        oc._communicate(process, "request", 1, ExecutionContext())
    terminate.assert_called_once_with(process)


@pytest.mark.parametrize("cancel_check", [None, lambda: False])
def test_real_unread_large_stdin_obeys_total_deadline_and_reaps_process(cancel_check, monkeypatch):
    monkeypatch.setattr(oc, "_IS_WINDOWS", True)
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", "import time; print('ready', flush=True); time.sleep(30)"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    watchdog_fired = threading.Event()

    def emergency_cleanup():
        watchdog_fired.set()
        process.kill()

    # Prevent an implementation regression from leaving pytest/its child hung.
    watchdog = threading.Timer(5, emergency_cleanup)
    watchdog.start()
    try:
        assert process.stdout.readline().strip() == "ready"
        started = time.monotonic()
        with pytest.raises(oc.OpenCodeRequestError, match="quá lâu") as caught:
            oc._communicate(process, "private-prompt-" * 100_000, .2,
                            ExecutionContext("unread-pipe", cancel_check))
        elapsed = time.monotonic() - started
        assert elapsed < 2, f"Pipe delivery escaped the .2s deadline: {elapsed:.2f}s"
        assert "private-prompt" not in str(caught.value)
        assert process.poll() is not None
        assert all(stream.closed for stream in (process.stdin, process.stdout, process.stderr))
        assert not any(thread.name == f"opencode-io-{process.pid}" for thread in threading.enumerate())
        assert not watchdog_fired.is_set()
    finally:
        watchdog.cancel()
        watchdog.join()
        oc._terminate_process_tree(process)


def test_real_unread_large_stdin_is_cancellable_before_request_timeout(monkeypatch):
    monkeypatch.setattr(oc, "_IS_WINDOWS", True)
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", "import time; print('ready', flush=True); time.sleep(30)"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    cancelled = threading.Event()
    cancel = threading.Timer(.2, cancelled.set)
    watchdog = threading.Timer(5, process.kill)
    watchdog.start()
    try:
        assert process.stdout.readline().strip() == "ready"
        started = time.monotonic()
        cancel.start()
        with pytest.raises(oc.OpenCodeCancelledError, match="Đã hủy") as caught:
            oc._communicate(process, "private-prompt-" * 100_000, 120,
                            ExecutionContext("cancel-unread-pipe", cancelled.is_set))
        assert time.monotonic() - started < 2
        assert "private-prompt" not in str(caught.value)
        assert process.poll() is not None
        assert all(stream.closed for stream in (process.stdin, process.stdout, process.stderr))
        assert not any(thread.name == f"opencode-io-{process.pid}" for thread in threading.enumerate())
    finally:
        cancel.cancel()
        cancel.join()
        watchdog.cancel()
        watchdog.join()
        oc._terminate_process_tree(process)


def test_real_large_bidirectional_exchange_delivers_stdin_once_and_drains_both_outputs(monkeypatch):
    monkeypatch.setattr(oc, "_IS_WINDOWS", True)
    request = "你好·" * 100_000
    child = ("import sys; sys.stdout.write('o' * 200000); sys.stdout.flush(); "
             "sys.stderr.write('e' * 200000); sys.stderr.flush(); "
             "data = sys.stdin.read(); print('\\n' + str(len(data)))")
    process = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-c", child],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        stdout, stderr = oc._communicate(process, request, 5, ExecutionContext("full-pipe", lambda: False))
        assert stdout == "o" * 200_000 + f"\n{len(request)}\n"
        assert stderr == "e" * 200_000
        assert process.returncode == 0
        assert all(stream.closed for stream in (process.stdin, process.stdout, process.stderr))
        assert not any(thread.name == f"opencode-io-{process.pid}" for thread in threading.enumerate())
    finally:
        oc._terminate_process_tree(process)


def test_cancellation_during_retry_backoff_prevents_next_process(isolated, monkeypatch):
    cancelled = threading.Event()
    process = make_process(returncode=1)
    start = Mock(return_value=process)
    monkeypatch.setattr(oc.subprocess, "Popen", start)
    monkeypatch.setattr(oc.time, "sleep", lambda seconds: cancelled.set())
    with execution_context("run-retry", cancelled.is_set), pytest.raises(oc.OpenCodeCancelledError):
        oc.OpenCodeZenClient(api_key="fixture-key", max_retries=1).translate("hello")
    assert start.call_count == 1


def test_execution_context_restores_parent_and_sanitizes_log_identity():
    initial = current_execution_context()
    with execution_context("parent") as parent:
        with pytest.raises(ValueError):
            with execution_context("injected\nprivate-key") as nested:
                assert nested.run_id == "unscoped"
                raise ValueError
        assert current_execution_context() is parent
    assert current_execution_context() is initial
    with pytest.raises(TypeError):
        with execution_context("run", cancel_check=True):
            pass


def test_pipeline_blocking_context_propagates_and_cancellation_reaches_adapter():
    from types import SimpleNamespace
    from core.streaming.pipeline import StreamingPipelineSession
    marker = contextvars.ContextVar("pipeline-test-marker", default="missing")
    entered, exited = threading.Event(), threading.Event()
    session = SimpleNamespace(task_id="pipeline-run", is_stopped=False)

    def blocking():
        context = current_execution_context()
        assert context.run_id == "pipeline-run" and marker.get() == "parent-marker"
        entered.set()
        while not context.cancel_check():
            if exited.wait(0.01):
                raise AssertionError("Cancelled task did not signal adapter")
        exited.set()
        raise oc.OpenCodeCancelledError("stopped")

    async def run():
        marker.set("parent-marker")
        task = asyncio.create_task(StreamingPipelineSession._run_blocking(session, blocking))
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(0.01)
            assert entered.is_set()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=2)
            assert exited.is_set()
            assert current_execution_context().run_id == "unscoped"
            assert (await asyncio.to_thread(current_execution_context)).run_id == "unscoped"
        finally:
            exited.set()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


def test_validated_response_cache_skips_cli_and_invalidates_changed_config(isolated, monkeypatch, caplog):
    start = Mock(return_value=make_process(json.dumps({"type": "text", "part": {"text": '{"text":"valid"}'}})))
    monkeypatch.setattr(oc.subprocess, "Popen", start)
    client = oc.OpenCodeZenClient(api_key="fixture-key")
    validate = lambda raw: isinstance(json.loads(raw)["text"], str)
    with caplog.at_level(logging.INFO, logger="ai"):
        for _ in range(2):
            assert client.translate("hello", response_validator=validate, schema_id="text-v1") == '{"text":"valid"}'
    assert start.call_count == 1 and "AI_CACHE_HIT" in caplog.text
    client.translate("hello", response_validator=validate, schema_id="text-v2")
    client.translate("hello", response_validator=validate, schema_id="text-v2", use_cache=False)
    assert start.call_count == 3


def test_forced_review_bypasses_response_cache_without_mutating_resume_client(isolated, monkeypatch):
    from core.translation_review import AutomaticTranslationReviewer
    start = Mock(return_value=make_process(json.dumps({"type": "text", "part": {"text": '{"text":"valid"}'}})))
    monkeypatch.setattr(oc.subprocess, "Popen", start)
    client = oc.OpenCodeZenClient(api_key="fixture-key")
    reviewer = AutomaticTranslationReviewer(client=client)
    options = {"response_validator": lambda raw: isinstance(json.loads(raw)["text"], str), "schema_id": "text-v1"}
    reviewer._review_client(False).translate("same review", **options)
    fresh = reviewer._review_client(True)
    assert type(fresh) is oc.OpenCodeZenClient and fresh.execution_layer is client.execution_layer
    fresh.translate("same review", **options)
    assert start.call_count == 2, "Forced review must invoke the provider despite a valid cached response"
    assert reviewer._review_client(False) is client
    client.translate("same review", **options)
    assert start.call_count == 2, "Ordinary unchanged Resume must retain validated caching"


def test_malformed_response_not_cached_and_schema_error_retains_safe_cause(isolated, monkeypatch):
    start = Mock(return_value=make_process(json.dumps({"type": "text", "part": {"text": '{broken private-output'}})))
    monkeypatch.setattr(oc.subprocess, "Popen", start)
    client = oc.OpenCodeZenClient(api_key="fixture-key", max_retries=2)
    for _ in range(2):
        with pytest.raises(oc.OpenCodeResponseValidationError) as caught:
            client.translate("hello", response_validator=json.loads, schema_id="text-v1")
        assert isinstance(caught.value.__cause__, json.JSONDecodeError)
        assert "private-output" not in str(caught.value)
    assert start.call_count == 2 and not list((isolated / "cache").glob("*.json"))


def test_credentials_echo_is_never_written_to_success_cache(isolated, monkeypatch):
    start = Mock(return_value=make_process(json.dumps({"type": "text", "part": {"text": '{"text":"private-api-key"}'}})))
    monkeypatch.setattr(oc.subprocess, "Popen", start)
    client = oc.OpenCodeZenClient(api_key="private-api-key")
    for _ in range(2):
        client.translate("hello", response_validator=json.loads, schema_id="text-v1")
    assert start.call_count == 2 and not list((isolated / "cache").glob("*.json"))


@pytest.mark.parametrize("failure, attempts, code", [
    ("HTTP 429 rate limit", 2, "provider_rate_limited"),
    ("HTTP 503 service unavailable", 2, "provider_server"),
    ("ECONNRESET network failed", 2, "provider_transport"),
    ("HTTP 401 invalid API key", 1, "provider_authentication"),
    ("HTTP 403 forbidden", 1, "provider_authentication"),
    ("Model not found", 1, "provider_model"),
    ("FreeTierError", 1, "provider_rejected"),
])
def test_provider_failure_categories_control_retry_without_logging_raw(
        isolated, monkeypatch, failure, attempts, code, caplog):
    process = make_process("", failure + " private-key private-prompt", 1)
    start = Mock(return_value=process)
    monkeypatch.setattr(oc.subprocess, "Popen", start)
    monkeypatch.setattr(oc, "_retry_delay", lambda seconds, context: oc._check_cancelled(context))
    with caplog.at_level(logging.INFO, logger="ai"):
        with pytest.raises(oc.OpenCodeRequestError) as caught:
            oc.OpenCodeZenClient(api_key="private-key", max_retries=1).translate("private-prompt")
    assert start.call_count == attempts and caught.value.code == code
    assert f"code={code}" in caplog.text and "private-" not in caplog.text


def test_stage_deadline_can_be_shorter_but_never_silently_longer(isolated, monkeypatch):
    process = make_process()
    monkeypatch.setattr(oc.subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(oc, "_IS_WINDOWS", False)
    client = oc.OpenCodeZenClient(api_key="test", timeout=60, task_timeouts={"address": 15})
    client.translate("hello", task_kind="address")
    assert process.communicate.call_args.kwargs["timeout"] == 15
    client.translate("hello", task_kind="translation")
    assert process.communicate.call_args.kwargs["timeout"] == 60
    with pytest.raises(oc.OpenCodeConfigurationError):
        oc.OpenCodeZenClient(api_key="test", timeout=60, task_timeouts={"address": 120})


def test_circuit_breaker_is_shared_across_reconstructed_clients(isolated, monkeypatch):
    layer = AIExecutionLayer(AIExecutionPolicy(circuit_failures=1), cache_root=isolated / "cache")
    monkeypatch.setattr(oc, "_shared_execution_layer", lambda: layer)
    process = make_process("", "HTTP 503", 1)
    start = Mock(return_value=process)
    monkeypatch.setattr(oc.subprocess, "Popen", start)
    with pytest.raises(oc.OpenCodeRequestError):
        oc.OpenCodeZenClient(api_key="test").translate("first")
    with pytest.raises(oc.OpenCodeRequestError) as caught:
        oc.OpenCodeZenClient(api_key="test").translate("next")
    assert caught.value.code == "provider_circuit_open" and caught.value.retry_after > 0
    start.assert_called_once()
