import platform
import subprocess
from typing import Dict, Any

def detect_hardware() -> Dict[str, Any]:
    """Detects system hardware: CPU, RAM, GPU, VRAM, CUDA, and ONNX providers."""
    info = {
        "platform": platform.platform(),
        "processor": platform.processor(),
        "cpu_count": None,
        "ram_gb": None,
        "gpu_name": None,
        "vram_total_mb": None,
        "vram_free_mb": None,
        "cuda_available": False,
        "cuda_version": None,
        "onnx_providers": []
    }

    try:
        import os
        info["cpu_count"] = os.cpu_count()
    except Exception:
        pass

    # Check CUDA with PyTorch
    try:
        import torch
        info["cuda_available"] = torch.cuda.is_available()
        if info["cuda_available"]:
            info["gpu_name"] = torch.cuda.get_device_name(0)
            props = torch.cuda.get_device_properties(0)
            info["vram_total_mb"] = round(props.total_memory / (1024 * 1024), 1)
            info["cuda_version"] = torch.version.cuda
    except Exception:
        pass

    # Check nvidia-smi as fallback/enrichment
    if not info["gpu_name"]:
        try:
            res = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            if res.returncode == 0 and res.stdout.strip():
                parts = [p.strip() for p in res.stdout.strip().split(",")]
                info["gpu_name"] = parts[0]
                info["vram_total_mb"] = float(parts[1])
                info["vram_free_mb"] = float(parts[2])
        except Exception:
            pass

    # Check ONNX providers
    try:
        import onnxruntime as ort
        info["onnx_providers"] = ort.get_available_providers()
    except Exception:
        pass

    return info
