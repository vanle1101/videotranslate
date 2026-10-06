"""On-demand voice samples with bounded sessions and a reusable PCM cache."""
import hashlib
import importlib.metadata
import io
import json
import re
import tempfile
import threading
import time
import uuid
import wave
from pathlib import Path

from config import settings
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
    cache_schema = 1
    max_cached_samples = 32
    max_cache_bytes = 64 * 1024 * 1024
    max_sample_bytes = 8 * 1024 * 1024
    _cache_lock = threading.RLock()

    def __init__(self, cache_dir=None):
        self._lock = threading.RLock()
        self._synthesis_lock = threading.Lock()
        self._samples = {}
        self._closed = False
        self.cache_dir = (Path(cache_dir) if cache_dir is not None
                          else settings.WORKSPACE_DIR / "cache" / "voice_previews")

    @staticmethod
    def _engine_revision(engine_name):
        """Invalidate samples on adapter, installed runtime or local model changes.

        Stat model assets instead of re-reading gigabytes or loading inference.
        Nothing here downloads a model or includes API credentials.
        """
        packages = {"edge-tts": ("edge-tts",),
                    "piper-tts": ("piper-tts", "onnxruntime", "sea-g2p"),
                    "vieneu-tts": ("vieneu", "onnxruntime", "sea-g2p")}[engine_name]
        versions = {}
        for package in packages:
            try:
                versions[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                versions[package] = "unavailable"
        adapter = {"edge-tts": "edge_fallback.py", "vieneu-tts": "vieneu_engine.py",
                   "piper-tts": "piper_engine.py"}[engine_name]
        revision = {"runtime": versions, "adapter": hashlib.sha256(
            (Path(__file__).parent / "engines" / "tts" / adapter).read_bytes()).hexdigest()}
        assets = []
        if engine_name == "piper-tts":
            from core.engines.tts.piper_engine import PIPER_ASSETS, PIPER_REVISION, piper_model_dir
            revision["model"] = PIPER_REVISION
            assets = [piper_model_dir() / name for name, _, _ in PIPER_ASSETS]
        elif engine_name == "vieneu-tts":
            from core.model_manager import ModelManager
            from core.voice_catalog import _preset_file
            record = next(row for row in ModelManager.get_all_models() if row["engine"] == "VieNeu-TTS")
            model = Path(record["path"])
            for root in (model, model.parent / "models--OpenMOSS-Team--MOSS-Audio-Tokenizer-Nano-ONNX"):
                for snapshot in (root / "snapshots").glob("*"):
                    assets.extend(path for folder in (snapshot, snapshot / "onnx_update")
                                  for path in folder.glob("*") if path.is_file())
            preset = _preset_file()
            if preset is not None:
                assets.append(preset)
            revision["model"] = "v3turbo-onnx-fp32"
        revision["assets"] = []
        for path in sorted(assets):
            try:
                stat = path.stat()
                revision["assets"].append((str(path), stat.st_size, stat.st_mtime_ns))
            except FileNotFoundError:
                revision["assets"].append((str(path), "missing"))
        return revision

    def _cache_path(self, engine_name, voice):
        identity = {"schema": self.cache_schema, "engine": engine_name, "voice": voice,
                    "text": SAMPLE_TEXT, "revision": self._engine_revision(engine_name)}
        key = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        return self.cache_dir / f"{key}.wav"

    @classmethod
    def _validated_audio(cls, path):
        """Read the entire PCM payload so a WAV header cannot hide truncation."""
        try:
            if not 44 < path.stat().st_size <= cls.max_sample_bytes:
                return None
            data = path.read_bytes()
            with wave.open(io.BytesIO(data), "rb") as audio:
                channels, width, rate, count = (audio.getnchannels(), audio.getsampwidth(),
                                                audio.getframerate(), audio.getnframes())
                if (audio.getcomptype() != "NONE" or channels not in (1, 2) or width != 2
                        or not 8000 <= rate <= 96000 or not .1 <= count / rate <= 60):
                    return None
                frames = audio.readframes(count)
                if len(frames) != count * channels * width or not any(frames):
                    return None
            return data
        except (OSError, EOFError, wave.Error):
            return None

    def _prune_disk(self, keep):
        entries = []
        for path in self.cache_dir.glob("*.wav"):
            if not re.fullmatch(r"[0-9a-f]{64}\.wav", path.name) or path.is_symlink():
                continue
            try:
                stat = path.stat()
                entries.append((path, stat.st_size, stat.st_mtime_ns))
            except FileNotFoundError:
                continue
        total, count = sum(row[1] for row in entries), len(entries)
        for path, size, _ in sorted(entries, key=lambda row: row[2]):
            if count <= self.max_cached_samples and total <= self.max_cache_bytes:
                break
            if path == keep:
                continue
            try:
                path.unlink(missing_ok=True)
                total, count = total - size, count - 1
            except OSError:
                # A busy Windows media handle must not discard a valid sample.
                continue

    def _cached_audio(self, path):
        with self._cache_lock:
            audio = self._validated_audio(path)
            if audio is not None:
                try:
                    path.touch()
                except OSError:
                    pass
                self._prune_disk(path)
            return audio

    def _register(self, audio, *, cached):
        with self._lock:
            if self._closed:
                raise VoicePreviewBusy("Ứng dụng đang đóng.")
            self._prune()
            key = uuid.uuid4().hex
            self._samples[key] = (time.monotonic(), audio)
        return {"preview_id": key, "status": "READY", "cached": cached,
                "audio_url": f"/api/voices/preview/{key}/audio"}

    def _prune(self):
        now = time.monotonic()
        for key, (created, _) in list(self._samples.items()):
            if now - created >= self.lifetime_seconds:
                del self._samples[key]
        while len(self._samples) >= self.max_samples:
            del self._samples[next(iter(self._samples))]

    def create(self, voice_id):
        with self._lock:
            if self._closed:
                raise VoicePreviewBusy("Ứng dụng đang đóng.")
        engine_name, voice = select_voice(voice_id)
        cached_path = self._cache_path(engine_name, voice)
        audio = self._cached_audio(cached_path)
        if audio is not None:
            return self._register(audio, cached=True)
        if not self._synthesis_lock.acquire(blocking=False):
            raise VoicePreviewBusy("Đang tạo mẫu giọng. Hãy chờ mẫu hiện tại hoàn tất.")
        try:
            with self._lock:
                if self._closed:
                    raise VoicePreviewBusy("Ứng dụng đang đóng.")
            # Another request may have published between our initial miss and
            # acquiring the inference lock. Never synthesize that sample twice.
            audio = self._cached_audio(cached_path)
            if audio is not None:
                return self._register(audio, cached=True)
            if engine_name == "vieneu-tts":
                engine = VieNeuEngine()
            elif engine_name == "piper-tts":
                from core.engines.tts.piper_engine import PiperEngine
                engine = PiperEngine()
            else:
                engine = EdgeTTSFallbackEngine(voice=voice)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="studio-voice-", dir=self.cache_dir) as directory:
                output = Path(directory) / "sample.wav"
                if engine_name == "vieneu-tts":
                    # Preview audio should not populate the persistent segment cache.
                    engine.cache_dir = Path(directory) / "cache"
                engine.synthesize(SAMPLE_TEXT, output, voice=voice)
                audio = self._validated_audio(output)
                if audio is None:
                    raise RuntimeError("Không nhận được mẫu âm thanh hợp lệ. Hãy thử lại.")
                with self._lock:
                    if self._closed:
                        raise VoicePreviewBusy("Ứng dụng đang đóng.")
                    with self._cache_lock:
                        output.replace(cached_path)
                        self._prune_disk(cached_path)
            return self._register(audio, cached=False)
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
