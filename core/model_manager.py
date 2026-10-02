from pathlib import Path
from typing import List, Dict, Any
from config import settings

class ModelManager:
    """Manages local model checkpoints, download statuses, and disk paths."""

    @staticmethod
    def get_all_models() -> List[Dict[str, Any]]:
        models = []

        # 1. BS-RoFormer (python-audio-separator)
        roformer_dir = settings.BASE_DIR / "workspace" / "models" / "audio_separator"
        roformer_file = roformer_dir / "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
        roformer_exists = roformer_file.exists() and roformer_file.stat().st_size > 100000000
        models.append({
            "engine": "python-audio-separator",
            "model_name": "BS-RoFormer (Vocal/Instrumental)",
            "downloaded": roformer_exists,
            "size_mb": round(roformer_file.stat().st_size / (1024 * 1024), 1) if roformer_exists else 0,
            "device": "CUDA (GTX 1050 Ti)",
            "status": "AVAILABLE" if roformer_exists else "NOT_DOWNLOADED",
            "path": str(roformer_file.resolve()) if roformer_exists else str(roformer_file)
        })

        # 2. SenseVoice (FunAudioLLM)
        sense_dir = settings.BASE_DIR / "workspace" / "models" / "sensevoice_onnx"
        sense_file = sense_dir / "model.int8.onnx"
        sense_exists = sense_file.exists() and (sense_dir / "tokens.txt").exists()
        models.append({
            "engine": "FunAudioLLM/SenseVoice",
            "model_name": "SenseVoiceSmall ONNX Int8",
            "downloaded": sense_exists,
            "size_mb": round(sense_file.stat().st_size / (1024 * 1024), 1) if sense_exists else 0,
            "device": "CPU / ONNX (Auto)",
            "status": "AVAILABLE" if sense_exists else "NOT_DOWNLOADED",
            "path": str(sense_file.resolve()) if sense_exists else str(sense_file)
        })

        # 3. VieNeu-TTS v3 Turbo
        vieneu_cache = Path("C:/Users/phamc/.cache/huggingface/hub/models--pnnbao-ump--VieNeu-TTS-v3-Turbo")
        vieneu_exists = vieneu_cache.exists()
        models.append({
            "engine": "VieNeu-TTS",
            "model_name": "VieNeu-TTS v3 Turbo",
            "downloaded": vieneu_exists,
            "size_mb": 475.0 if vieneu_exists else 0,
            "device": "CPU / ONNX Runtime",
            "status": "AVAILABLE" if vieneu_exists else "NOT_DOWNLOADED",
            "path": str(vieneu_cache.resolve()) if vieneu_exists else str(vieneu_cache)
        })

        # 4. Faster-Whisper Fallback
        models.append({
            "engine": "Faster-Whisper (Fallback)",
            "model_name": "Whisper-small (CTranslate2)",
            "downloaded": True,
            "size_mb": 461.0,
            "device": "CUDA / CPU",
            "status": "AVAILABLE",
            "path": "HuggingFace/Systran"
        })

        # 5. ProPainter / Video Subtitle Remover
        vsr_path = Path("E:/DichVideoEngines/video-subtitle-remover")
        models.append({
            "engine": "YaoFANGUK/VSR + ProPainter",
            "model_name": "ProPainter & Targeted Mask",
            "downloaded": vsr_path.exists(),
            "size_mb": 120.0 if vsr_path.exists() else 0,
            "device": "CUDA / FFmpeg",
            "status": "AVAILABLE" if vsr_path.exists() else "NOT_DOWNLOADED",
            "path": str(vsr_path.resolve()) if vsr_path.exists() else str(vsr_path)
        })

        return models
