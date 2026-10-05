"""Small, on-demand voice samples with bounded memory and no retained audio files."""
import tempfile
import threading
import time
import uuid
from pathlib import Path

from core.voice_catalog import resolve_voice
from core.engines.tts.edge_fallback import EdgeTTSFallbackEngine
from core.engines.tts.vieneu_engine import VieNeuEngine

SAMPLE_TEXT = "Xin chào, đây là giọng đọc tiếng Việt. Bạn có thể chọn giọng này để thuyết minh video."


class VoicePreviewBusy(RuntimeError):
    pass


def select_voice(voice_id=None, engine=None, legacy_voice=None):
    """Resolve once before accepting work; never silently substitute a voice."""
    selection = resolve_voice(voice_id or legacy_voice, engine)
    if voice_id and legacy_voice and resolve_voice(legacy_voice, engine) != selection:
        raise ValueError("Giọng đọc và mã giọng không khớp. Hãy chọn lại giọng.")
    resolved_engine, voice = selection
    if resolved_engine == "vieneu-tts" and not VieNeuEngine().is_available:
        raise ValueError("Giọng VieNeu chưa sẵn sàng. Hãy cài model và runtime VieNeu.")
    if resolved_engine == "piper-tts":
        from core.engines.tts.piper_engine import PiperEngine
        if not PiperEngine().is_available:
            raise ValueError("Giọng Piper chưa sẵn sàng. Hãy cài model và runtime Piper.")
    return resolved_engine, voice


class VoicePreviewManager:
    max_samples = 8
    lifetime_seconds = 300

    def __init__(self):
        self._lock = threading.RLock()
        self._synthesis_lock = threading.Lock()
        self._samples = {}
        self._closed = False

    def _prune(self):
        now = time.monotonic()
        for key, (created, _) in list(self._samples.items()):
            if now - created >= self.lifetime_seconds:
                del self._samples[key]
        while len(self._samples) >= self.max_samples:
            del self._samples[next(iter(self._samples))]

    def create(self, voice_id):
        engine_name, voice = select_voice(voice_id)
        if not self._synthesis_lock.acquire(blocking=False):
            raise VoicePreviewBusy("Đang tạo mẫu giọng. Hãy chờ mẫu hiện tại hoàn tất.")
        try:
            with self._lock:
                if self._closed:
                    raise VoicePreviewBusy("Ứng dụng đang đóng.")
            if engine_name == "vieneu-tts":
                engine = VieNeuEngine()
            elif engine_name == "piper-tts":
                from core.engines.tts.piper_engine import PiperEngine
                engine = PiperEngine()
            else:
                engine = EdgeTTSFallbackEngine(voice=voice)
            with tempfile.TemporaryDirectory(prefix="studio-voice-") as directory:
                output = Path(directory) / "sample.wav"
                if engine_name == "vieneu-tts":
                    # Preview audio should not populate the persistent segment cache.
                    engine.cache_dir = Path(directory) / "cache"
                engine.synthesize(SAMPLE_TEXT, output, voice=voice)
                if not output.is_file() or not 44 < output.stat().st_size <= 8 * 1024 * 1024:
                    raise RuntimeError("Không nhận được mẫu âm thanh hợp lệ. Hãy thử lại.")
                audio = output.read_bytes()
            with self._lock:
                if self._closed:
                    raise VoicePreviewBusy("Ứng dụng đang đóng.")
                self._prune()
                key = uuid.uuid4().hex
                self._samples[key] = (time.monotonic(), audio)
            return {"preview_id": key, "status": "READY",
                    "audio_url": f"/api/voices/preview/{key}/audio"}
        finally:
            self._synthesis_lock.release()

    def audio(self, key):
        with self._lock:
            item = self._samples.get(key)
            if item is None or time.monotonic() - item[0] >= self.lifetime_seconds:
                self._samples.pop(key, None)
                raise KeyError(key)
            return item[1]

    def delete(self, key):
        with self._lock:
            self._samples.pop(key, None)

    def shutdown(self):
        with self._lock:
            self._closed = True
            self._samples.clear()
        # The in-flight inference owns its TemporaryDirectory and always cleans it.
        # Do not block Qt shutdown on a remote Edge response.


voice_preview_manager = VoicePreviewManager()
