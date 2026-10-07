import errno
import logging

import pytest

from core.runtime_errors import export_failure, redacted_detail
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
