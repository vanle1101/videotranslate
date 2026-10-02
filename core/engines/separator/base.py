from abc import abstractmethod
from pathlib import Path
from typing import Tuple, Dict, Any, Optional
from core.engines.base import BaseEngine

class SeparatorEngine(BaseEngine):
    """Base class for Vocal and BGM/SFX audio separation."""

    @abstractmethod
    def separate(
        self,
        audio_path: Path,
        output_dir: Path,
        progress_callback: Optional[callable] = None
    ) -> Tuple[Path, Path]:
        """
        Separates audio into:
        (vocals_path, instrumental_path)
        Instrumental MUST preserve music, footsteps, ambience, impact effects.
        """
        pass
