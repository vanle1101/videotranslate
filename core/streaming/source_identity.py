"""Durable restart matching; no credentials or file content in manifests."""
import hashlib
import json
from pathlib import Path

from config import settings


def processing_signature():
    names = ("LLM_PROVIDER", "GEMINI_MODEL", "OPENCODE_MODEL", "OPENROUTER_MODEL",
             "WHISPER_MODEL_SIZE", "WHISPER_COMPUTE_TYPE", "SUPPRESSION_MODE",
             "MUSE_BROWSER_MODE")
    values = {name: getattr(settings, name, None) for name in names}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def local_file_signature(path):
    """Detect replacement/edit of a source at the same path without loading media."""
    try:
        info = Path(path).stat()
        if not Path(path).is_file():
            return None
        return hashlib.sha256(f"{info.st_size}:{info.st_mtime_ns}".encode()).hexdigest()
    except (OSError, TypeError, ValueError):
        return None
