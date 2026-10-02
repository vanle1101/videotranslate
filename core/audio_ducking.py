import subprocess
from pathlib import Path
from typing import Optional
from config import settings

class AudioDucker:
    def __init__(
        self,
        ducked_db: float = settings.BGM_VOLUME_DUCKED_DB,
        normal_db: float = settings.BGM_VOLUME_NORMAL_DB,
        voice_boost_db: float = settings.VOICE_VOLUME_BOOST_DB
    ):
        self.ducked_db = ducked_db
        self.normal_db = normal_db
        self.voice_boost_db = voice_boost_db

    def mix_and_duck(
        self,
        bgm_path: Path,
        voice_path: Path,
        output_path: Path
    ) -> Path:
        """
        Ducks BGM automatically whenever the voice track is speaking using FFmpeg's sidechaincompress.
        Produces a broadcast-ready stereo mix.
        """
        print(f"[*] Mixing BGM ({bgm_path.name}) and Voice ({voice_path.name}) with dynamic sidechain ducking...")

        # FFmpeg filter:
        # 1. Apply volume adjustment to BGM and Voice
        # 2. Feed Voice into BGM's sidechaincompress:
        #    threshold: 0.05 (triggers when voice starts)
        #    ratio: 6 (heavily compresses BGM down by ~12-16dB)
        #    attack: 40ms (fast smooth duck)
        #    release: 350ms (smooth natural return to normal)
        # 3. Mix both together cleanly without clipping
        filter_complex = (
            f"[0:a]volume={self.normal_db}dB[bgm_norm];"
            f"[1:a]volume={self.voice_boost_db}dB[voice_boost];"
            f"[bgm_norm][voice_boost]sidechaincompress="
            f"threshold=0.05:ratio=6:attack=40:release=350:makeup=1[bgm_ducked];"
            f"[bgm_ducked][voice_boost]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,"
            f"alimiter=limit=0.95[out]"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", str(bgm_path),
            "-i", str(voice_path),
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-ac", "2", "-ar", "44100",
            str(output_path)
        ]

        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            print(f"[!] Sidechain ducking failed, falling back to standard amix: {res.stderr.decode('utf-8', errors='ignore')[:200]}")
            # Fallback simple amix
            cmd_fallback = [
                "ffmpeg", "-y",
                "-i", str(bgm_path),
                "-i", str(voice_path),
                "-filter_complex", "[0:a]volume=0.3[a0];[1:a]volume=1.2[a1];[a0][a1]amix=inputs=2:duration=longest[out]",
                "-map", "[out]",
                "-ac", "2", "-ar", "44100",
                str(output_path)
            ]
            subprocess.run(cmd_fallback, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        print(f"[+] Master ducked audio created: {output_path.name}")
        return output_path
