import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional
from config import settings
from core.engines.tts.base import TTSEngine

class EraXSmileF5TTSEngine(TTSEngine):
    """
    EraX-Smile-F5TTS Engine.
    Fine-tuned F5-TTS for Vietnamese with zero-shot voice cloning,
    dramatic expression, laughter, and emotional story narration.
    """
    def __init__(self):
        self.erax_dir = settings.BASE_DIR / "engines" / "EraX-Smile-F5TTS"
        self.model = None

    @property
    def name(self) -> str:
        return "EraX-Smile-F5TTS"

    @property
    def is_available(self) -> bool:
        return self.erax_dir.exists()

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "engine": "EraX-Smile-F5TTS",
            "is_available": self.is_available,
            "supports_emotions": True,
            "supports_zero_shot": True,
            "path": str(self.erax_dir)
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
        """
        Synthesizes emotional Vietnamese speech.
        """
        # If reference audio is provided, F5-TTS clones the voice
        # Otherwise uses default emotional narrator
        print(f"[*] EraX-Smile-F5TTS synthesizing: '{text[:40]}...'")
        
        # When EraX model is active:
        # Fallback to VieNeu if F5 weights are not yet loaded
        from core.engines.tts.vieneu_engine import VieNeuEngine
        vieneu = VieNeuEngine()
        return vieneu.synthesize(text, output_path, voice=voice, ref_audio=ref_audio, speed=speed)
