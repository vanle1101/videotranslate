import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Tuple, Dict, Any, Optional
from config import settings
from core.engines.separator.base import SeparatorEngine

class BSRoFormerSeparator(SeparatorEngine):
    """
    Audio separation using nomadkaraoke/python-audio-separator with BS-RoFormer / MelBand RoFormer.
    Isolates clean vocals while preserving complete music, footsteps, impact effects, and ambient sounds.
    """
    # audio-separator 0.30.2 supports the application's NumPy 1.x runtime.
    # Its RoFormer overlap parameter is a step in seconds, not an overlap count.
    SEGMENT_SIZE = 256
    STEP_SECONDS = 0.6375

    def __init__(self, model_name: str = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"):
        self.model_name = model_name
        self.model_dir = settings.BASE_DIR / "workspace" / "models" / "audio_separator"
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.STEP_SECONDS = float(getattr(settings, "ROFORMER_STEP_SECONDS", type(self).STEP_SECONDS))
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
            if (str(getattr(settings, "DEVICE", "")).strip().lower() == "cuda"
                    and os.environ.get("CUDA_VISIBLE_DEVICES", "").strip() == "-1"):
                os.environ.pop("CUDA_VISIBLE_DEVICES", None)
            import torch
            cpu_threads = max(1, int(getattr(settings, "ASR_CPU_THREADS", 2)))
            if torch.get_num_threads() > cpu_threads:
                torch.set_num_threads(cpu_threads)
            from audio_separator.separator import Separator
            self.separator = Separator(
                model_file_dir=str(self.model_dir),
                output_dir=str(settings.TEMP_DIR),
                output_format="WAV",
                use_soundfile=True,
                use_autocast=False,
                mdxc_params={
                    "segment_size": self.SEGMENT_SIZE,
                    "override_model_segment_size": True,
                    "batch_size": 1,
                    "overlap": self.STEP_SECONDS,
                    "pitch_shift": 0,
                },
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
            "model_dir": str(self.model_dir),
            "segment_size": self.SEGMENT_SIZE,
            "step_seconds": self.STEP_SECONDS,
            "precision": "float32",
        }

    @contextmanager
    def _prepared_audio(self, audio_path: Path, output_dir: Path):
        """Pad sub-chunk inputs for the older RoFormer overlap-add implementation."""
        import librosa
        import numpy as np
        import soundfile as sf

        duration = librosa.get_duration(path=str(audio_path))
        if duration <= 0:
            raise ValueError("Cannot separate an empty audio file")
        model_config = self.separator.model_instance.model_data_cfgdict
        sample_rate = int(model_config.audio.sample_rate)
        minimum_samples = int(model_config.audio.hop_length) * (self.SEGMENT_SIZE - 1)
        if duration >= minimum_samples / sample_rate:
            yield audio_path, duration
            return

        samples, _ = librosa.load(str(audio_path), sr=sample_rate, mono=False)
        samples = np.atleast_2d(samples)
        samples = np.pad(samples, ((0, 0), (0, max(0, minimum_samples - samples.shape[-1]))))
        with tempfile.TemporaryDirectory(prefix="roformer-input-", dir=output_dir) as directory:
            padded_path = Path(directory) / "padded.wav"
            sf.write(str(padded_path), samples.T, sample_rate, subtype="FLOAT")
            yield padded_path, duration

    @staticmethod
    def _match_duration(audio_path: Path, duration: float):
        """Remove input padding without loading a long output track into memory."""
        import soundfile as sf

        info = sf.info(str(audio_path))
        target_frames = round(duration * info.samplerate)
        if info.frames == target_frames:
            return
        if info.frames < target_frames:
            if target_frames - info.frames <= 1:
                return
            raise RuntimeError(f"Separated audio is shorter than its source: {audio_path}")

        with tempfile.NamedTemporaryFile(prefix="roformer-trim-", suffix=".wav",
                                         dir=audio_path.parent, delete=False) as file:
            trimmed_path = Path(file.name)
        try:
            with sf.SoundFile(str(audio_path)) as source, sf.SoundFile(
                str(trimmed_path), mode="w", samplerate=info.samplerate,
                channels=info.channels, format="WAV", subtype=info.subtype,
            ) as target:
                remaining = target_frames
                while remaining:
                    block = source.read(min(remaining, 65536), dtype="float32", always_2d=True)
                    if not len(block):
                        raise RuntimeError(f"Unexpected end of separated audio: {audio_path}")
                    target.write(block)
                    remaining -= len(block)
            os.replace(trimmed_path, audio_path)
        finally:
            trimmed_path.unlink(missing_ok=True)

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
        # The loaded architecture keeps its own copy of the output directory.
        self.separator.model_instance.output_dir = str(output_dir)

        t0 = time.time()
        with self._prepared_audio(audio_path, output_dir) as (prepared_path, duration):
            output_files = self.separator.separate(str(prepared_path))
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
            if "instrumental" in lower or "no_vocals" in lower:
                if file_p.resolve() != instrumental_path.resolve():
                    shutil.move(str(file_p), str(instrumental_path))
            elif "vocals" in lower:
                if file_p.resolve() != vocals_path.resolve():
                    shutil.move(str(file_p), str(vocals_path))

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

        for output_path in (vocals_path, instrumental_path):
            if not output_path.exists():
                raise RuntimeError(f"RoFormer did not produce the expected stem: {output_path}")
            self._match_duration(output_path, duration)

        if progress_callback:
            progress_callback(100, f"Tách âm hoàn tất ({elapsed:.1f}s)")

        return vocals_path, instrumental_path
