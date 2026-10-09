import platform
import subprocess
import os
import sys
from typing import Dict, Any


def _system_identity():
    if sys.platform == "win32":
        # Python 3.12 platform.processor()/platform.platform() query native WMI.
        # WMI raised 0x8007000e inside the live backend on this machine. Display
        # metadata must not enter that native probe or hold up the API loop.
        version = sys.getwindowsversion()
        label = f"Windows-{version.major}.{version.minor}.{version.build}"
        processor = os.environ.get("PROCESSOR_IDENTIFIER", "")
        return label, processor
    return platform.platform(), platform.processor()


def detect_hardware(*, probe_native: bool = True) -> Dict[str, Any]:
    """Detects system hardware: CPU, RAM, GPU, VRAM, CUDA, and ONNX providers."""
    system, processor = _system_identity()
    info = {
        "platform": system,
        "processor": processor,
        "cpu_count": None,
        "ram_gb": None,
        "ram_available_gb": None,
        "gpu_name": None,
        "vram_total_mb": None,
        "vram_free_mb": None,
        "cuda_available": False,
        "cuda_version": None,
        "cuda_backend": None,
        "onnx_providers": []
    }

    try:
        info["cpu_count"] = os.cpu_count()
    except Exception:
        pass

    try:
        import psutil
        memory = psutil.virtual_memory()
        info["ram_gb"] = round(memory.total / (1024 ** 3), 2)
        info["ram_available_gb"] = round(memory.available / (1024 ** 3), 2)
    except Exception:
        pass

    # Display metadata must not cold-load native inference runtimes. Their
    # capability remains unknown until an explicit engine/prewarm probe runs.
    if not probe_native:
        info["cuda_available"] = None
        info["native_probe"] = "not_run"

    # Check CUDA with PyTorch
    if probe_native:
        try:
            import torch
            info["cuda_available"] = torch.cuda.is_available()
            if info["cuda_available"]:
                info["cuda_backend"] = "pytorch"
                info["gpu_name"] = torch.cuda.get_device_name(0)
                props = torch.cuda.get_device_properties(0)
                info["vram_total_mb"] = round(props.total_memory / (1024 * 1024), 1)
                info["cuda_version"] = torch.version.cuda
        except Exception:
            pass

    # Faster-Whisper uses CTranslate2 and does not require PyTorch.
    if probe_native and not info["cuda_available"]:
        try:
            import ctranslate2
            if ctranslate2.get_cuda_device_count() > 0:
                supported = ctranslate2.get_supported_compute_types("cuda")
                info["cuda_available"] = bool(supported)
                if supported:
                    info["cuda_backend"] = "ctranslate2"
        except Exception:
            pass

    # A detected NVIDIA card alone does not establish a working CUDA runtime.
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=3 if probe_native else 1, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        if res.returncode == 0 and res.stdout.strip():
            parts = [p.strip() for p in res.stdout.splitlines()[0].split(",")]
            info["gpu_name"] = parts[0]
            info["vram_total_mb"] = float(parts[1])
            info["vram_free_mb"] = float(parts[2])
    except Exception:
        pass

    # Check ONNX providers
    if probe_native:
        try:
            import onnxruntime as ort
            info["onnx_providers"] = ort.get_available_providers()
        except Exception:
            pass

    return info
