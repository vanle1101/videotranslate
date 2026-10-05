import hashlib
import time
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from config import settings
from core.engines.tts.base import TTSEngine

class VieNeuEngine(TTSEngine):
    """
    Production VieNeu-TTS v3 Turbo Engine.
    Direct integration with pnnbao97/VieNeu-TTS.
    Supports preset voices and 3-5s zero-shot voice cloning.
    """
    def __init__(self):
        self.model = None
        self._preset_voices = None
        self.cache_dir = settings.TEMP_DIR / "vieneu_cache"

    @property
    def name(self) -> str:
        return "VieNeu-TTS-v3-Turbo"

    @property
    def is_available(self) -> bool:
        from core.model_manager import ModelManager
        record = next(item for item in ModelManager.get_all_models() if item["engine"] == "VieNeu-TTS")
        return record["runtime_available"]

    def _ensure_loaded(self):
        if self.model is None:
            print("[*] Initializing VieNeu-TTS v3 Turbo...")
            from vieneu import Vieneu
            # These are the CPU/ONNX checkpoints shown in Models. Do not let
            # installing Torch silently switch the SDK to another model/backend.
            self.model = Vieneu(device="cpu", backend="onnx", threads=4)
            try:
                self._preset_voices = self.model.list_preset_voices()
            except Exception:
                self._preset_voices = []

    def get_info(self) -> Dict[str, Any]:
        from core.model_manager import ModelManager
        record = next(item for item in ModelManager.get_all_models() if item["engine"] == "VieNeu-TTS")
        return {
            "name": self.name,
            "version": "v3-Turbo",
            "is_available": self.is_available,
            "model_path": record["path"],
            "supports_cloning": True,
            "sample_rate": 48000
        }

    def list_preset_voices(self) -> List[Tuple[str, str]]:
        self._ensure_loaded()
        return self._preset_voices or []

    def synthesize(
        self,
        text: str,
        output_path: Path,
        voice: Optional[str] = "Trúc Ly",
        ref_audio: Optional[Path] = None,
        speed: float = 1.0,
        progress_callback: Optional[callable] = None
    ) -> Path:
        """
        Synthesizes Vietnamese speech using VieNeu-TTS v3 Turbo.
        Uses caching to avoid redundant inferences.
        """
        self._ensure_loaded()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        clean_text = text.strip()
        if not clean_text:
            return output_path

        # Cache key based on text and voice/ref_audio
        cache_key = hashlib.md5(f"{clean_text}_{voice}_{ref_audio}_{speed}".encode('utf-8')).hexdigest()
        cached_file = self.cache_dir / f"{cache_key}.wav"

        if cached_file.exists() and cached_file.stat().st_size > 1000:
            import shutil
            shutil.copyfile(cached_file, output_path)
            return output_path

        t0 = time.time()
        # Voice cloning if ref_audio provided
        if ref_audio and Path(ref_audio).exists():
            print(f"[*] VieNeu-TTS cloning voice from: {ref_audio.name}")
            audio = self.model.infer(clean_text, ref_audio=str(ref_audio))
        else:
            chosen_voice = voice or "Trúc Ly"
            audio = self.model.infer(clean_text, voice=chosen_voice)

        self.model.save(audio, str(cached_file))
        import shutil
        shutil.copyfile(cached_file, output_path)

        dur = time.time() - t0
        print(f"[+] VieNeu-TTS synthesized ({dur:.2f}s): {output_path.name}")
        return output_path
