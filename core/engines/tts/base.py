from abc import abstractmethod
from pathlib import Path
from typing import Dict, Any, Optional
from core.engines.base import BaseEngine

class TTSEngine(BaseEngine):
    """Base class for Vietnamese Text-to-Speech & Voice Cloning."""

    @abstractmethod
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
        Synthesizes text into high quality audio WAV.
        Supports preset voice or reference audio voice cloning.
        """
        pass
