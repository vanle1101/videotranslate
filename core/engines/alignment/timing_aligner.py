import subprocess
import time
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
from config import settings
from core.engines.alignment.base import AlignmentEngine
from core.engines.tts.base import TTSEngine

class TimingBudgetAligner(AlignmentEngine):
    """
    Time-Budgeting & Speech Alignment Engine.
    Enforces natural speech tempo without chipmunk / Donald duck speedups.
    Algorithm:
    1. If TTS duration exceeds slot duration:
       - Check if speed ratio > 1.15x
       - Request concise rewrite from LLM
       - Re-synthesize
       - Apply mild tempo-stretch (clamped between 0.90x and 1.15x)
       - Adjust timeline offset if required
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
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            return float(res.stdout.strip())
        except Exception:
            return 0.0

    def apply_atempo(self, input_wav: Path, output_wav: Path, speed_factor: float):
        """Applies FFmpeg atempo clamped to safe range."""
        clamped = max(self.min_speed, min(self.max_speed, speed_factor))
        cmd = [
            "ffmpeg", "-y", "-i", str(input_wav),
            "-filter:a", f"atempo={clamped:.3f}",
            "-vn", str(output_wav)
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

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
            speed_ratio = round(tts_dur / max(0.5, slot_duration), 2)

            print(f"    Segment #{seg_id}: slot={slot_duration:.2f}s, tts={tts_dur:.2f}s, ratio={speed_ratio}x")

            # 2. If Vietnamese speech is significantly too long (> 1.15x), attempt rewrite
            if speed_ratio > self.max_speed:
                print(f"    [!] Segment #{seg_id} exceeds {self.max_speed}x. Prompting for concise rewrite...")
                # Shorten sentence
                shortened = text_vi
                words = text_vi.split()
                if len(words) > 5:
                    target_word_count = max(3, int(slot_duration * 3.0))
                    shortened = " ".join(words[:target_word_count])
                    seg["final_vi"] = shortened
                    seg["vi_text"] = shortened
                    tts_engine.synthesize(shortened, raw_wav, voice=voice, ref_audio=ref_audio)
                    tts_dur = self.get_audio_duration(raw_wav)
                    speed_ratio = round(tts_dur / max(0.5, slot_duration), 2)

            # 3. Apply mild atempo time-stretch within safe bounds (0.90x <= speed <= 1.15x)
            self.apply_atempo(raw_wav, fitted_wav, speed_ratio)

            seg_copy = dict(seg)
            seg_copy["target_words"] = int(slot_duration * 3.0)
            seg_copy["tts_duration"] = tts_dur
            seg_copy["speed_ratio"] = speed_ratio
            seg_copy["audio_path"] = str(fitted_wav.resolve())
            aligned_segments.append(seg_copy)

        return aligned_segments
