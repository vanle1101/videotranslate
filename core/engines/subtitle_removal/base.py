from abc import abstractmethod
from pathlib import Path
from typing import Tuple, Optional
from core.engines.base import BaseEngine

class SubtitleRemovalEngine(BaseEngine):
    """Base class for hardcoded subtitle removal and inpainting."""

    @abstractmethod
    def remove(
        self,
        video_path: Path,
        output_path: Path,
        mode: str = "auto", # 'auto', 'fast', 'ai'
        subtitle_bbox: Optional[Tuple[int, int, int, int]] = None,
        progress_callback: Optional[callable] = None
    ) -> Path:
        """
        Removes or cleanly masks burned-in subtitles from video.
        Modes:
        - 'fast': Smart glassmorphism/gradient overlay on detected bbox
        - 'ai': ProPainter temporal inpainting
        - 'auto': Detects motion and region to decide best method
        """
        pass
