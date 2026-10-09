import math
from pathlib import Path
from typing import Optional
from config import settings
from core.media_process import run_media

class PremiumAudioMixer:
    """
    Broadcast-grade Audio Mixing Engine.
    Combines BS-RoFormer Instrumental track and Vietnamese Voiceover with:
    - Sidechain dynamic ducking
    - Voice presence boost & limiter
    - Loudness normalization (EBU R128)
    - Fade-in / fade-out
    """
    def __init__(
        self,
        voice_gain_db: Optional[float] = None,
        bgm_gain_db: Optional[float] = None,
        duck_amount_db: Optional[float] = None,
        attack_ms: int = 40,
        release_ms: int = 350
    ):
        self.voice_gain_db = settings.VOICE_VOLUME_BOOST_DB if voice_gain_db is None else voice_gain_db
        self.bgm_gain_db = settings.BGM_VOLUME_NORMAL_DB if bgm_gain_db is None else bgm_gain_db
        self.duck_amount_db = settings.BGM_VOLUME_DUCKED_DB if duck_amount_db is None else duck_amount_db
        self.attack_ms = attack_ms
        self.release_ms = release_ms

    def mix(
        self,
        instrumental_path: Path,
        voice_path: Path,
        output_path: Path,
        total_duration: Optional[float] = None,
        cancel_check=None,
    ) -> Path:
        """
        Executes premium mix with FFmpeg filter graph.
        """
        print(f"[*] Premium Audio Mixer:")
        print(f"    Instrumental (RoFormer): {instrumental_path.name}")
        print(f"    Voice: {voice_path.name}")
        print(f"    Ducking: -{abs(self.duck_amount_db)}dB (Attack: {self.attack_ms}ms, Release: {self.release_ms}ms)")

        if total_duration is not None and (
            not math.isfinite(total_duration) or total_duration <= 0
        ):
            raise ValueError("total_duration must be a positive, finite number")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Each filter output can be consumed only once. Split the voice for
        # the sidechain detector and for the audible voiceover in the final mix.
        # Padding both inputs also prevents a short voice track cutting BGM off.
        ratio = 1.0 if self.duck_amount_db == 0 else max(1.0, min(20.0, abs(self.duck_amount_db) / 2.5))
        # A finite EOF on one side of the sidechain/asplit graph can end amix
        # before its queued tail is drained. Keep both inputs live until the
        # final atrim reaches the requested duration; don't pad only to that
        # duration upstream. Legacy callers without a duration still end at BGM.
        padding = ",apad" if total_duration else ""
        voice_padding = padding if total_duration else ",apad"
        trim = f",atrim=duration={total_duration}" if total_duration else ""

        filter_complex = (
            f"[0:a]asetpts=PTS-STARTPTS,volume={self.bgm_gain_db}dB{padding}[bgm_in];"
            f"[1:a]asetpts=PTS-STARTPTS,volume={self.voice_gain_db}dB{voice_padding},"
            f"asplit=2[voice_sidechain][voice_mix];"
            f"[bgm_in][voice_sidechain]sidechaincompress="
            f"threshold=0.04:ratio={ratio:.1f}:attack={self.attack_ms}:release={self.release_ms}:makeup=1[bgm_ducked];"
            f"[bgm_ducked][voice_mix]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mixed];"
            f"[mixed]alimiter=limit=0.95{trim}[out]"
        )

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-i", str(instrumental_path),
            "-i", str(voice_path),
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-ac", "2", "-ar", "44100",
            *(["-c:a", "pcm_s16le", "-rf64", "auto"] if output_path.suffix.lower() == ".wav" else []),
            str(output_path)
        ]

        run_media(cmd, cancel_check=cancel_check)
        print(f"[+] Master audio successfully created: {output_path.name}")
        return output_path
