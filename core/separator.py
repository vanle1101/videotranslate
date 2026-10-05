import os
import subprocess
from pathlib import Path
from typing import Tuple
from config import settings

class AudioSeparator:
    def __init__(self, temp_dir: Path = None):
        self.temp_dir = temp_dir or settings.TEMP_DIR
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def extract_audio(self, video_path: str, output_wav: Path) -> Path:
        """Extract full 44.1kHz stereo audio from video."""
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-acodec", "pcm_s16le",
            "-ar", "44100", "-ac", "2",
            str(output_wav)
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return output_wav

    def separate(self, video_path: str, task_id: str) -> Tuple[Path, Path]:
        """
        Separates video audio into:
        1. vocals_path: Chinese vocals (to feed into ASR)
        2. bgm_path: Music & Sound Effects (to keep as backing track)
        """
        task_dir = self.temp_dir / task_id
        task_dir.mkdir(parents=True, exist_ok=True)

        original_audio = task_dir / "original_audio.wav"
        self.extract_audio(video_path, original_audio)

        vocals_path = task_dir / "vocals.wav"
        bgm_path = task_dir / "bgm_sfx.wav"

        # Check if Demucs or python-audio-separator is available
        demucs_available = False
        try:
            import demucs.separate
            demucs_available = True
        except ImportError:
            # Check CLI demucs
            check = subprocess.run(["demucs", "--help"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if check.returncode == 0:
                demucs_available = True

        if demucs_available and settings.SEPARATION_ENGINE == "demucs":
            print(f"[*] Running Demucs separation on {original_audio.name}...")
            # Run Demucs CLI to isolate vocals
            # --two-stems=vocals outputs 'vocals.wav' and 'no_vocals.wav'
            out_model_dir = task_dir / "demucs_out"
            cmd = [
                "demucs",
                "--two-stems", "vocals",
                "-n", settings.DEMUCS_MODEL,
                "-o", str(out_model_dir),
                str(original_audio)
            ]
            try:
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                # Find output files
                model_folder = out_model_dir / settings.DEMUCS_MODEL / original_audio.stem
                if (model_folder / "vocals.wav").exists() and (model_folder / "no_vocals.wav").exists():
                    import shutil
                    shutil.move(str(model_folder / "vocals.wav"), str(vocals_path))
                    shutil.move(str(model_folder / "no_vocals.wav"), str(bgm_path))
                    return vocals_path, bgm_path
            except Exception as e:
                print(f"[!] Demucs failed ({e}), falling back to direct audio splitting.")

        # Fallback: Vocals = original audio, BGM = original audio with vocal suppression filter
        # or simply original audio for ducking
        import shutil
        shutil.copyfile(original_audio, vocals_path)
        
        # Simple vocal reducer filter in FFmpeg (pan=stereo|c0=c0-c1|c1=c1-c0 phase inversion or low/high pass)
        # Keeps drum/bass and background stereo effects
        cmd = [
            "ffmpeg", "-y", "-i", str(original_audio),
            "-af", "stereotools=mlev=0.1:slev=1.2", # reduce center channel (vocal)
            str(bgm_path)
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            shutil.copyfile(original_audio, bgm_path)

        return vocals_path, bgm_path
