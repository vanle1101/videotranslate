import asyncio
from pathlib import Path
from typing import Dict, Any, Optional
import edge_tts
from core.engines.tts.base import TTSEngine

class EdgeTTSFallbackEngine(TTSEngine):
    """
    Fallback TTS engine using Microsoft Edge-TTS.
    Marked strictly as fallback in UI and reports.
    """
    def __init__(self, voice: str = "vi-VN-HoaiMyNeural"):
        self.voice = voice

    @property
    def name(self) -> str:
        return f"Edge-TTS ({self.voice}) [Fallback]"

    @property
    def is_available(self) -> bool:
        return True

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "engine": "edge-tts",
            "is_available": True,
            "is_fallback": True
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
        chosen_voice = voice or self.voice
        rate_str = f"+{int((speed - 1.0) * 100)}%" if speed >= 1.0 else f"{int((speed - 1.0) * 100)}%"
        
        async def _run():
            com = edge_tts.Communicate(text, chosen_voice, rate=rate_str)
            await com.save(str(output_path))

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import nest_asyncio
                nest_asyncio.apply()
                loop.run_until_complete(_run())
            else:
                loop.run_until_complete(_run())
        except Exception:
            asyncio.run(_run())

        return output_path
