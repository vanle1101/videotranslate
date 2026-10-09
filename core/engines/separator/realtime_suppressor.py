import time
import json
import math
from pathlib import Path
from typing import Optional, List, Dict, Any
import numpy as np
from core.media_process import run_media

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
    def __init__(self, suppression_level_db: float = -26.0, analysis_timeout: float = 30.0):
        if not math.isfinite(analysis_timeout) or analysis_timeout <= 0:
            raise ValueError("analysis_timeout must be a positive, finite number")
        self.engine_name = "Realtime Auto Vocal Suppressor"
        self.suppression_level_db = suppression_level_db
        self.last_throughput_rtf = 0.0
        self.last_mode = "AUTO"
        self.analysis_timeout = float(analysis_timeout)

    @property
    def name(self) -> str:
        return self.engine_name

    def _run_analysis_command(self, command, cancel_check=None):
        """Bound short probes/samples and actually kill the child on timeout."""
        deadline = time.monotonic() + self.analysis_timeout
        expired, user_cancelled = False, False

        def cancelled():
            nonlocal expired, user_cancelled
            if cancel_check and cancel_check():
                user_cancelled = True
                return True
            if time.monotonic() >= deadline:
                expired = True
                return True
            return False

        try:
            return run_media(command, cancel_check=cancelled, capture_output=True)
        except RuntimeError as exc:
            if expired and not user_cancelled:
                raise TimeoutError(
                    f"Phân tích âm thanh quá hạn {self.analysis_timeout:g}s; có thể thử lại bước này.") from exc
            raise

    def analyze_audio_properties(self, input_audio_path: Path, cancel_check=None) -> Dict[str, Any]:
        """
        Analyzes channel correlation, side energy, and stereo width.
        Determines whether center cancellation is suitable or would destroy mono BGM.
        """
        probe_cmd = [
            "ffprobe", "-v", "error", "-show_entries", "stream=channels,sample_rate",
            "-select_streams", "a:0", "-of", "json", str(input_audio_path)
        ]
        try:
            probe_data = json.loads(self._run_analysis_command(probe_cmd, cancel_check))
            stream = probe_data["streams"][0]
            channels = stream["channels"]
            raw_rate = stream["sample_rate"]
            if (type(raw_rate) is not int
                    and not (isinstance(raw_rate, str) and raw_rate.isascii() and raw_rate.isdecimal())):
                raise ValueError("invalid audio sample rate type")
            sample_rate = int(raw_rate)
            if type(channels) is not int or not 1 <= channels <= 64 or sample_rate <= 0:
                raise ValueError("invalid audio channels/sample rate")
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise ValueError("Không đọc được số kênh/tần số âm thanh; không tự giả định nguồn stereo.") from exc

        if channels == 1:
            return {
                "channels": 1,
                "sample_rate": sample_rate,
                "correlation": 1.0,
                "side_ratio": 0.0,
                "mode": "DSP_MONO_ADAPTIVE_FORMANT",
                "reason": "Mono audio detected: Center cancellation would mute everything; applying multi-band formant notch."
            }

        # Sample 5 seconds of PCM data to compute L-R correlation
        pcm_cmd = [
            "ffmpeg", "-v", "error", "-nostdin", "-ss", "0", "-t", "5", "-i", str(input_audio_path),
            "-vn", "-f", "s16le", "-ac", "2", "-ar", "22050", "pipe:1"
        ]
        raw_bytes = self._run_analysis_command(pcm_cmd, cancel_check)
        if not raw_bytes or len(raw_bytes) % 4 or len(raw_bytes) > 5 * 22050 * 4:
            raise ValueError("Không giải mã được mẫu PCM stereo hợp lệ để đo âm thanh.")
        data = np.frombuffer(raw_bytes, dtype="<i2").reshape(-1, 2).astype(np.float32)
        L, R = data[:, 0], data[:, 1]
        denom = np.sqrt(np.sum(L**2) * np.sum(R**2))
        corr = float(np.sum(L * R) / max(denom, 1e-6))
        side_energy = float(np.mean((L - R)**2))
        mid_energy = float(np.mean(((L + R) / 2.0)**2))
        side_ratio = float(side_energy / max(mid_energy, 1e-6))
        if not math.isfinite(corr) or not math.isfinite(side_ratio):
            raise ValueError("Phép đo âm thanh không hữu hạn; không tự chọn chế độ giảm giọng.")

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
            "sample_rate": sample_rate,
            "correlation": round(corr, 4),
            "side_ratio": round(side_ratio, 4),
            "mode": mode,
            "reason": reason
        }

    def process_file(
        self,
        input_audio_path: Path,
        output_audio_path: Path,
        forced_mode: Optional[str] = None,
        cancel_check=None,
    ) -> Dict[str, Any]:
        """
        Processes audio file using auto-selected optimal strategy.
        """
        t0 = time.monotonic()
        output_audio_path.parent.mkdir(parents=True, exist_ok=True)

        analysis = self.analyze_audio_properties(input_audio_path, cancel_check=cancel_check)
        selected_mode = forced_mode or analysis["mode"]
        if selected_mode not in ("DSP_STEREO_CENTER_CANCEL", "DSP_MONO_ADAPTIVE_FORMANT"):
            raise ValueError(f"Chế độ giảm giọng không hợp lệ: {selected_mode}")
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

        suffix = output_audio_path.suffix.lower()
        if suffix == ".wav":
            codec_args = ["-c:a", "pcm_s16le", "-rf64", "auto"]
        elif suffix in (".ogg", ".opus"):
            codec_args = ["-c:a", "libopus", "-b:a", "128k", "-ar", "48000"]
        else:
            codec_args = ["-c:a", "aac", "-b:a", "192k"]

        cmd = [
            "ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(input_audio_path),
            "-filter_complex", filter_complex,
            "-map", "[aout]",
            *codec_args,
            str(output_audio_path)
        ]

        run_media(cmd, cancel_check)

        elapsed = max(0.001, time.monotonic() - t0)

        # Calculate duration of output
        dur_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(output_audio_path)]
        if not output_audio_path.is_file() or output_audio_path.stat().st_size <= 0:
            raise ValueError("Bộ giảm giọng không tạo được tệp âm thanh hợp lệ.")
        try:
            duration = float(self._run_analysis_command(dur_cmd, cancel_check).strip())
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("invalid measured duration")
        except (ValueError, TypeError) as exc:
            raise ValueError("Không đo được thời lượng âm thanh kết quả; chưa thể báo xử lý thành công.") from exc

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
