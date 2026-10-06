"""Retry empty Edge responses without publishing incomplete speech files."""
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from edge_tts.exceptions import NoAudioReceived

from core.engines.tts.edge_fallback import EdgeTTSFallbackEngine


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
    assert [call.args[0] for call in sleep.await_args_list] == [0.5, 1.0]
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
        with pytest.raises(ValueError, match="invalid voice"):
            EdgeTTSFallbackEngine().synthesize("Xin chào", tmp_path / "speech.mp3")
    factory.assert_called_once()
    sleep.assert_not_awaited()
    assert not list(tmp_path.iterdir())
