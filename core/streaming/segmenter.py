import re
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Tuple

class AudioSegmenter:
    """
    Fast VAD and Silence-based Audio Segmenter.
    Discovers natural sentence boundaries without dumb fixed-time slicing.
    Runs at 70x-100x realtime using FFmpeg silencedetect.
    """
    def __init__(self, min_silence_duration: float = 0.25, noise_threshold_db: float = -28.0):
        self.min_silence_duration = min_silence_duration
        self.noise_threshold_db = noise_threshold_db

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

    def detect_silence_intervals(self, audio_path: Path) -> List[Tuple[float, float]]:
        """Finds (silence_start, silence_end) timestamps."""
        cmd = [
            "ffmpeg", "-y", "-i", str(audio_path),
            "-af", f"silencedetect=noise={self.noise_threshold_db}dB:d={self.min_silence_duration}",
            "-f", "null", "-"
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        output = res.stderr

        silences = []
        cur_start = None

        for line in output.splitlines():
            if "silence_start:" in line:
                m = re.search(r"silence_start:\s*([\d\.]+)", line)
                if m:
                    cur_start = float(m.group(1))
            elif "silence_end:" in line:
                m = re.search(r"silence_end:\s*([\d\.]+)", line)
                if m:
                    end_val = float(m.group(1))
                    start_val = cur_start if cur_start is not None else 0.0
                    silences.append((start_val, end_val))
                    cur_start = None

        return silences

    def segment_audio(self, audio_path: Path, max_segment_duration: float = 8.0, min_segment_duration: float = 1.2) -> List[Dict[str, Any]]:
        """
        Derives speech segments from non-silent intervals.
        Enforces natural sentence lengths:
        - Merges tiny phrases (< min_segment_duration) with adjacent speech if gap is small.
        - Splits overly long continuous monologues (> max_segment_duration) safely.
        """
        total_duration = self.get_audio_duration(audio_path)
        if total_duration <= 0:
            return []

        silences = self.detect_silence_intervals(audio_path)

        # Invert silences to find speech intervals
        speech_intervals = []
        cur_pos = 0.0

        for s_start, s_end in silences:
            if s_start > cur_pos:
                speech_intervals.append((round(cur_pos, 2), round(s_start, 2)))
            cur_pos = max(cur_pos, s_end)

        if cur_pos < total_duration:
            speech_intervals.append((round(cur_pos, 2), round(total_duration, 2)))

        if not speech_intervals:
            # Fallback: if no silence detected (e.g. loud continuous audio)
            speech_intervals = [(0.0, round(total_duration, 2))]

        # Merge short fragments (< min_segment_duration) into adjacent segment
        merged = []
        for start, end in speech_intervals:
            dur = end - start
            if dur < 0.2:
                continue # ignore microscopic clicks

            if merged and (dur < min_segment_duration or (start - merged[-1][1]) < 0.25):
                prev_start, prev_end = merged[-1]
                if (end - prev_start) <= max_segment_duration:
                    merged[-1] = (prev_start, end)
                    continue

            merged.append((start, end))

        # Split segments that exceed max_segment_duration
        final_segments = []
        seg_id = 0

        for start, end in merged:
            dur = end - start
            if dur > max_segment_duration:
                # Split evenly
                sub_count = int(dur / max_segment_duration) + 1
                step = dur / sub_count
                for i in range(sub_count):
                    s = round(start + i * step, 2)
                    e = round(min(end, start + (i + 1) * step), 2)
                    final_segments.append({
                        "id": seg_id,
                        "start": s,
                        "end": e,
                        "duration": round(e - s, 2),
                        "status": "WAITING"
                    })
                    seg_id += 1
            else:
                final_segments.append({
                    "id": seg_id,
                    "start": round(start, 2),
                    "end": round(end, 2),
                    "duration": round(dur, 2),
                    "status": "WAITING"
                })
                seg_id += 1

        return final_segments
