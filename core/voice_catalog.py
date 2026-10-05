"""Vietnamese voices backed by installed providers, without loading a model."""

from functools import lru_cache
import importlib.util
import json
from pathlib import Path
from typing import Any, Optional

from config import settings


_EDGE_VOICES = {
    "vi-VN-HoaiMyNeural": ("Hoài My", "Nữ · Tiếng Việt"),
    "vi-VN-NamMinhNeural": ("Nam Minh", "Nam · Tiếng Việt"),
}
_ENGINE_NAMES = {
    "edge": "edge-tts", "edge-tts": "edge-tts",
    "vieneu": "vieneu-tts", "vieneu-tts": "vieneu-tts",
    "piper": "piper-tts", "piper-tts": "piper-tts",
}


def _module_present(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _preset_file() -> Optional[Path]:
    # Looking up the top-level spec does not import the SDK or initialize ONNX.
    try:
        spec = importlib.util.find_spec("vieneu")
        if spec and spec.origin:
            return Path(spec.origin).parent / "assets" / "voices_v3_turbo.json"
    except (ImportError, ValueError):
        pass
    return None


@lru_cache(maxsize=2)
def _read_presets(path: str, modified_ns: int, size: int) -> tuple:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    presets = data.get("presets", {})
    if not isinstance(presets, dict):
        return ()
    records = []
    for name, preset in presets.items():
        if not isinstance(name, str) or not isinstance(preset, dict):
            continue
        if not preset.get("speaker_emb") or not preset.get("codes"):
            continue
        featured = preset.get("featured")
        records.append({
            "name": name,
            "description": str(preset.get("description", "Tiếng Việt")),
            "aliases": tuple(a for a in (preset.get("aliases") or []) if isinstance(a, str)),
            "featured": featured if isinstance(featured, int) and featured > 0 else 1000,
        })
    # Do not keep reference codes / embeddings alive merely for a dropdown.
    return tuple(sorted(records, key=lambda item: item["featured"]))


def _vieneu_presets() -> tuple:
    path = _preset_file()
    if path is None:
        return ()
    try:
        info = path.stat()
        return _read_presets(str(path), info.st_mtime_ns, info.st_size)
    except (OSError, ValueError, TypeError):
        return ()


def _vieneu_available() -> bool:
    from core.model_manager import ModelManager
    return any(item["engine"] == "VieNeu-TTS" and item["runtime_available"]
               for item in ModelManager.get_all_models())


def list_voices() -> list[dict[str, Any]]:
    """Return real provider voices. Availability is local readiness, not a network probe."""
    edge_ready = _module_present("edge_tts")
    voices = [{
        "id": f"edge:{voice_id}", "name": name,
        "engine": "edge-tts", "source": "Microsoft Edge",
        "language": "vi-VN", "description": description,
        "available": edge_ready, "offline": False,
    } for voice_id, (name, description) in _EDGE_VOICES.items()]
    presets = _vieneu_presets()
    vieneu_ready = _vieneu_available() if presets else False
    voices.extend({
        "id": f"vieneu:{preset['name']}", "name": preset["name"],
        "engine": "vieneu-tts", "source": "VieNeu-TTS v3 Turbo · Hugging Face",
        "source_url": "https://huggingface.co/pnnbao-ump/VieNeu-TTS-v3-Turbo",
        "language": "vi-VN", "description": preset["description"],
        "available": vieneu_ready, "offline": True,
    } for preset in presets)
    from core.engines.tts.piper_engine import PIPER_SOURCE, PIPER_SOURCE_URL, PIPER_VOICES, piper_available
    piper_ready = piper_available()
    voices.extend({
        "id": f"piper:{preset['id']}", "name": preset["name"],
        "engine": "piper-tts", "source": PIPER_SOURCE, "source_url": PIPER_SOURCE_URL,
        "language": "vi-VN", "description": preset["description"],
        "available": piper_ready, "offline": True,
    } for preset in PIPER_VOICES)
    return voices


def resolve_voice(voice_id: Optional[str] = None, engine: Optional[str] = None) -> tuple[str, str]:
    """Resolve a catalog ID or legacy native ID; reject cross-provider pairs."""
    selected_engine = None
    if engine and engine.strip():
        selected_engine = _ENGINE_NAMES.get(engine.strip().lower())
        if selected_engine is None:
            raise ValueError("Bộ đọc không được hỗ trợ. Hãy chọn Microsoft Edge, VieNeu hoặc Piper.")
    voice = voice_id.strip() if voice_id else ""
    identified_engine = None
    if ":" in voice:
        prefix, voice = voice.split(":", 1)
        identified_engine = _ENGINE_NAMES.get(prefix.lower())
        if identified_engine is None or not voice:
            raise ValueError("Nguồn giọng đọc không hợp lệ.")
    elif voice in _EDGE_VOICES:
        identified_engine = "edge-tts"
    elif voice:
        from core.engines.tts.piper_engine import PIPER_VOICES
        identified_engine = ("piper-tts" if any(voice in (v["id"], v["name"]) for v in PIPER_VOICES)
                             else "vieneu-tts")

    if selected_engine and identified_engine and selected_engine != identified_engine:
        raise ValueError("Giọng đọc không thuộc bộ đọc đã chọn.")
    resolved_engine = selected_engine or identified_engine
    if resolved_engine is None:
        resolved_engine = _ENGINE_NAMES.get(str(settings.TTS_ENGINE).strip().lower())
    if resolved_engine not in {"edge-tts", "vieneu-tts", "piper-tts"}:
        raise ValueError("Bộ đọc mặc định không được hỗ trợ. Hãy chọn lại giọng đọc.")
    if resolved_engine == "edge-tts":
        native_voice = voice or settings.EDGE_VOICE
        if native_voice not in _EDGE_VOICES:
            raise ValueError("Giọng Microsoft Edge tiếng Việt không hợp lệ.")
        return resolved_engine, native_voice
    if resolved_engine == "piper-tts":
        from core.engines.tts.piper_engine import PIPER_VOICES
        native_voice = voice or "0"
        for preset in PIPER_VOICES:
            if native_voice in (preset["id"], preset["name"]):
                return resolved_engine, preset["id"]
        raise ValueError("Giọng Piper không hợp lệ. Hãy chọn lại giọng đọc.")

    native_voice = voice or "Trúc Ly"
    presets = _vieneu_presets()
    if not presets:
        raise ValueError("Chưa có danh sách giọng VieNeu-TTS v3 Turbo trên máy.")
    for preset in presets:
        if native_voice == preset["name"]:
            return resolved_engine, native_voice
    for preset in presets:
        if native_voice in preset["aliases"]:
            return resolved_engine, preset["name"]
    raise ValueError("Không tìm thấy giọng VieNeu đã chọn. Hãy tải lại danh sách giọng.")
