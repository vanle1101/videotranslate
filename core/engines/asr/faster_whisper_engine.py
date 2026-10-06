from pathlib import Path
from typing import List, Dict, Any, Optional
from config import settings
from core.engines.asr.base import ASREngine
from core.dialogue_segments import split_dialogue_segments

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
            vad_parameters={"min_silence_duration_ms": 300},
            beam_size=5,
            word_timestamps=True,
        )

        results = []
        for idx, seg in enumerate(segments_gen):
            words = []
            for word in (getattr(seg, "words", None) or []):
                word_text = getattr(word, "word", "")
                word_start = getattr(word, "start", None)
                word_end = getattr(word, "end", None)
                if not isinstance(word_text, str):
                    continue
                if not isinstance(word_start, (int, float)) or not isinstance(word_end, (int, float)):
                    continue
                words.append({"word": word_text, "start": round(float(word_start), 3),
                              "end": round(float(word_end), 3)})
            results.append({
                "id": idx,
                "start": round(seg.start, 2),
                "end": round(seg.end, 2),
                "duration": round(seg.end - seg.start, 2),
                "text_zh": seg.text.strip(),
                "text": seg.text.strip(),
                "words": words,
                "emotion": "<|NEUTRAL|>",
                "speaker": getattr(seg, "speaker", None)
            })
        # Return complete utterances instead of Whisper's sometimes long VAD
        # chunks.  The helper uses only measured word timestamps and leaves a
        # row intact when Whisper did not provide usable word timing.
        return split_dialogue_segments(results)
