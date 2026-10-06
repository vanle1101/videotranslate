"""Free-only OpenCode CLI adapter regression tests (never call the network)."""
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from core.engines.translation import opencode_client as oc
from config import settings


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "OPENCODE_API_KEY", "")
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "existing-data"))
    monkeypatch.setattr(oc, "find_opencode_executable", lambda: "opencode.exe")
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
    assert not Path(options["cwd"]).exists()
    env = options["env"]
    assert env["OPENCODE_API_KEY"] == "fixture-key"
    assert "OPENCODE_CONFIG" not in env
    assert "OPENCODE_PERMISSION" not in env
    assert "GEMINI_API_KEY" not in env
    assert env["OPENCODE_DISABLE_PROJECT_CONFIG"] == "true"
    assert env["OPENCODE_TEST_HOME"] != str(Path.home())
    cfg = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    assert cfg["model"] == cfg["small_model"] == "opencode/big-pickle"
    assert cfg["enabled_providers"] == ["opencode"]
    assert cfg["permission"] == {"*": "deny"}
    assert cfg["share"] == "disabled"
    assert cfg["snapshot"] is False
    assert cfg["autoupdate"] is False
    assert cfg["agent"]["video-translator"]["tools"] == {"*": False}
    assert "fixture-key" not in env["OPENCODE_CONFIG_CONTENT"]
    assert "provider" not in cfg  # Native provider supplies first-party auth behavior.
    sent = json.loads(process.communicate.call_args.args[0])
    assert sent == {"instructions": "Translate to Vietnamese", "input": prompt}
    assert process.communicate.call_args.kwargs["timeout"] == 20


def test_stdout_events_are_parsed_without_reasoning_or_tool_output(isolated, monkeypatch):
    events = [
        {"type": "reasoning", "part": {"text": "private reasoning"}},
        {"type": "tool_use", "part": {"text": "not answer"}},
        {"type": "text", "part": {"text": "Xin "}},
        {"type": "text", "part": {"text": "chào"}},
    ]
    process = make_process("notice\n" + "\n".join(map(json.dumps, events)))
    monkeypatch.setattr(oc.subprocess, "Popen", Mock(return_value=process))
    assert oc.OpenCodeZenClient(api_key="fixture-key").translate("hello") == "Xin chào"


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
