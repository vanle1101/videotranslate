import os
import importlib.util
from pathlib import Path
from typing import List, Dict, Any
from config import settings


def _nonempty_file(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _module_present(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _model_record(engine: str, name: str, path: Path, files: List[Path], device: str,
                  modules=(), runtime_note="") -> Dict[str, Any]:
    present = bool(files) and all(_nonempty_file(file) for file in files)
    missing_modules = [name for name in modules if not _module_present(name)]
    runtime_available = not missing_modules and not runtime_note
    if missing_modules:
        runtime_note = "Thiếu thư viện chạy model: " + ", ".join(missing_modules)
    return {
        "engine": engine,
        "model_name": name,
        "downloaded": present,
        "size_mb": round(sum(file.stat().st_size for file in files if _nonempty_file(file)) / (1024 ** 2), 1),
        "device": device,
        "status": ("FILES_PRESENT" if runtime_available else "RUNTIME_MISSING") if present else "NOT_DOWNLOADED",
        "runtime_available": present and runtime_available,
        "runtime_verified": False,
        "runtime_note": runtime_note or "Đã có thư viện; chưa kiểm tra suy luận tại màn hình này.",
        "missing_files": [str(file) for file in files if not _nonempty_file(file)],
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
        # A single .onnx or .bin is not a complete voice engine. Require its
        # graphs, external tensor data, tokenizer and separate MOSS codec.
        voice_names = [f"onnx_update/{name}" for name in (
            "config.json", "tokenizer.json", "vieneu_prefill.onnx", "vieneu_decode_step.onnx",
            "vieneu_acoustic_cached.onnx", "vieneu_backbone_shared.data", "vieneu_v3_heads.npz",
        )] + ["config.json", "speaker_encoder.onnx", "denoiser.onnx"]
        codec_cache = hf_hub / "models--OpenMOSS-Team--MOSS-Audio-Tokenizer-Nano-ONNX"
        codec_names = [
            "moss_audio_tokenizer_decode_full.onnx", "moss_audio_tokenizer_decode_shared.data",
            "moss_audio_tokenizer_decode_step.onnx", "codec_browser_onnx_meta.json",
            "moss_audio_tokenizer_encode.onnx", "moss_audio_tokenizer_encode.data",
        ]

        def snapshot_files(cache, names):
            snapshots = list((cache / "snapshots").glob("*"))
            complete = next((folder for folder in snapshots
                             if all(_nonempty_file(folder / name) for name in names)), None)
            folder = complete or (max(snapshots, key=lambda p: p.stat().st_mtime) if snapshots else cache)
            return [folder / name for name in names]

        vieneu_files = snapshot_files(vieneu_cache, voice_names) + snapshot_files(codec_cache, codec_names)

        whisper_size = settings.WHISPER_MODEL_SIZE
        whisper_dir = model_root / f"faster-whisper-{whisper_size}"
        whisper_files = [whisper_dir / name for name in ("model.bin", "config.json", "tokenizer.json")]

        vsr_dir = model_root / "propainter"
        vsr_files = [vsr_dir / name for name in
                     ("ProPainter.pth", "raft-things.pth", "recurrent_flow_completion.pth")]
        return [
            _model_record("python-audio-separator", "BS-RoFormer (Vocal/Instrumental)", roformer.parent,
                          [roformer, roformer.with_suffix(".yaml")], device, ("audio_separator", "torch")),
            _model_record("FunAudioLLM/SenseVoice", "SenseVoiceSmall ONNX Int8", sense_dir,
                          [sense_dir / "model.int8.onnx", sense_dir / "tokens.txt"], "CPU / ONNX", ("sherpa_onnx",)),
            _model_record("VieNeu-TTS", "VieNeu-TTS v3 Turbo + MOSS codec", vieneu_cache,
                          vieneu_files, "CPU / ONNX", ("vieneu", "onnxruntime", "sea_g2p")),
            _model_record("Faster-Whisper (Fallback)", f"Whisper-{whisper_size} (CTranslate2)",
                          whisper_dir, whisper_files, device, ("faster_whisper",)),
            _model_record("YaoFANGUK/VSR + ProPainter", "ProPainter + RAFT + Flow Completion", vsr_dir,
                          vsr_files, device,
                          runtime_note="Đã tải checkpoint; ứng dụng chưa tích hợp suy luận ProPainter. Che phụ đề hiện dùng bộ lọc blur."),
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
            "runtime_available": target["runtime_available"], "runtime_verified": False,
            "message": (f"Đã tìm thấy đủ tệp {target['model_name']} ({target['size_mb']} MB). "
                        f"{target['runtime_note']} Chưa kiểm tra checksum ở thao tác này."),
        }
