import subprocess
import time
import math
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
from config import settings
from core.engines.alignment.base import AlignmentEngine
from core.engines.tts.base import TTSEngine
from core.engines.alignment.speech_timing import build_speech_timing, take_tts_word_boundaries, trim_tts_padding

class SpeechBudgetError(RuntimeError):
    """Complete speech cannot fit without an unnatural speed change."""


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
            # ffprobe reports durations at sample precision; a value such as
            # 1.1500000000000004 is still the configured 1.15x limit. Treat a
            # sub-millisecond floating point excess as rounding, while keeping
            # a real over-budget sentence on the normal rewrite/error path.
            if fit_duration is not None and actual_speed > self.max_speed + 1e-3:
                raise SpeechBudgetError(
                    f"Lời thoại quá dài để đọc tự nhiên (cần {actual_speed:.2f}x, "
                    f"giới hạn {self.max_speed:.2f}x). Hãy rút gọn lời Việt hoặc thử tạo giọng lại."
                )
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
            # atempo emits complete codec/sample blocks. Near the exact speed
            # ceiling that can add less than a millisecond to the measured
            # container duration; accepting this quantisation avoids asking
            # for an imperceptibly faster (and disallowed) rate.
            if rendered_duration <= fit_duration + 0.001:
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

            from core.engines.alignment.natural_speech import synthesize_natural_speech
            spoken = synthesize_natural_speech(text=text_vi, source=seg.get("text_zh", ""),
                duration=slot_duration, output_path=fitted_wav, engine=tts_engine, aligner=self,
                translator=translation_engine, voice=voice, ref_audio=ref_audio)
            text_vi = spoken["text"]
            tts_dur, speed_ratio, boundaries = spoken["tts_duration"], spoken["speed_ratio"], spoken["boundaries"]

            seg_copy = dict(seg)
            seg_copy["final_vi"] = text_vi
            if spoken["pacing_verification"]:
                seg_copy["verification"] = {**(seg.get("verification") or {}),
                    "pacing": spoken["pacing_verification"], "before_pacing": seg.get("final_vi"),
                    "translation_changed": True}
            seg_copy["target_words"] = int(slot_duration * 3.0)
            seg_copy["tts_duration"] = tts_dur
            seg_copy["speed_ratio"] = round(speed_ratio, 2)
            seg_copy["audio_path"] = str(fitted_wav.resolve())
            seg_copy.update(build_speech_timing(text_vi, seg["start"], seg["end"],
                                               fitted_wav, speed_ratio, boundaries))
            aligned_segments.append(seg_copy)

        return aligned_segments
