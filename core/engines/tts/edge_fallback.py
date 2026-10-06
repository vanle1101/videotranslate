import asyncio
import json
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Any, Optional
import edge_tts
from config import settings
from core.engines.tts.base import TTSEngine

class EdgeTTSFallbackEngine(TTSEngine):
    """
    Fallback TTS engine using Microsoft Edge-TTS.
    Marked strictly as fallback in UI and reports.
    """
    def __init__(self, voice: Optional[str] = None):
        self.voice = voice or settings.EDGE_VOICE
        # Kept per output path so streaming can align captions to the exact
        # speech that was just synthesized. The metadata is never persisted in
        # user media or sent anywhere.
        self._word_boundaries = {}

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
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._word_boundaries.pop(str(output_path.resolve()), None)
        if not text.strip():
            raise ValueError("Không có nội dung tiếng Việt để đọc.")
        # VieNeu preset names are not valid Microsoft voice IDs.
        chosen_voice = voice if voice and voice.startswith("vi-VN-") else self.voice
        rate_str = f"+{int((speed - 1.0) * 100)}%" if speed >= 1.0 else f"{int((speed - 1.0) * 100)}%"
        # Edge returns MP3 bytes. Convert to real PCM when a WAV is requested.
        with tempfile.TemporaryDirectory(prefix="edge_tts_", dir=output_path.parent) as temp_dir:
            mp3_path = Path(temp_dir) / "speech.mp3"
            metadata_path = Path(temp_dir) / "boundaries.jsonl"
            boundaries = []

            async def _run():
                # The service sometimes ends a valid request without audio. A
                # Communicate stream is single-use, so retry with a new instance
                # and discard any partial file before requesting the same voice.
                for attempt in range(3):
                    try:
                        boundaries.clear()
                        com = edge_tts.Communicate(text, chosen_voice, rate=rate_str,
                                                   boundary="WordBoundary")
                        await com.save(str(mp3_path), str(metadata_path))
                        if metadata_path.is_file():
                            try:
                                for line in metadata_path.read_text(encoding="utf-8").splitlines():
                                    message = json.loads(line)
                                    if message.get("type") == "WordBoundary":
                                        boundaries.append({
                                            "text": message.get("text", ""),
                                            "start": float(message.get("offset", 0)) / 10_000_000,
                                            "end": (float(message.get("offset", 0)) + float(message.get("duration", 0))) / 10_000_000,
                                        })
                            except (ValueError, TypeError, AttributeError):
                                # Valid speech remains usable if metadata is
                                # malformed; downstream exposes its fallback.
                                boundaries.clear()
                        return
                    except edge_tts.exceptions.NoAudioReceived:
                        mp3_path.unlink(missing_ok=True)
                        metadata_path.unlink(missing_ok=True)
                        if attempt == 2:
                            raise RuntimeError(
                                "Edge-TTS chưa trả về âm thanh sau 3 lần thử. "
                                "Hãy thử lại hoặc chọn giọng đọc khác."
                            ) from None
                        await asyncio.sleep(0.5 * (attempt + 1))

            try:
                asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(_run())
            else:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    executor.submit(lambda: asyncio.run(_run())).result()

            if output_path.suffix.lower() == ".mp3":
                mp3_path.replace(output_path)
            else:
                result = subprocess.run(
                    ["ffmpeg", "-y", "-i", str(mp3_path), "-vn", "-ac", "1", "-ar", "24000", str(output_path)],
                    capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if result.returncode:
                    raise RuntimeError(result.stderr.decode("utf-8", errors="replace")[-1500:])
        self._word_boundaries[str(output_path.resolve())] = boundaries
        while len(self._word_boundaries) > 64:
            self._word_boundaries.pop(next(iter(self._word_boundaries)))
        return output_path

    def take_word_boundaries(self, path: Path):
        """Return and consume boundaries for a completed synthesis."""
        return self._word_boundaries.pop(str(Path(path).resolve()), [])
