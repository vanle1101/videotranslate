import os
import shutil
import time
from pathlib import Path
from typing import Tuple, Dict, Any, Optional
from config import settings
from core.engines.separator.base import SeparatorEngine

class BSRoFormerSeparator(SeparatorEngine):
    """
    Audio separation using nomadkaraoke/python-audio-separator with BS-RoFormer / MelBand RoFormer.
    Isolates clean vocals while preserving complete music, footsteps, impact effects, and ambient sounds.
    """
    def __init__(self, model_name: str = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"):
        self.model_name = model_name
        self.model_dir = settings.BASE_DIR / "workspace" / "models" / "audio_separator"
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.separator = None

    @property
    def name(self) -> str:
        return f"BS-RoFormer ({self.model_name})"

    @property
    def is_available(self) -> bool:
        try:
            import audio_separator
            import torch
            return True
        except ImportError:
            return False

    def _ensure_loaded(self):
        if self.separator is None:
            print(f"[*] Initializing python-audio-separator with model: {self.model_name}...")
            from audio_separator.separator import Separator
            self.separator = Separator(
                model_file_dir=str(self.model_dir),
                output_dir=str(settings.TEMP_DIR),
                output_format="WAV"
            )
            print(f"[*] Loading model {self.model_name}...")
            self.separator.load_model(self.model_name)
            print("[+] Model loaded into memory successfully.")

    def get_info(self) -> Dict[str, Any]:
        ckpt_path = self.model_dir / self.model_name
        return {
            "name": self.name,
            "engine": "python-audio-separator",
            "model_name": self.model_name,
            "is_available": self.is_available,
            "checkpoint_downloaded": ckpt_path.exists(),
            "checkpoint_size_mb": round(ckpt_path.stat().st_size / 1024 / 1024, 1) if ckpt_path.exists() else 0,
            "model_dir": str(self.model_dir)
        }

    def separate(
        self,
        audio_path: Path,
        output_dir: Path,
        progress_callback: Optional[callable] = None
    ) -> Tuple[Path, Path]:
        """
        Executes separation:
        Returns (vocals_path, instrumental_path)
        """
        self._ensure_loaded()
        output_dir.mkdir(parents=True, exist_ok=True)

        if progress_callback:
            progress_callback(10, f"Đang tách âm thanh bằng {self.name}...")

        # Update output_dir on separator instance
        self.separator.output_dir = str(output_dir)

        t0 = time.time()
        output_files = self.separator.separate(str(audio_path))
        elapsed = time.time() - t0
        print(f"[+] Separation completed in {elapsed:.1f}s. Output files: {output_files}")

        # Map outputs: one is vocals, one is instrumental
        vocals_path = output_dir / "vocals.wav"
        instrumental_path = output_dir / "instrumental.wav"

        for f in output_files:
            file_p = None
            for cand in [output_dir / f, Path(self.separator.output_dir) / f, settings.TEMP_DIR / f, Path(f)]:
                if cand.exists():
                    file_p = cand
                    break
            if not file_p:
                continue

            lower = f.lower()
            if "vocals" in lower or "(vocals)" in lower:
                shutil.move(str(file_p), str(vocals_path))
            elif "instrumental" in lower or "(instrumental)" in lower or "no_vocals" in lower:
                shutil.move(str(file_p), str(instrumental_path))

        if not vocals_path.exists() and len(output_files) >= 1:
            for cand in [output_dir / output_files[0], settings.TEMP_DIR / output_files[0], Path(output_files[0])]:
                if cand.exists():
                    shutil.copyfile(str(cand), str(vocals_path))
                    break
        if not instrumental_path.exists() and len(output_files) >= 2:
            for cand in [output_dir / output_files[1], settings.TEMP_DIR / output_files[1], Path(output_files[1])]:
                if cand.exists():
                    shutil.copyfile(str(cand), str(instrumental_path))
                    break

        if progress_callback:
            progress_callback(100, f"Tách âm hoàn tất ({elapsed:.1f}s)")

        return vocals_path, instrumental_path
