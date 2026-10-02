import asyncio
import os
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional
import edge_tts
from config import settings

class VietnameseTTS:
    def __init__(self, voice: Optional[str] = None):
        self.voice = voice or settings.EDGE_VOICE
        self.temp_dir = settings.TEMP_DIR
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    async def generate_speech_segment(self, text: str, output_path: Path, voice: Optional[str] = None) -> Path:
        """Generate speech for a single text segment using edge-tts."""
        chosen_voice = voice or self.voice
        communicate = edge_tts.Communicate(text, chosen_voice, rate="+0%", volume="+0%")
        await communicate.save(str(output_path))
        return output_path

    def get_audio_duration(self, audio_path: Path) -> float:
        """Get precise duration of an audio file in seconds via ffprobe."""
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

    def adjust_tempo(self, input_wav: Path, output_wav: Path, speed_factor: float):
        """
        Adjust tempo of speech without changing pitch using FFmpeg atempo.
        Clamped between 0.85 and 1.3 to keep voice natural.
        """
        clamped_factor = max(0.85, min(1.30, speed_factor))
        cmd = [
            "ffmpeg", "-y", "-i", str(input_wav),
            "-filter:a", f"atempo={clamped_factor:.3f}",
            "-vn", str(output_wav)
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    async def generate_full_dubbing(
        self,
        segments: List[Dict[str, Any]],
        total_duration: float,
        task_id: str
    ) -> Path:
        """
        Generates Vietnamese TTS for each segment, adjusts tempo to match
        the original Chinese timing, and places each piece precisely on the timeline.
        Returns path to the complete voiceover WAV file.
        """
        task_dir = self.temp_dir / task_id / "tts"
        task_dir.mkdir(parents=True, exist_ok=True)

        final_voice_wav = self.temp_dir / task_id / "vietnamese_voice.wav"
        segment_wavs = []

        print(f"[*] Generating Vietnamese TTS for {len(segments)} segments...")

        for seg in segments:
            seg_id = seg["id"]
            vi_text = seg.get("vi_text", "").strip()
            if not vi_text:
                continue

            target_duration = max(0.5, seg["end"] - seg["start"])
            raw_seg_path = task_dir / f"seg_{seg_id}_raw.mp3"
            fitted_seg_path = task_dir / f"seg_{seg_id}_fitted.wav"

            # 1. Generate speech with edge-tts
            try:
                await self.generate_speech_segment(vi_text, raw_seg_path)
                raw_duration = self.get_audio_duration(raw_seg_path)

                # 2. Time-stretch if necessary
                if raw_duration > 0 and raw_duration > target_duration:
                    speed = raw_duration / target_duration
                    self.adjust_tempo(raw_seg_path, fitted_seg_path, speed)
                else:
                    # Convert to wav without speed change
                    cmd = ["ffmpeg", "-y", "-i", str(raw_seg_path), str(fitted_seg_path)]
                    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

                segment_wavs.append({
                    "start": seg["start"],
                    "path": fitted_seg_path
                })
            except Exception as e:
                print(f"[!] Error generating TTS for segment {seg_id}: {e}")

        # 3. Assemble all segments into one continuous track matching the video timeline
        print(f"[*] Assembling speech timeline (total duration: {total_duration:.1f}s)...")
        self._assemble_timeline(segment_wavs, total_duration, final_voice_wav)
        return final_voice_wav

    def _assemble_timeline(self, segment_wavs: List[Dict[str, Any]], total_duration: float, output_path: Path):
        """
        Uses FFmpeg adelay and amix or an audio filter graph to position each segment
        at its exact millisecond offset.
        """
        if not segment_wavs:
            # Create silent audio
            cmd = [
                "ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo",
                "-t", str(total_duration), str(output_path)
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            return

        # Build FFmpeg complex filter with adelay
        # Format: [0:a]adelay=delay_ms|delay_ms[a0]; ... [a0][a1]...amix=inputs=N:dropout_transition=0
        inputs = []
        filter_parts = []
        mix_inputs = []

        for idx, item in enumerate(segment_wavs):
            inputs.extend(["-i", str(item["path"])])
            delay_ms = int(item["start"] * 1000)
            filter_parts.append(f"[{idx}:a]adelay={delay_ms}|{delay_ms}[a{idx}]")
            mix_inputs.append(f"[a{idx}]")

        mix_str = "".join(mix_inputs) + f"amix=inputs={len(segment_wavs)}:dropout_transition=0:normalize=0[mixed]"
        # Pad to total duration with apad / atrim
        filter_complex = f"{';'.join(filter_parts)};{mix_str};[mixed]atrim=0:{total_duration},apad=whole_dur={total_duration}[out]"

        cmd = [
            "ffmpeg", "-y"
        ] + inputs + [
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-ac", "2", "-ar", "44100",
            str(output_path)
        ]

        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            print(f"[!] Warning FFmpeg assembly failed: {res.stderr.decode('utf-8', errors='ignore')[:300]}")
            # Fallback simple concat
            cmd_fallback = [
                "ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo",
                "-t", str(total_duration), str(output_path)
            ]
            subprocess.run(cmd_fallback, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
