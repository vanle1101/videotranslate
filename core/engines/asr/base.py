from abc import abstractmethod
from pathlib import Path
from typing import List, Dict, Any, Optional
from core.engines.base import BaseEngine

class ASREngine(BaseEngine):
    """Base class for Speech-to-Text engines."""

    @abstractmethod
    def transcribe(
        self,
        audio_path: Path,
        language: str = "zh",
        progress_callback: Optional[callable] = None
    ) -> List[Dict[str, Any]]:
        """
        Transcribes audio into standardized internal segments:
        [
            {
                "id": 0,
                "start": 1.24,
                "end": 3.82,
                "duration": 2.58,
                "text_zh": "...",
                "emotion": "...",
                "speaker": None
            }
        ]
        """
        pass
