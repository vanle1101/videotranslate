import asyncio
import json
import logging
import time
import uuid
import wave
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Any, Optional
import edge_tts
import aiohttp
from config import settings
from core.engines.tts.base import TTSEngine
from core.media_process import run_media
from core.runtime_context import current_execution_context


EDGE_REQUEST_TIMEOUT = 45.0


class EdgeTTSRequestError(RuntimeError):
    """Safe provider category retained by runtime/recovery diagnostics."""

    def __init__(self, message, *, code, status=None, tls_verify_code=None, retryable=False):
        super().__init__(message)
        self.code = code
        self.status = status
        self.tls_verify_code = tls_verify_code
        self.retryable = retryable is True


def _tls_verify_code(error):
    code = getattr(getattr(error, "certificate_error", None), "verify_code", None)
    return code if type(code) is int and 0 <= code <= 1000 else None


def _failure_category(error):
    if isinstance(error, aiohttp.ClientConnectorCertificateError):
        return "tts_tls_certificate"
    if isinstance(error, aiohttp.ClientConnectorSSLError):
        return "tts_tls_handshake"
    if isinstance(error, aiohttp.ClientResponseError):
        return "tts_rate_limited" if error.status == 429 else "tts_http_error"
    if isinstance(error, asyncio.TimeoutError):
        return "tts_timeout"
    if isinstance(error, edge_tts.exceptions.NoAudioReceived):
        return "tts_empty_audio"
    return "tts_transport" if isinstance(error, aiohttp.ClientError) else "tts_failed"


def _retryable(error):
    if isinstance(error, (aiohttp.ClientConnectorCertificateError, aiohttp.ClientConnectorSSLError)):
        return False
    if isinstance(error, aiohttp.ClientResponseError):
        return error.status in {408, 429} or 500 <= error.status < 600
    return isinstance(error, (edge_tts.exceptions.NoAudioReceived,
                              aiohttp.ClientConnectionError, asyncio.TimeoutError))


def _provider_error(error, *, exhausted=False):
    """Keep URLs, headers and SDK response bodies out of the user-facing task."""
    if isinstance(error, aiohttp.ClientConnectorCertificateError):
        verify_code = _tls_verify_code(error)
        reason = ("Chứng chỉ đã hết hạn hoặc chưa có hiệu lực; kiểm tra ngày giờ máy."
                  if verify_code in {9, 10} else
                  "Chứng chỉ không khớp máy chủ; kiểm tra proxy/VPN hoặc phần mềm lọc mạng."
                  if verify_code == 62 else
                  "Kiểm tra ngày giờ máy và chứng chỉ của proxy/VPN hoặc phần mềm lọc mạng.")
        return EdgeTTSRequestError("Edge-TTS không xác minh được chứng chỉ TLS của kết nối. "
                                  + reason + " Bản dịch được giữ; thử lại câu này sau khi kết nối ổn định.",
                                  code="tts_tls_certificate", tls_verify_code=verify_code)
    if isinstance(error, aiohttp.ClientConnectorSSLError):
        return EdgeTTSRequestError("Edge-TTS không thiết lập được kết nối TLS. "
            "Kiểm tra proxy/VPN hoặc phần mềm lọc mạng rồi thử lại câu này.", code="tts_tls_handshake")
    if isinstance(error, aiohttp.ClientResponseError):
        status = error.status
        if status in {401, 403}:
            message = "Edge-TTS từ chối yêu cầu. Kiểm tra mạng hoặc thử lại sau."
        elif status == 429:
            message = "Edge-TTS đang giới hạn lượt yêu cầu. Hãy thử lại sau."
        else:
            message = "Edge-TTS không phản hồi đúng. Hãy thử lại sau."
        if exhausted:
            message = "Không kết nối ổn định tới Edge-TTS sau 3 lần thử. " + message
        return EdgeTTSRequestError(message, code=_failure_category(error), status=status,
                                   retryable=_retryable(error))
    if isinstance(error, edge_tts.exceptions.NoAudioReceived):
        return EdgeTTSRequestError("Edge-TTS chưa trả về âm thanh" + (" sau 3 lần thử" if exhausted else "")
            + ". Bản dịch được giữ; sẽ thử lại riêng câu này sau các câu khác.",
            code="tts_empty_audio", retryable=True)
    if isinstance(error, (aiohttp.ClientError, asyncio.TimeoutError)):
        message = ("Edge-TTS hết thời gian chờ. Hãy thử lại câu này." if isinstance(error, asyncio.TimeoutError)
                   else "Không kết nối được tới Edge-TTS. Kiểm tra mạng rồi thử lại.")
        if exhausted:
            message = "Không kết nối ổn định tới Edge-TTS sau 3 lần thử. " + message
        return EdgeTTSRequestError(message, code=_failure_category(error), retryable=_retryable(error))
    if isinstance(error, (TypeError, ValueError)):
        return ValueError("Cấu hình giọng Edge-TTS không hợp lệ; kiểm tra giọng và tốc độ đọc.")
    if isinstance(error, OSError):
        return RuntimeError("Không ghi được âm thanh Edge-TTS. Kiểm tra dung lượng và quyền ghi thư mục làm việc.")
    return RuntimeError("Edge-TTS gặp lỗi khi tạo giọng. Bản dịch được giữ; hãy thử lại câu này.")


async def _await_cancellable(awaitable, cancel_check, timeout):
    """Bound service work and Stop latency, draining the request before return."""
    worker = asyncio.ensure_future(awaitable)
    started = time.monotonic()
    try:
        while not worker.done():
            if cancel_check and cancel_check():
                raise asyncio.CancelledError
            if time.monotonic() - started >= timeout:
                raise asyncio.TimeoutError
            await asyncio.wait({worker}, timeout=.2)
        if cancel_check and cancel_check():
            raise asyncio.CancelledError
        return worker.result()
    finally:
        if not worker.done():
            worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

class EdgeTTSFallbackEngine(TTSEngine):
    """
    Fallback TTS engine using Microsoft Edge-TTS.
    Marked strictly as fallback in UI and reports.
    """
    def __init__(self, voice: Optional[str] = None):
        self.voice = voice or settings.EDGE_VOICE
        # Kept per output path so streaming can align captions to the exact
        # speech that was just synthesized. The metadata is never persisted in
        # user media or sent anywhere.
        self._word_boundaries = {}

    @property
    def name(self) -> str:
        return f"Edge-TTS ({self.voice}) [Fallback]"

    @property
    def is_available(self) -> bool:
        return True

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "engine": "edge-tts",
            "is_available": True,
            "is_fallback": True
        }

    def synthesize(
        self,
        text: str,
        output_path: Path,
        voice: Optional[str] = None,
        ref_audio: Optional[Path] = None,
        speed: float = 1.0,
        progress_callback: Optional[callable] = None
    ) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._word_boundaries.pop(str(output_path.resolve()), None)
        if not text.strip():
            raise ValueError("Không có nội dung tiếng Việt để đọc.")
        # VieNeu preset names are not valid Microsoft voice IDs.
        chosen_voice = voice if voice and voice.startswith("vi-VN-") else self.voice
        rate_str = f"+{int((speed - 1.0) * 100)}%" if speed >= 1.0 else f"{int((speed - 1.0) * 100)}%"
        execution = current_execution_context()
        logger = logging.getLogger("pipeline")
        request_id = uuid.uuid4().hex[:12]
        def check_cancel():
            if execution.cancel_check and execution.cancel_check():
                raise asyncio.CancelledError
        check_cancel()
        # Edge returns MP3 bytes. Convert to real PCM when a WAV is requested.
        with tempfile.TemporaryDirectory(prefix="edge_tts_", dir=output_path.parent) as temp_dir:
            mp3_path = Path(temp_dir) / "speech.mp3"
            metadata_path = Path(temp_dir) / "boundaries.jsonl"
            boundaries = []

            async def _run():
                # The service sometimes ends a valid request without audio. A
                # Communicate stream is single-use, so retry with a new instance
                # and discard any partial file before requesting the same voice.
                for attempt in range(3):
                    check_cancel()
                    started = time.monotonic()
                    try:
                        boundaries.clear()
                        logger.info("TTS_REQUEST run_id=%s request_id=%s provider=edge-tts voice=%s attempt=%d text_chars=%d",
                                    execution.run_id, request_id, chosen_voice, attempt + 1, len(text))
                        com = edge_tts.Communicate(text, chosen_voice, rate=rate_str,
                                                   boundary="WordBoundary", connect_timeout=10, receive_timeout=30)
                        await _await_cancellable(com.save(str(mp3_path), str(metadata_path)),
                                                 execution.cancel_check, EDGE_REQUEST_TIMEOUT)
                        if not mp3_path.is_file() or not mp3_path.stat().st_size:
                            raise edge_tts.exceptions.NoAudioReceived("Empty audio file")
                        if metadata_path.is_file():
                            try:
                                for line in metadata_path.read_text(encoding="utf-8").splitlines():
                                    message = json.loads(line)
                                    if message.get("type") == "WordBoundary":
                                        boundaries.append({
                                            "text": message.get("text", ""),
                                            "start": float(message.get("offset", 0)) / 10_000_000,
                                            "end": (float(message.get("offset", 0)) + float(message.get("duration", 0))) / 10_000_000,
                                        })
                            except (ValueError, TypeError, AttributeError):
                                # Valid speech remains usable if metadata is
                                # malformed; downstream exposes its fallback.
                                boundaries.clear()
                        logger.info("TTS_RESPONSE run_id=%s request_id=%s provider=edge-tts attempt=%d audio_bytes=%d word_boundaries=%d elapsed_ms=%d",
                                    execution.run_id, request_id, attempt + 1, mp3_path.stat().st_size,
                                    len(boundaries), int((time.monotonic() - started) * 1000))
                        return
                    except Exception as error:
                        logger.warning("TTS_REQUEST_FAILED run_id=%s request_id=%s provider=edge-tts voice=%s attempt=%d error_type=%s status=%s code=%s tls_verify_code=%s elapsed_ms=%d",
                                       execution.run_id, request_id, chosen_voice, attempt + 1, type(error).__name__,
                                       error.status if isinstance(error, aiohttp.ClientResponseError) else "none",
                                       _failure_category(error), _tls_verify_code(error) if _tls_verify_code(error) is not None else "none",
                                       int((time.monotonic() - started) * 1000))
                        if not _retryable(error):
                            raise _provider_error(error) from None
                        mp3_path.unlink(missing_ok=True)
                        metadata_path.unlink(missing_ok=True)
                        if attempt == 2:
                            raise _provider_error(error, exhausted=True) from None
                        await _await_cancellable(asyncio.sleep((3.0, 8.0)[attempt]),
                                                 execution.cancel_check, 10.0)

            try:
                asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(_run())
            else:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    executor.submit(lambda: asyncio.run(_run())).result()

            if output_path.suffix.lower() == ".mp3":
                check_cancel()
                mp3_path.replace(output_path)
            else:
                converted = Path(temp_dir) / "converted.wav"
                try:
                    run_media(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(mp3_path),
                               "-vn", "-ac", "1", "-ar", "24000", str(converted)], execution.cancel_check)
                except RuntimeError:
                    check_cancel()
                    raise
                with wave.open(str(converted), "rb") as audio:
                    frames = audio.getnframes()
                    if audio.getnchannels() != 1 or audio.getframerate() != 24000 or audio.getsampwidth() != 2 or frames <= 0:
                        raise RuntimeError("Edge-TTS trả về âm thanh không hợp lệ.")
                    audio.setpos(frames - 1)
                    if len(audio.readframes(1)) != 2:
                        raise RuntimeError("Edge-TTS trả về âm thanh chưa đầy đủ.")
                check_cancel()
                converted.replace(output_path)
        self._word_boundaries[str(output_path.resolve())] = boundaries
        while len(self._word_boundaries) > 64:
            self._word_boundaries.pop(next(iter(self._word_boundaries)))
        return output_path

    def take_word_boundaries(self, path: Path):
        """Return and consume boundaries for a completed synthesis."""
        return self._word_boundaries.pop(str(Path(path).resolve()), [])
