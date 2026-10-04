from pathlib import Path
from typing import List, Dict, Any, Optional
from config import settings
from core.engines.asr.base import ASREngine

class FasterWhisperFallbackEngine(ASREngine):
    """
    Fallback ASR engine using Faster-Whisper.
    Used when SenseVoice is unavailable or requested by user.
    """
    def __init__(self, model_size: Optional[str] = None):
        self.model_size = model_size or settings.WHISPER_MODEL_SIZE
        self.model = None

    @property
    def name(self) -> str:
        return f"Faster-Whisper ({self.model_size}) [Fallback]"

    @property
    def is_available(self) -> bool:
        try:
            import faster_whisper
            return True
        except ImportError:
            return False

    def _ensure_loaded(self):
        if self.model is None:
            from core.asr import load_whisper_model
            self.model = load_whisper_model(self.model_size)

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "engine": "faster-whisper",
            "is_available": self.is_available,
            "is_fallback": True
        }

    def transcribe(
        self,
        audio_path: Path,
        language: str = "zh",
        progress_callback: Optional[callable] = None
    ) -> List[Dict[str, Any]]:
        self._ensure_loaded()
        segments_gen, _ = self.model.transcribe(
            str(audio_path),
            language=language,
            vad_filter=True,
            beam_size=5
        )

        results = []
        for idx, seg in enumerate(segments_gen):
            results.append({
                "id": idx,
                "start": round(seg.start, 2),
                "end": round(seg.end, 2),
                "duration": round(seg.end - seg.start, 2),
                "text_zh": seg.text.strip(),
                "text": seg.text.strip(),
                "emotion": "<|NEUTRAL|>",
                "speaker": None
            })
        return results
