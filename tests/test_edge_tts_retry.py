"""Retry empty Edge responses without publishing incomplete speech files."""
import asyncio
import logging
import ssl
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
import aiohttp
from edge_tts.exceptions import NoAudioReceived

from core.engines.tts.edge_fallback import EdgeTTSFallbackEngine, EdgeTTSRequestError
from core.runtime_context import execution_context


def test_empty_service_responses_retry_fresh_stream_and_discard_partial_audio(tmp_path):
    attempts = []

    def communicate(*args, **kwargs):
        attempt = len(attempts)
        attempts.append((args, kwargs))

        async def save(path, metadata_path=None):
            path = Path(path)
            assert not path.exists()
            if attempt < 2:
                path.write_bytes(b"incomplete")
                raise NoAudioReceived("empty service response")
            path.write_bytes(b"complete audio")

        return Mock(save=save)

    output = tmp_path / "speech.mp3"
    with patch("core.engines.tts.edge_fallback.edge_tts.Communicate", side_effect=communicate), \
            patch("core.engines.tts.edge_fallback.asyncio.sleep", new_callable=AsyncMock) as sleep:
        EdgeTTSFallbackEngine().synthesize("Xin chào", output, voice="vi-VN-NamMinhNeural")
    assert output.read_bytes() == b"complete audio"
    assert len(attempts) == 3
    assert all(args == ("Xin chào", "vi-VN-NamMinhNeural") for args, _ in attempts)
    assert [call.args[0] for call in sleep.await_args_list] == [3.0, 8.0]
    assert not list(tmp_path.glob("edge_tts_*"))


def test_repeated_empty_responses_fail_cleanly_and_preserve_previous_output(tmp_path):
    output = tmp_path / "speech.mp3"
    output.write_bytes(b"existing user audio")
    response = Mock(save=AsyncMock(side_effect=NoAudioReceived("empty service response")))
    with patch("core.engines.tts.edge_fallback.edge_tts.Communicate", return_value=response) as factory, \
            patch("core.engines.tts.edge_fallback.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(RuntimeError, match="sau 3 lần thử"):
            EdgeTTSFallbackEngine().synthesize("Xin chào", output)
    assert factory.call_count == 3
    assert output.read_bytes() == b"existing user audio"
    assert not list(tmp_path.glob("edge_tts_*"))


def test_configuration_errors_are_not_retried(tmp_path):
    with patch("core.engines.tts.edge_fallback.edge_tts.Communicate", side_effect=ValueError("invalid voice")) as factory, \
            patch("core.engines.tts.edge_fallback.asyncio.sleep", new_callable=AsyncMock) as sleep:
        with pytest.raises(ValueError, match="không hợp lệ"):
            EdgeTTSFallbackEngine().synthesize("Xin chào", tmp_path / "speech.mp3")
    factory.assert_called_once()
    sleep.assert_not_awaited()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("kind", ["disconnect", "timeout", "429", "503"])
def test_transient_transport_failures_retry_and_log_safe_request_identity(tmp_path, caplog, kind):
    calls = []
    errors = {"disconnect": aiohttp.ServerDisconnectedError("secret-sensitive-url"),
              "timeout": asyncio.TimeoutError(),
              "429": aiohttp.ClientResponseError(None, (), status=429),
              "503": aiohttp.ClientResponseError(None, (), status=503)}
    def communicate(*args, **kwargs):
        calls.append(kwargs)
        async def save(path, metadata):
            if len(calls) == 1:
                Path(path).write_bytes(b"partial")
                raise errors[kind]
            assert not Path(path).exists()
            Path(path).write_bytes(b"complete audio")
        return Mock(save=save)
    with caplog.at_level(logging.INFO, logger="pipeline"), execution_context("edge-retry-test"), \
            patch("core.engines.tts.edge_fallback.edge_tts.Communicate", side_effect=communicate), \
            patch("core.engines.tts.edge_fallback.asyncio.sleep", new_callable=AsyncMock):
        EdgeTTSFallbackEngine().synthesize("Secret source dialogue", tmp_path / "speech.mp3")
    assert len(calls) == 2
    assert "TTS_REQUEST_FAILED run_id=edge-retry-test" in caplog.text
    assert "TTS_RESPONSE run_id=edge-retry-test" in caplog.text
    assert "Secret source dialogue" not in caplog.text and "secret-sensitive-url" not in caplog.text


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_nontransient_http_errors_are_not_retried(tmp_path, status):
    error = aiohttp.ClientResponseError(None, (), status=status)
    with patch("core.engines.tts.edge_fallback.edge_tts.Communicate", side_effect=error) as factory:
        with pytest.raises(RuntimeError, match="Edge-TTS"):
            EdgeTTSFallbackEngine().synthesize("Xin chào", tmp_path / "speech.mp3")
    factory.assert_called_once()


@pytest.mark.parametrize("verify_code, expected", [(9, "ngày giờ"), (10, "hết hạn"),
                                                  (62, "khớp máy chủ"), (20, "chứng chỉ")])
def test_certificate_failure_has_precise_safe_diagnostic_and_never_disables_verification(
        tmp_path, caplog, verify_code, expected):
    certificate = ssl.SSLCertVerificationError(1, "private-certificate-name private-url")
    certificate.verify_code = verify_code
    certificate.verify_message = "private-provider-details"
    error = aiohttp.ClientConnectorCertificateError(None, certificate)
    output = tmp_path / "speech.wav"
    output.write_bytes(b"previous user audio")
    with caplog.at_level(logging.INFO, logger="pipeline"), execution_context("edge-cert-test"), \
            patch("core.engines.tts.edge_fallback.edge_tts.Communicate", side_effect=error) as factory, \
            patch("core.engines.tts.edge_fallback.asyncio.sleep", new_callable=AsyncMock) as sleep:
        with pytest.raises(EdgeTTSRequestError, match=expected) as caught:
            EdgeTTSFallbackEngine().synthesize("Private dialogue", output)
    assert caught.value.code == "tts_tls_certificate" and caught.value.status is None
    assert caught.value.tls_verify_code == verify_code
    assert "TLS" in str(caught.value) and "Kiểm tra mạng" not in str(caught.value)
    assert f"code=tts_tls_certificate tls_verify_code={verify_code}" in caplog.text
    assert "status=none" in caplog.text and "TTS_RESPONSE" not in caplog.text
    assert "private-" not in caplog.text + str(caught.value) and "Private dialogue" not in caplog.text
    factory.assert_called_once()
    sleep.assert_not_awaited()
    assert "connector" not in factory.call_args.kwargs and "ssl" not in factory.call_args.kwargs
    assert output.read_bytes() == b"previous user audio"
    assert not list(tmp_path.glob("edge_tts_*"))


def test_tls_handshake_failure_is_distinct_from_http_and_transport(tmp_path, caplog):
    error = aiohttp.ClientConnectorSSLError(None, ssl.SSLError("private-sensitive-host"))
    with caplog.at_level(logging.INFO, logger="pipeline"), \
            patch("core.engines.tts.edge_fallback.edge_tts.Communicate", side_effect=error) as factory:
        with pytest.raises(EdgeTTSRequestError, match="TLS") as caught:
            EdgeTTSFallbackEngine().synthesize("Xin chào", tmp_path / "speech.mp3")
    assert caught.value.code == "tts_tls_handshake" and caught.value.status is None
    assert "code=tts_tls_handshake tls_verify_code=none" in caplog.text
    assert "private-sensitive-host" not in caplog.text + str(caught.value)
    factory.assert_called_once()
    assert not list(tmp_path.iterdir())


def test_stop_interrupts_pending_service_request_and_preserves_previous_output(tmp_path):
    output = tmp_path / "speech.mp3"
    output.write_bytes(b"previous completed audio")
    cancelled = False
    drained = []
    async def save(path, metadata):
        nonlocal cancelled
        try:
            Path(path).write_bytes(b"partial")
            cancelled = True
            await asyncio.Event().wait()
        finally:
            drained.append(True)
    with execution_context("edge-stop", lambda: cancelled), \
            patch("core.engines.tts.edge_fallback.edge_tts.Communicate", return_value=Mock(save=save)):
        with pytest.raises(asyncio.CancelledError):
            EdgeTTSFallbackEngine().synthesize("Xin chào", output)
    assert drained == [True] and output.read_bytes() == b"previous completed audio"
    assert not list(tmp_path.glob("edge_tts_*"))


def test_total_service_timeout_is_bounded_and_drains_each_attempt(tmp_path):
    drained = []
    async def save(path, metadata):
        try:
            await asyncio.Event().wait()
        finally:
            drained.append(True)
    with patch("core.engines.tts.edge_fallback.EDGE_REQUEST_TIMEOUT", .01), \
            patch("core.engines.tts.edge_fallback.asyncio.sleep", new_callable=AsyncMock), \
            patch("core.engines.tts.edge_fallback.edge_tts.Communicate", return_value=Mock(save=save)) as factory:
        with pytest.raises(RuntimeError, match="kết nối ổn định.*3 lần"):
            EdgeTTSFallbackEngine().synthesize("Xin chào", tmp_path / "speech.mp3")
    assert factory.call_count == 3 and len(drained) == 3
    assert not list(tmp_path.iterdir())


def test_conversion_failure_does_not_truncate_previous_wav(tmp_path):
    destination = tmp_path / "speech.wav"
    destination.write_bytes(b"previous valid audio")
    async def save(path, metadata):
        Path(path).write_bytes(b"bad provider MP3")
    def convert(command, cancel_check):
        Path(command[-1]).write_bytes(b"partial converted WAV")
        raise RuntimeError("Conversion failed")
    with patch("core.engines.tts.edge_fallback.edge_tts.Communicate", return_value=Mock(save=save)), \
            patch("core.engines.tts.edge_fallback.run_media", side_effect=convert):
        with pytest.raises(RuntimeError, match="Conversion failed"):
            EdgeTTSFallbackEngine().synthesize("Xin chào", destination)
    assert destination.read_bytes() == b"previous valid audio"
    assert not list(tmp_path.glob("edge_tts_*"))
