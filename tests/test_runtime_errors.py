import errno
import logging

import pytest

from core.runtime_errors import export_failure, redacted_detail, redacted_traceback
from config import settings


@pytest.mark.parametrize("error,category,message", [
    (FileNotFoundError("source.mp4"), "missing_file", "Thiếu video"),
    (OSError(errno.ENOSPC, "No space left on device"), "disk_full", "không đủ chỗ"),
    (PermissionError("locked output"), "file_access", "Không ghi được"),
    (RuntimeError("FFmpeg thất bại: Invalid filter"), "media_process", "FFmpeg"),
])
def test_export_diagnostics_keep_cause_stage_and_action(caplog, error, category, message):
    with caplog.at_level(logging.ERROR, logger="errors"):
        result = export_failure(error, "export_test", "Render MP4")
    assert message in result and "export_test" in result
    assert f"category={category}" in caplog.text
    assert "stage=Render MP4" in caplog.text and type(error).__name__ in caplog.text


def test_diagnostics_redact_settings_and_provider_credentials(monkeypatch, caplog):
    monkeypatch.setattr(settings, "OPENCODE_API_KEY", "private-configured-key")
    error = RuntimeError("FFmpeg private-configured-key Authorization: Bearer secret-bearer-token "
                         "api_key=secret-inline https://example.test/file?signature=private-url")
    with caplog.at_level(logging.ERROR, logger="errors"):
        result = export_failure(error, "export_test", "Render")
    for secret in ("private-configured-key", "secret-bearer-token", "secret-inline", "private-url"):
        assert secret not in caplog.text and secret not in result
    assert "[redacted]" in caplog.text
    assert len(redacted_detail("x" * 10000)) == 6000


def test_export_diagnostics_preserve_chained_traceback_without_credentials(monkeypatch, caplog):
    monkeypatch.setattr(settings, "OPENCODE_API_KEY", "private-configured-key")
    try:
        try:
            raise ValueError("provider Authorization: Bearer private-bearer")
        except ValueError as cause:
            raise RuntimeError("FFmpeg private-configured-key") from cause
    except RuntimeError as error:
        trace = redacted_traceback(error)
        with caplog.at_level(logging.ERROR, logger="errors"):
            export_failure(error, "export-chain", "Render")
    assert "ValueError: provider" in trace
    assert "direct cause" in trace and "test_export_diagnostics_preserve_chained_traceback" in trace
    assert "ValueError: provider" in caplog.text and "direct cause" in caplog.text
    assert "private-bearer" not in caplog.text and "private-configured-key" not in caplog.text


def test_provider_trace_keeps_full_frame_chain_without_echoing_arbitrary_private_body():
    try:
        try:
            raise ValueError("private-provider-body-which-is-not-a-credential")
        except ValueError as cause:
            raise RuntimeError("private-user-prompt") from cause
    except RuntimeError as error:
        trace = redacted_traceback(error, include_messages=False)
    assert "ValueError" in trace and "RuntimeError" in trace and "direct cause" in trace
    assert trace.count("in test_provider_trace_keeps_full_frame_chain") == 2
    assert "private-provider-body" not in trace and "private-user-prompt" not in trace
