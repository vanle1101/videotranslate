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
from pathlib import Path
from unittest.mock import Mock

import pytest

from core.engines.translation import opencode_client as oc
from config import settings
from core.runtime_context import execution_context, current_execution_context
from core.runtime_context import current_execution_context, execution_context, ExecutionContext


@pytest.fixture
def isolated(monkeypatch, tmp_path):
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


def test_errors_do_not_include_raw_cli_outputs(isolated, monkeypatch):
    process = make_process("", "token fixture-key private-prompt", returncode=1)
    monkeypatch.setattr(oc.subprocess, "Popen", Mock(return_value=process))
    with pytest.raises(oc.OpenCodeRequestError) as caught:
        oc.OpenCodeZenClient(api_key="fixture-key").translate("private-prompt")
    assert "fixture-key" not in str(caught.value)
    assert "private-prompt" not in str(caught.value)


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
