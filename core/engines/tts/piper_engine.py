"""Five Vietnamese Piper voices, using one shared CPU-only ONNX session.

Model: CakeByVPBank/piper-pgl-v4-vi_VN-version39_epoch39 (MIT).
Runtime: piper-tts 1.8.0 (GPL-3.0-or-later). See docs/piper-voices.md.
Importing this module never loads a model or contacts a remote service.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import threading
import wave

from config import settings
from core.engines.tts.base import TTSEngine

PIPER_SOURCE = "Piper · Cake"
PIPER_SOURCE_URL = "https://huggingface.co/CakeByVPBank/piper-pgl-v4-vi_VN-version39_epoch39"
PIPER_REVISION = "8ea50134bea762f6a1faac671e1a33c41871289c"
PIPER_MODEL_FILENAME = "vi_VN-csa-voice-piper-v3-medium.onnx"
PIPER_ASSETS = (
    (PIPER_MODEL_FILENAME, 77100991, "99424831c88afec929eba3aef83a1b3d92f261b0bdc771bc2458f7f1ba63b6d2"),
    (PIPER_MODEL_FILENAME + ".json", 4917, "919c6f45f16e6549e5f72ae4961bf191abbb661830574e7786ab13da496a408e"),
)
PIPER_VOICES = (
    {"id": "0", "name": "Ngọc Lan", "description": "Nữ · Bắc · Đọc tự nhiên", "gender": "female", "region": "Bắc"},
    {"id": "1", "name": "Minh Anh", "description": "Nữ · Bắc · Giọng rõ, chắc", "gender": "female", "region": "Bắc"},
    {"id": "2", "name": "Quang Huy", "description": "Nam · Bắc · Đọc tự nhiên", "gender": "male", "region": "Bắc"},
    {"id": "3", "name": "Thu Hà", "description": "Nữ · Bắc · Giọng hướng dẫn", "gender": "female", "region": "Bắc"},
    {"id": "4", "name": "Yến Nhi", "description": "Nữ · Nam · Âm sắc trầm", "gender": "female", "region": "Nam"},
)
_LENGTH_SCALES = (1.0, 1.2, 1.2, 1.2, 1.0)


def piper_model_dir() -> Path:
    return settings.WORKSPACE_DIR / "models" / "piper_vi"


def piper_available() -> bool:
    """Cheap package/file check; the engine verifies hashes before loading."""
    try:
        if not all(importlib.util.find_spec(name) for name in ("piper", "onnxruntime", "sea_g2p")):
            return False
        root = piper_model_dir()
        return all((root / name).is_file() and (root / name).stat().st_size == size
                   for name, size, _digest in PIPER_ASSETS)
    except (ImportError, OSError, ValueError):
        return False


def _verify_assets(root: Path) -> None:
    for name, size, expected_digest in PIPER_ASSETS:
        path = root / name
        if not path.is_file() or path.stat().st_size != size:
            raise RuntimeError("Thiếu model Piper. Chạy download_piper.py --download trước.")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected_digest:
            raise RuntimeError("Model Piper không khớp mã kiểm tra của nguồn phát hành.")


def _speaker_id(voice: str | None) -> int:
    chosen = "0" if voice is None else str(voice)
    for entry in PIPER_VOICES:
        if chosen in (entry["id"], entry["name"], f"piper:{entry['id']}"):
            return int(entry["id"])
    raise ValueError("Giọng Piper không hợp lệ. Hãy chọn lại một giọng trong danh sách.")


def _espeak_data_path(path: Path) -> Path:
    # eSpeak's Windows C file API cannot open the accented absolute workspace
    # path. A relative ASCII path uses the existing Windows current directory;
    # no process-wide chdir, duplicate data, or filesystem alias is needed.
    if os.name != "nt" or str(path).isascii():
        return path
    try:
        relative = Path(os.path.relpath(path))
    except ValueError:
        relative = path
    if str(relative).isascii() and (relative / "phontab").is_file():
        return relative
    raise RuntimeError("Không mở được dữ liệu giọng Piper. Hãy mở Studio bằng shortcut trong thư mục dự án.")


class PiperEngine(TTSEngine):
    _lock = threading.RLock()
    _model = None
    _normalizer = None
    _model_root: Path | None = None

    @property
    def name(self) -> str:
        return "Piper-Cake-Vietnamese"

    @property
    def is_available(self) -> bool:
        return piper_available()

    def get_info(self) -> dict:
        return {
            "name": self.name, "version": "1.8.0", "is_available": self.is_available,
            "model_path": str(piper_model_dir() / PIPER_MODEL_FILENAME),
            "supports_cloning": False, "sample_rate": 22050, "device": "cpu",
            "source": PIPER_SOURCE, "source_url": PIPER_SOURCE_URL,
        }

    def list_preset_voices(self) -> list[tuple[str, str]]:
        return [(f"{v['name']} — {v['description']}", v["id"]) for v in PIPER_VOICES]

    @classmethod
    def _ensure_loaded(cls):
        # Shared lock also serializes eSpeak's process-wide phonemizer state.
        with cls._lock:
            root = piper_model_dir().resolve()
            if cls._model is not None and cls._model_root == root:
                return cls._model
            _verify_assets(root)
            import onnxruntime as ort
            from piper import PiperVoice
            from piper.config import PiperConfig
            from piper.phonemize_espeak import ESPEAK_DATA_DIR
            from sea_g2p import Normalizer

            config = json.loads((root / (PIPER_MODEL_FILENAME + ".json")).read_text(encoding="utf-8"))
            options = ort.SessionOptions()
            options.intra_op_num_threads = 4
            options.inter_op_num_threads = 1
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            session = ort.InferenceSession(
                str(root / PIPER_MODEL_FILENAME), sess_options=options,
                providers=["CPUExecutionProvider"],
            )
            model = PiperVoice(session=session, config=PiperConfig.from_dict(config), download_dir=root,
                               espeak_data_dir=_espeak_data_path(ESPEAK_DATA_DIR))
            normalizer = Normalizer(lang="vi")
            cls._model, cls._normalizer, cls._model_root = model, normalizer, root
            return model

    def synthesize(self, text: str, output_path: Path, voice: str | None = None,
                   ref_audio: Path | None = None, speed: float = 1.0,
                   progress_callback=None) -> Path:
        if ref_audio is not None:
            raise ValueError("Piper dùng giọng có sẵn, không hỗ trợ mẫu giọng riêng.")
        speaker = _speaker_id(voice)
        speed = float(speed)
        if not math.isfinite(speed) or not 0.5 <= speed <= 2.0:
            raise ValueError("Tốc độ giọng đọc phải nằm trong khoảng 0,5–2,0.")
        if not text or not text.strip():
            raise ValueError("Nội dung đọc không được để trống.")
        output_path = Path(output_path)
        with self._lock:
            model = self._ensure_loaded()
            from piper import SynthesisConfig

            clean_text = self._normalizer.normalize(text.strip())
            if not clean_text.strip():
                raise ValueError("Nội dung không có từ có thể đọc.")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            config = SynthesisConfig(speaker_id=speaker, length_scale=_LENGTH_SCALES[speaker] / speed,
                                     noise_scale=0.667, noise_w_scale=0.8)
            with wave.open(str(output_path), "wb") as output:
                model.synthesize_wav(clean_text, output, syn_config=config)
            with wave.open(str(output_path), "rb") as result:
                if result.getnframes() == 0:
                    raise RuntimeError("Piper không tạo được âm thanh.")
        if progress_callback:
            progress_callback(100)
        return output_path
