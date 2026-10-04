import os
from pathlib import Path
from typing import List, Dict, Any
from config import settings


def _nonempty_file(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _model_record(engine: str, name: str, path: Path, files: List[Path], device: str) -> Dict[str, Any]:
    present = bool(files) and all(_nonempty_file(file) for file in files)
    return {
        "engine": engine,
        "model_name": name,
        "downloaded": present,
        "size_mb": round(sum(file.stat().st_size for file in files if _nonempty_file(file)) / (1024 ** 2), 1),
        "device": device,
        "status": "AVAILABLE" if present else "NOT_DOWNLOADED",
        "path": str(path),
    }


class ModelManager:
    """Reports checkpoint files present on this machine without downloading models."""

    @staticmethod
    def get_all_models() -> List[Dict[str, Any]]:
        model_root = settings.WORKSPACE_DIR / "models"
        device = str(settings.DEVICE).upper()
        roformer = model_root / "audio_separator" / settings.ROFORMER_MODEL
        sense_dir = model_root / "sensevoice_onnx"

        # Respect the user's existing Hugging Face cache configuration.
        cache_home = Path(os.getenv("XDG_CACHE_HOME", str(Path.home() / ".cache")))
        hf_home = Path(os.getenv("HF_HOME", str(cache_home / "huggingface")))
        hf_hub = Path(os.getenv("HF_HUB_CACHE", os.getenv("HUGGINGFACE_HUB_CACHE", str(hf_home / "hub"))))
        vieneu_cache = hf_hub / "models--pnnbao-ump--VieNeu-TTS-v3-Turbo"
        vieneu_files = [
            file for file in (vieneu_cache / "snapshots").glob("**/*")
            if file.suffix in {".onnx", ".safetensors", ".gguf", ".bin"} and _nonempty_file(file)
        ]

        whisper_size = settings.WHISPER_MODEL_SIZE
        whisper_dir = model_root / f"faster-whisper-{whisper_size}"
        whisper_files = [whisper_dir / name for name in ("model.bin", "config.json", "tokenizer.json")]

        vsr_dir = settings.BASE_DIR / "engines" / "video-subtitle-remover"
        vsr_files = [file for file in vsr_dir.glob("**/ProPainter.pth") if _nonempty_file(file)]
        return [
            _model_record("python-audio-separator", "BS-RoFormer (Vocal/Instrumental)", roformer, [roformer], device),
            _model_record("FunAudioLLM/SenseVoice", "SenseVoiceSmall ONNX Int8", sense_dir,
                          [sense_dir / "model.int8.onnx", sense_dir / "tokens.txt"], "CPU / ONNX"),
            _model_record("VieNeu-TTS", "VieNeu-TTS v3 Turbo", vieneu_cache, vieneu_files, "CPU / ONNX"),
            _model_record("Faster-Whisper (Fallback)", f"Whisper-{whisper_size} (CTranslate2)",
                          whisper_dir, whisper_files, device),
            _model_record("YaoFANGUK/VSR + ProPainter", "ProPainter checkpoint", vsr_dir, vsr_files, device),
        ]

    @staticmethod
    def verify_model(query: str) -> Dict[str, Any]:
        query = query.strip().lower()
        target = next((model for model in ModelManager.get_all_models()
                       if query and (query in model["engine"].lower() or query in model["model_name"].lower())), None)
        if target is None:
            return {"ok": False, "message": f"Không tìm thấy model khớp với '{query}'"}
        if not target["downloaded"]:
            return {
                "ok": False, "engine": target["engine"], "model_name": target["model_name"],
                "status": "MISSING",
                "message": f"Chưa có đủ tệp model tại: {target['path']}",
            }
        return {
            "ok": True, "engine": target["engine"], "model_name": target["model_name"],
            "size_mb": target["size_mb"], "device": target["device"], "status": "FILES_PRESENT",
            "message": f"Đã tìm thấy tệp {target['model_name']} ({target['size_mb']} MB). Chưa kiểm tra checksum hoặc chạy suy luận.",
        }
