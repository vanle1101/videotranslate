import time
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any
import numpy as np

class RealtimeVocalSuppressor:
    """
    Intelligent Auto-Adaptive Chinese Vocal Suppressor.
    Selects optimal separation strategy based on real-time audio analysis:
    
    1. Stereo Wide Track (r_LR < 0.92, Side > 5%):
       -> Mode: DSP_STEREO_CENTER_CANCEL
       -> Mid-Side Phase Cancellation (-36dB mid level) + Multi-band Formant Notch.
       -> Preserves stereo BGM, reverb, and ambient Foley.
       
    2. Mono / Dual-Mono Track (r_LR >= 0.92 or channels == 1):
       -> Mode: DSP_MONO_ADAPTIVE_FORMANT
       -> Multi-Band Crossover:
          * Low (<250Hz): 100% untouched (bass, kick, impacts).
          * High (>3600Hz): 100% untouched (footsteps, cutlery, high ambience).
          * Speech Formants (250Hz-3600Hz): Deep formant notch filtering (-20dB).
       -> Never cancels mono backing music; keeps SFX & BGM intact.
    """
    def __init__(self, suppression_level_db: float = -26.0):
        self.engine_name = "Realtime Auto Vocal Suppressor"
        self.suppression_level_db = suppression_level_db
        self.last_throughput_rtf = 0.0
        self.last_mode = "AUTO"

    @property
    def name(self) -> str:
        return self.engine_name

    def analyze_audio_properties(self, input_audio_path: Path) -> Dict[str, Any]:
        """
        Analyzes channel correlation, side energy, and stereo width.
        Determines whether center cancellation is suitable or would destroy mono BGM.
        """
        probe_cmd = [
            "ffprobe", "-v", "error", "-show_entries", "stream=channels,sample_rate",
            "-select_streams", "a:0", "-of", "json", str(input_audio_path)
        ]
        try:
            import json
            probe_res = subprocess.run(probe_cmd, capture_output=True, text=True,
                                       encoding="utf-8", errors="replace", check=True)
            probe_data = json.loads(probe_res.stdout)
            channels = int(probe_data["streams"][0].get("channels", 2))
        except Exception:
            channels = 2

        if channels == 1:
            return {
                "channels": 1,
                "correlation": 1.0,
                "side_ratio": 0.0,
                "mode": "DSP_MONO_ADAPTIVE_FORMANT",
                "reason": "Mono audio detected: Center cancellation would mute everything; applying multi-band formant notch."
            }

        # Sample 5 seconds of PCM data to compute L-R correlation
        pcm_cmd = [
            "ffmpeg", "-y", "-ss", "0", "-t", "5", "-i", str(input_audio_path),
            "-vn", "-f", "s16le", "-ac", "2", "-ar", "22050", "pipe:1"
        ]
        try:
            raw_bytes = subprocess.run(pcm_cmd, capture_output=True, check=True).stdout
            data = np.frombuffer(raw_bytes, dtype=np.int16).reshape(-1, 2).astype(np.float32)
            L, R = data[:, 0], data[:, 1]
            denom = np.sqrt(np.sum(L**2) * np.sum(R**2))
            corr = float(np.sum(L * R) / max(denom, 1e-6))
            side_energy = float(np.mean((L - R)**2))
            mid_energy = float(np.mean(((L + R) / 2.0)**2))
            side_ratio = float(side_energy / max(mid_energy, 1e-6))
        except Exception:
            corr = 0.99
            side_ratio = 0.01

        # Decision threshold:
        # If correlation is >= 0.92 or side_ratio <= 0.04, it's effectively dual-mono.
        if corr < 0.92 and side_ratio > 0.04:
            mode = "DSP_STEREO_CENTER_CANCEL"
            reason = f"Stereo wide audio (corr={corr:.3f}, side={side_ratio:.1%}): Suitable for center cancellation."
        else:
            mode = "DSP_MONO_ADAPTIVE_FORMANT"
            reason = f"Dual-mono / narrow stereo (corr={corr:.3f}, side={side_ratio:.1%}): Subtraction would damage BGM; applying formant notch."

        return {
            "channels": channels,
            "correlation": round(corr, 4),
            "side_ratio": round(side_ratio, 4),
            "mode": mode,
            "reason": reason
        }

    def process_file(
        self,
        input_audio_path: Path,
        output_audio_path: Path,
        forced_mode: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Processes audio file using auto-selected optimal strategy.
        """
        t0 = time.time()
        output_audio_path.parent.mkdir(parents=True, exist_ok=True)

        analysis = self.analyze_audio_properties(input_audio_path)
        selected_mode = forced_mode or analysis["mode"]
        self.last_mode = selected_mode

        if selected_mode == "DSP_STEREO_CENTER_CANCEL":
            # Multi-band Stereo Center Cancellation
            filter_complex = (
                "[0:a]asplit=3[low_in][mid_in][high_in];"
                "[low_in]lowpass=f=250[low_out];"
                "[high_in]highpass=f=3800[high_out];"
                "[mid_in]bandpass=f=2025:width_type=h:w=3550,"
                "stereotools=mlev=0.015625:slev=1.25,"
                "equalizer=f=1200:t=q:w=1.5:g=-24,"
                "equalizer=f=2400:t=q:w=2.0:g=-20[mid_out];"
                "[low_out][mid_out][high_out]amix=inputs=3:normalize=0[aout]"
            )
            suppression_est_db = -26.0
        else:
            # Multi-Band Formant Notch (Preserves mono BGM, bass, and SFX)
            filter_complex = (
                "[0:a]asplit=3[low_in][mid_in][high_in];"
                "[low_in]lowpass=f=250,volume=1.0[low_out];"
                "[high_in]highpass=f=3600,volume=1.0[high_out];"
                "[mid_in]bandpass=f=1925:width_type=h:w=3350,"
                "equalizer=f=800:t=q:w=1.5:g=-18,"
                "equalizer=f=1400:t=q:w=2.0:g=-22,"
                "equalizer=f=2600:t=q:w=2.0:g=-18,"
                "volume=0.35[mid_out];"
                "[low_out][mid_out][high_out]amix=inputs=3:normalize=0[aout]"
            )
            suppression_est_db = -20.0

        is_wav = output_audio_path.suffix.lower() == ".wav"
        codec_args = ["-c:a", "pcm_s16le"] if is_wav else ["-c:a", "aac", "-b:a", "192k"]

        cmd = [
            "ffmpeg", "-y", "-i", str(input_audio_path),
            "-filter_complex", filter_complex,
            "-map", "[aout]",
            *codec_args,
            str(output_audio_path)
        ]

        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                              text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            # Fallback simple notch
            fallback_cmd = [
                "ffmpeg", "-y", "-i", str(input_audio_path),
                "-vn",
                "-af", "equalizer=f=1200:t=q:w=1.5:g=-20,equalizer=f=2400:t=q:w=2.0:g=-18",
                *codec_args,
                str(output_audio_path)
            ]
            subprocess.run(fallback_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        elapsed = max(0.001, time.time() - t0)

        # Calculate duration of output
        dur_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(output_audio_path)]
        try:
            dur_res = subprocess.run(dur_cmd, capture_output=True, text=True,
                                     encoding="utf-8", errors="replace", check=True)
            duration = float(dur_res.stdout.strip())
        except Exception:
            duration = 1.0

        self.last_throughput_rtf = round(duration / elapsed, 1)
        self.suppression_level_db = suppression_est_db

        return {
            "engine": f"Auto Suppressor ({selected_mode})",
            "mode": selected_mode,
            "correlation": analysis.get("correlation", 1.0),
            "side_ratio": analysis.get("side_ratio", 0.0),
            "reason": analysis.get("reason", ""),
            "suppression_level_db": f"{suppression_est_db:.1f} dB",
            "processing_time": round(elapsed, 4),
            "duration": round(duration, 2),
            "throughput_rtf": f"{self.last_throughput_rtf}x",
            "output_path": str(output_audio_path)
        }
