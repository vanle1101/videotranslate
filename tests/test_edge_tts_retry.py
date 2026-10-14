"""Retry empty Edge responses without publishing incomplete speech files."""
import asyncio
import logging
import ssl
from pathlib import Path
from types import SimpleNamespace
from email.utils import formatdate
from unittest.mock import AsyncMock, Mock, patch

import pytest
import aiohttp
from edge_tts.exceptions import NoAudioReceived

from core.engines.tts.edge_fallback import EdgeTTSFallbackEngine, EdgeTTSRequestError
from core.runtime_context import execution_context


@pytest.mark.parametrize("header,expected", [("720",720), ("12.5",12.5),
    ("-1",None),("nan",None),("inf",None),("garbage",None),("",None),(None,None)])
def test_retry_after_numeric_and_invalid_values(header, expected):
    from core.engines.tts.edge_fallback import _provider_error
    error=aiohttp.ClientResponseError(None,(),status=429,headers={"retry-after":header})
    typed=_provider_error(error,exhausted=True)
    assert typed.retry_after == expected and typed.retryable


@pytest.mark.parametrize("header_kind", ["numeric","date"])
def test_exhausted_retry_after_survives_six_attempts_into_durable_schedule(tmp_path,header_kind):
    from core.streaming.retry_schedule import schedule_retry
    now=1_800_000_000.
    header="720" if header_kind == "numeric" else formatdate(now+720,usegmt=True)
    error=aiohttp.ClientResponseError(None,(),status=429,headers={"Retry-After":header})
    with patch("core.engines.tts.edge_fallback.time.time",return_value=now), \
            patch("core.engines.tts.edge_fallback.edge_tts.Communicate",side_effect=error) as factory, \
            patch("core.engines.tts.edge_fallback.asyncio.sleep",new_callable=AsyncMock) as sleep:
        with pytest.raises(EdgeTTSRequestError) as caught:
            EdgeTTSFallbackEngine().synthesize("Xin chào",tmp_path/"speech.mp3")
    assert factory.call_count == 6 and sleep.await_count == 5
    assert all(call.args[0]>=720 for call in sleep.await_args_list)
    assert caught.value.retry_after == 720
    session=SimpleNamespace(_retry_schedule=[],_restart_intent="active")
    record=schedule_retry(session,"speech","1",caught.value,"a"*64,now=now)
    assert record["next_attempt_at"]==now+720
    assert record["error_code"]=="tts_rate_limited"


def test_backoff_is_exponential_with_bounded_jitter_and_provider_floor():
    from core.engines.tts.edge_fallback import _backoff_seconds
    with patch("core.engines.tts.edge_fallback.random.uniform",side_effect=lambda low,high:high):
        assert [_backoff_seconds(attempt) for attempt in range(7)]==[2.5,5,10,20,40,60,60]
        assert _backoff_seconds(0,720)==720
    with patch("core.engines.tts.edge_fallback.random.uniform",return_value=0):
        assert _backoff_seconds(0)==2


@pytest.mark.parametrize("header",["1e308",formatdate(1_800_000_000+8*86400,usegmt=True)])
def test_excessive_retry_after_is_terminal_without_early_retry_or_unbounded_wait(tmp_path,header):
    from core.streaming.retry_schedule import schedule_retry
    output=tmp_path/"speech.mp3"
    output.write_bytes(b"previous accepted audio")
    error=aiohttp.ClientResponseError(None,(),status=429,headers={"Retry-After":header})
    with patch("core.engines.tts.edge_fallback.time.time",return_value=1_800_000_000), \
            patch("core.engines.tts.edge_fallback.edge_tts.Communicate",side_effect=error) as factory, \
            patch("core.engines.tts.edge_fallback.asyncio.sleep",new_callable=AsyncMock) as sleep:
        with pytest.raises(EdgeTTSRequestError,match="quá 7 ngày") as caught:
            EdgeTTSFallbackEngine().synthesize("Xin chào",output)
    factory.assert_called_once()
    sleep.assert_not_awaited()
    assert caught.value.code=="tts_retry_after_unsupported" and not caught.value.retryable
    session=SimpleNamespace(_retry_schedule=[],_restart_intent="active")
    assert schedule_retry(session,"speech","1",caught.value,"a"*64) is None
    assert session._retry_schedule==[] and output.read_bytes()==b"previous accepted audio"
    assert not list(tmp_path.glob("edge_tts_*"))


def test_stop_interrupts_long_retry_after_wait_without_second_request(tmp_path):
    output=tmp_path/"speech.mp3"
    output.write_bytes(b"previous accepted audio")
    cancelled=False
    drained=[]
    async def wait(seconds):
        nonlocal cancelled
        assert seconds>=720
        try:
            cancelled=True
            await asyncio.Event().wait()
        finally:
            drained.append(True)
    error=aiohttp.ClientResponseError(None,(),status=429,headers={"Retry-After":"720"})
    with execution_context("edge-stop-backoff",lambda:cancelled), \
            patch("core.engines.tts.edge_fallback.edge_tts.Communicate",side_effect=error) as factory, \
            patch("core.engines.tts.edge_fallback.asyncio.sleep",side_effect=wait):
        with pytest.raises(asyncio.CancelledError):
            EdgeTTSFallbackEngine().synthesize("Xin chào",output)
    factory.assert_called_once()
    assert drained==[True] and output.read_bytes()==b"previous accepted audio"
    assert not list(tmp_path.glob("edge_tts_*"))


@pytest.mark.parametrize("installed", ["6.1.10", "7.2.8", "7.3.0"])
def test_obsolete_edge_client_fails_actionably_before_network(tmp_path, installed):
    output = tmp_path / "speech.wav"
    output.write_bytes(b"existing saved audio")
    with patch("core.engines.tts.edge_fallback.version", return_value=installed), \
            patch("core.engines.tts.edge_fallback.edge_tts.Communicate") as factory:
        with pytest.raises(EdgeTTSRequestError, match="7.3.1") as caught:
            EdgeTTSFallbackEngine().synthesize("Xin chào", output)
    assert caught.value.code == "tts_client_outdated" and not caught.value.retryable
    factory.assert_not_called()
    assert output.read_bytes() == b"existing saved audio"


def test_updated_edge_protocol_is_installed():
    # Real SDK contract: a compatible version must actually advertise v2.
    from edge_tts.constants import SEC_MS_GEC_VERSION
    from core.engines.tts.edge_fallback import _validate_edge_client
    _validate_edge_client()
    assert SEC_MS_GEC_VERSION.startswith("2-")


def test_upgraded_disk_package_with_stale_loaded_module_requires_restart(tmp_path):
    with patch("core.engines.tts.edge_fallback.SEC_MS_GEC_VERSION", "1-143.0.3650.75"), \
            patch("core.engines.tts.edge_fallback.edge_tts.Communicate") as factory:
        with pytest.raises(EdgeTTSRequestError, match="Khởi động lại Studio") as caught:
            EdgeTTSFallbackEngine().synthesize("Xin chào", tmp_path / "unused.wav")
    assert caught.value.code == "tts_client_outdated"
    factory.assert_not_called()


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
    assert [kwargs["boundary"] for _, kwargs in attempts] == ["WordBoundary", "SentenceBoundary", "SentenceBoundary"]
    assert len(sleep.await_args_list) == 2
    assert 2 <= sleep.await_args_list[0].args[0] <= 2.5
    assert 4 <= sleep.await_args_list[1].args[0] <= 5
    assert not list(tmp_path.glob("edge_tts_*"))


def test_empty_word_metadata_request_recovers_with_sentence_audio_without_fake_word_timings(tmp_path):
    modes = []
    def communicate(*args, **kwargs):
        modes.append(kwargs["boundary"])
        async def save(path, metadata):
            if kwargs["boundary"] == "WordBoundary":
                raise NoAudioReceived("service rejected word boundary mode")
            Path(path).write_bytes(b"real provider audio")
            Path(metadata).write_text('{"type":"SentenceBoundary","text":"Xin chào","offset":0,"duration":10000000}\n', encoding="utf-8")
        return Mock(save=save)
    output = tmp_path / "speech.mp3"
    engine = EdgeTTSFallbackEngine()
    with patch("core.engines.tts.edge_fallback.edge_tts.Communicate", side_effect=communicate), \
            patch("core.engines.tts.edge_fallback.asyncio.sleep", new_callable=AsyncMock):
        engine.synthesize("Xin chào", output)
    assert modes == ["WordBoundary", "SentenceBoundary"]
    assert output.read_bytes() == b"real provider audio"
    assert engine.take_word_boundaries(output) == []


def test_repeated_empty_responses_fail_cleanly_and_preserve_previous_output(tmp_path):
    output = tmp_path / "speech.mp3"
    output.write_bytes(b"existing user audio")
    response = Mock(save=AsyncMock(side_effect=NoAudioReceived("empty service response")))
    with patch("core.engines.tts.edge_fallback.edge_tts.Communicate", return_value=response) as factory, \
            patch("core.engines.tts.edge_fallback.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(EdgeTTSRequestError, match="sau 6 lần thử") as caught:
            EdgeTTSFallbackEngine().synthesize("Xin chào", output)
    assert caught.value.code == "tts_empty_audio" and caught.value.retryable and caught.value.status is None
    assert factory.call_count == 6
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
    assert all(call["boundary"] == "WordBoundary" for call in calls)
    assert "TTS_REQUEST_FAILED run_id=edge-retry-test" in caplog.text
    assert "TTS_RESPONSE run_id=edge-retry-test" in caplog.text
    assert "Secret source dialogue" not in caplog.text and "secret-sensitive-url" not in caplog.text


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_nontransient_http_errors_are_not_retried(tmp_path, status):
    error = aiohttp.ClientResponseError(None, (), status=status, headers={"Retry-After":"1e308"})
    with patch("core.engines.tts.edge_fallback.edge_tts.Communicate", side_effect=error) as factory:
        with pytest.raises(EdgeTTSRequestError, match="Edge-TTS") as caught:
            EdgeTTSFallbackEngine().synthesize("Xin chào", tmp_path / "speech.mp3")
    assert caught.value.status == status and not caught.value.retryable
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
    assert not caught.value.retryable
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
    assert not caught.value.retryable
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
        with pytest.raises(RuntimeError, match="kết nối ổn định.*6 lần"):
            EdgeTTSFallbackEngine().synthesize("Xin chào", tmp_path / "speech.mp3")
    assert factory.call_count == 6 and len(drained) == 6
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("kind,code,status", [("disconnect", "tts_transport", None),
    ("timeout", "tts_timeout", None), ("429", "tts_rate_limited", 429),
    ("500", "tts_http_error", 500), ("503", "tts_http_error", 503)])
def test_exhausted_transient_failures_keep_safe_type_for_late_recovery(tmp_path, kind, code, status):
    errors = {"disconnect": aiohttp.ServerDisconnectedError("private-url private-auth"),
              "timeout": asyncio.TimeoutError("private-url private-auth"),
              **{str(number): aiohttp.ClientResponseError(None, (), status=number,
                    message="private-url private-auth") for number in (429, 500, 503)}}
    output = tmp_path / "speech.mp3"
    output.write_bytes(b"previous accepted audio")
    with patch("core.engines.tts.edge_fallback.edge_tts.Communicate", side_effect=errors[kind]) as factory, \
            patch("core.engines.tts.edge_fallback.asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(EdgeTTSRequestError) as caught:
            EdgeTTSFallbackEngine().synthesize("Xin chào", output)
    assert caught.value.code == code and caught.value.status == status and caught.value.retryable is True
    assert factory.call_count == 6 and "private-" not in str(caught.value)
    assert output.read_bytes() == b"previous accepted audio" and not list(tmp_path.glob("edge_tts_*"))


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
