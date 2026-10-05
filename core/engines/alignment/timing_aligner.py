import subprocess
import time
import math
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
from config import settings
from core.engines.alignment.base import AlignmentEngine
from core.engines.tts.base import TTSEngine

class TimingBudgetAligner(AlignmentEngine):
    """
    Time-Budgeting & Speech Alignment Engine.
    Fits complete speech into its allotted slot while preserving pitch.
    Callers can report when a segment needs more than the preferred speed range.
    """
    def __init__(self, speed_limits: Tuple[float, float] = (0.90, 1.15)):
        self.min_speed, self.max_speed = speed_limits
        self.temp_dir = settings.TEMP_DIR / "alignment"
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    @property
    def name(self) -> str:
        return "Timing-Budget-Aligner"

    @property
    def is_available(self) -> bool:
        return True

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "min_speed": self.min_speed,
            "max_speed": self.max_speed,
            "is_available": True
        }

    def get_audio_duration(self, audio_path: Path) -> float:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path)
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding="utf-8", errors="replace", check=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            return float(res.stdout.strip())
        except Exception:
            return 0.0

    def apply_atempo(self, input_wav: Path, output_wav: Path, speed_factor: float,
                     fit_duration: Optional[float] = None):
        """Fit all speech into its slot without silently cutting the last words."""
        if not math.isfinite(speed_factor) or speed_factor <= 0:
            raise ValueError("Tốc độ giọng đọc không hợp lệ.")
        if fit_duration is not None and (not math.isfinite(fit_duration) or fit_duration <= 0):
            raise ValueError("Thời lượng đoạn thoại không hợp lệ.")
        actual_speed = speed_factor if fit_duration is not None else max(self.min_speed, min(self.max_speed, speed_factor))
        for _ in range(4):
            # Chaining values <= 2 avoids atempo dropping samples at high speeds.
            factor = actual_speed
            filters = []
            while factor > 2.0:
                filters.append("atempo=2.0")
                factor /= 2.0
            while factor < 0.5:
                filters.append("atempo=0.5")
                factor /= 0.5
            filters.append(f"atempo={factor:.8f}")
            cmd = [
                "ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(input_wav),
                "-filter:a", ",".join(filters), "-vn", str(output_wav),
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=True)
            if fit_duration is None:
                return actual_speed
            rendered_duration = self.get_audio_duration(output_wav)
            if rendered_duration <= 0:
                raise RuntimeError("Không đọc được âm thanh sau khi căn thời lượng.")
            if rendered_duration <= fit_duration + 0.0001:
                return actual_speed
            # atempo works in blocks, so source_duration / speed is approximate.
            # Re-render the complete original at a slightly faster rate instead
            # of letting playback/export trim the final syllable at the slot end.
            actual_speed *= rendered_duration / fit_duration * 1.01
        raise RuntimeError("Không thể căn đủ lời vào thời lượng đoạn thoại. Hãy rút gọn bản dịch.")

    def align_and_budget(
        self,
        segments: List[Dict[str, Any]],
        tts_engine: TTSEngine,
        translation_engine: Any,
        speed_limits: Tuple[float, float] = (0.90, 1.15),
        voice: Optional[str] = "Trúc Ly",
        ref_audio: Optional[Path] = None
    ) -> List[Dict[str, Any]]:
        self.min_speed, self.max_speed = speed_limits
        aligned_segments = []

        print(f"[*] Executing Timing Alignment for {len(segments)} segments...")

        for seg in segments:
            seg_id = seg["id"]
            slot_duration = seg.get("duration", round(seg["end"] - seg["start"], 2))
            text_vi = seg.get("final_vi", seg.get("vi_text", "")).strip()

            if not text_vi:
                continue

            raw_wav = self.temp_dir / f"seg_{seg_id}_raw.wav"
            fitted_wav = self.temp_dir / f"seg_{seg_id}_fitted.wav"

            # 1. Synthesize with current TTS engine
            tts_engine.synthesize(text_vi, raw_wav, voice=voice, ref_audio=ref_audio)
            tts_dur = self.get_audio_duration(raw_wav)
            speed_ratio = max(self.min_speed, tts_dur / max(0.01, slot_duration))

            print(f"    Segment #{seg_id}: slot={slot_duration:.2f}s, tts={tts_dur:.2f}s, ratio={speed_ratio}x")

            # Do not "shorten" a translation by deleting its final words. That
            # silently changes meaning and can remove negation or an action.
            if speed_ratio > self.max_speed:
                print(f"    [!] Segment #{seg_id} needs {speed_ratio:.2f}x to preserve all spoken words.")

            try:
                speed_ratio = self.apply_atempo(raw_wav, fitted_wav, speed_ratio, fit_duration=slot_duration)
            finally:
                raw_wav.unlink(missing_ok=True)

            seg_copy = dict(seg)
            seg_copy["target_words"] = int(slot_duration * 3.0)
            seg_copy["tts_duration"] = tts_dur
            seg_copy["speed_ratio"] = round(speed_ratio, 2)
            seg_copy["audio_path"] = str(fitted_wav.resolve())
            aligned_segments.append(seg_copy)

        return aligned_segments
