"""VieNeu v3 Turbo presets on CPU, shared safely by previews and video tasks."""

import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
from typing import Any, Dict, List, Optional, Tuple
import wave

from config import settings
from core.engines.tts.base import TTSEngine
from core.voice_catalog import _vieneu_presets, resolve_voice


def _create_local_model():
    """Adapt SDK 3.8's ONNX constructor to cached directories only.

    Its high-level constructor does not forward ``codec_dir`` and unconditionally
    checks Hugging Face. Keep its voice handling, text normalization and synthesis
    methods, while constructing the same CPU runtime from local assets. No global
    environment, HF client or package code is changed.
    """
    from collections import OrderedDict
    from core.model_manager import ModelManager
    from vieneu.base import BaseVieneuTTS
    from vieneu.v3turbo import V3TurboVieNeuTTS
    from vieneu._v3_turbo_engine.onnx_runtime_lite import OnnxV3LiteEngine, _GRAPH_FILES, _CODEC_FILES
    from vieneu_utils.core_utils import BABBLE_MAX_RETRIES

    record = next(item for item in ModelManager.get_all_models() if item["engine"] == "VieNeu-TTS")
    model_cache = Path(record["path"])
    codec_cache = model_cache.parent / "models--OpenMOSS-Team--MOSS-Audio-Tokenizer-Nano-ONNX"

    def complete_snapshot(cache, files):
        for candidate in (cache / "snapshots").glob("*"):
            if all((candidate / file).is_file() and (candidate / file).stat().st_size > 0 for file in files):
                return candidate
        raise RuntimeError("Chưa có đủ tệp VieNeu để đọc ngoại tuyến. Kiểm tra mục Models.")

    model_dir = complete_snapshot(model_cache, [f"onnx_update/{file}" for file in _GRAPH_FILES]
                                  + ["speaker_encoder.onnx", "denoiser.onnx"])
    codec_dir = complete_snapshot(codec_cache, _CODEC_FILES)

    class LocalV3Turbo(V3TurboVieNeuTTS):
        def __init__(self):
            BaseVieneuTTS.__init__(self)
            self.sample_rate = 48000
            self.babble_retries = BABBLE_MAX_RETRIES
            self.engine = OnnxV3LiteEngine(
                checkpoint_path=str(model_dir), onnx_dir=str(model_dir / "onnx_update"),
                codec_dir=str(codec_dir), threads=4,
            )
            self.engine.babble_retries = self.babble_retries
            self.backend = "onnx"
            self.default_style = "tu_nhien"
            self._preset_voices = {}
            self._voice_aliases = {}
            self._default_voice = None
            self._ref_cache = OrderedDict()
            self.backbone_repo = str(model_dir)
            self._load_v3_voices()
            self.max_batch_size = 1
            self.max_streams = 1
            self._batch_engine = None
            self._stream_sched = None
            self._stream_lock = threading.Lock()

    return LocalV3Turbo()


class VieNeuEngine(TTSEngine):
    # One model and one inference at a time across pipeline / preview instances.
    _shared_model = None
    _lock = threading.RLock()
    _cache_limit_bytes = 64 * 1024 * 1024

    def __init__(self):
        self.model = None
        self.cache_dir = settings.TEMP_DIR / "vieneu_cache"

    @property
    def name(self) -> str:
        return "VieNeu-TTS-v3-Turbo"

    @property
    def is_available(self) -> bool:
        from core.voice_catalog import _vieneu_available
        return _vieneu_available()

    def _ensure_loaded(self):
        with self._lock:
            if self.model is not None:
                return
            if VieNeuEngine._shared_model is None:
                if not self.is_available:
                    raise RuntimeError("Chưa có đủ model hoặc thư viện VieNeu. Kiểm tra mục Models.")
                VieNeuEngine._shared_model = _create_local_model()
            self.model = VieNeuEngine._shared_model

    def get_info(self) -> Dict[str, Any]:
        from core.model_manager import ModelManager
        record = next(item for item in ModelManager.get_all_models() if item["engine"] == "VieNeu-TTS")
        return {
            "name": self.name, "version": "v3-Turbo", "is_available": self.is_available,
            "model_path": record["path"], "supports_cloning": True, "sample_rate": 48000,
        }

    def list_preset_voices(self) -> List[Tuple[str, str]]:
        # A dropdown must never allocate a model or make an HF request.
        return [(f"{item['name']} · {item['description']}", item["name"])
                for item in _vieneu_presets()]

    @staticmethod
    def _valid_wav(path: Path) -> bool:
        try:
            with wave.open(str(path), "rb") as audio:
                return audio.getnframes() > 0 and audio.getframerate() > 0
        except (OSError, EOFError, wave.Error):
            return False

    def _prune_cache(self, keep: Path):
        files = [file for file in self.cache_dir.glob("*.wav")
                 if re.fullmatch(r"[0-9a-f]{64}\.wav", file.name)]
        sizes = {file: file.stat().st_size for file in files}
        total = sum(sizes.values())
        for file in sorted(files, key=lambda path: path.stat().st_mtime):
            if total <= self._cache_limit_bytes:
                break
            if file != keep:
                file.unlink(missing_ok=True)
                total -= sizes[file]

    def synthesize(
        self, text: str, output_path: Path, voice: Optional[str] = None,
        ref_audio: Optional[Path] = None, speed: float = 1.0,
        progress_callback: Optional[callable] = None,
    ) -> Path:
        clean_text = text.strip()
        if not clean_text:
            raise ValueError("Không có nội dung tiếng Việt để đọc.")
        if not math.isfinite(speed) or not 0.5 <= speed <= 2.0:
            raise ValueError("Tốc độ giọng đọc phải từ 0,5 đến 2 lần.")
        _, chosen_voice = resolve_voice(voice, "vieneu-tts")
        reference_digest = None
        if ref_audio is not None:
            ref_audio = Path(ref_audio)
            if not ref_audio.is_file():
                raise ValueError("Không tìm thấy âm thanh mẫu giọng đọc.")
            digest = hashlib.sha256()
            with ref_audio.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            reference_digest = digest.hexdigest()
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cache_key = hashlib.sha256(json.dumps({
            "model": "v3turbo-onnx-fp32-v1", "text": clean_text,
            "voice": chosen_voice, "reference": reference_digest, "speed": speed,
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

        with self._lock:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cached_file = self.cache_dir / f"{cache_key}.wav"
            if not self._valid_wav(cached_file):
                self._ensure_loaded()
                with tempfile.TemporaryDirectory(prefix="synthesis_", dir=self.cache_dir) as temp_dir:
                    generated = Path(temp_dir) / "speech.wav"
                    if ref_audio is not None:
                        audio = self.model.infer(clean_text, ref_audio=str(ref_audio))
                    else:
                        # v3 Turbo takes a preset NAME, never an Edge voice ID.
                        audio = self.model.infer(clean_text, voice=chosen_voice)
                    self.model.save(audio, str(generated))
                    if speed != 1.0:
                        adjusted = Path(temp_dir) / "adjusted.wav"
                        subprocess.run([
                            "ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(generated),
                            "-filter:a", f"atempo={speed:.8f}", str(adjusted),
                        ], capture_output=True, check=True, timeout=60,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                        generated = adjusted
                    if not self._valid_wav(generated):
                        raise RuntimeError("VieNeu chưa tạo được âm thanh. Hãy thử lại hoặc đổi giọng.")
                    generated.replace(cached_file)
            if output_path.suffix.lower() == ".wav":
                if output_path.resolve() != cached_file.resolve():
                    shutil.copyfile(cached_file, output_path)
            else:
                subprocess.run([
                    "ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(cached_file),
                    str(output_path),
                ], capture_output=True, check=True, timeout=60,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self._prune_cache(cached_file)
        if progress_callback:
            progress_callback(100)
        return output_path
