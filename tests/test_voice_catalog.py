import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import wave

import pytest

from core import voice_catalog as catalog
from core.engines.tts import vieneu_engine as vieneu_module
from core.engines.tts.vieneu_engine import VieNeuEngine


@pytest.fixture
def presets(monkeypatch, tmp_path):
    path = tmp_path / "voices_v3_turbo.json"
    path.write_text(json.dumps({"presets": {
        "Trúc Ly": {"description": "Nữ · Bắc", "speaker_emb": [1], "codes": [[1]], "featured": 2},
        "Hải Đăng": {"description": "Nam · Bắc", "speaker_emb": [2], "codes": [[2]],
                      "aliases": ["Minh Quân"], "featured": 1},
        "Incomplete": {"description": "Không có âm thanh"},
    }}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(catalog, "_preset_file", lambda: path)
    monkeypatch.setattr(catalog, "_vieneu_available", lambda: True)
    monkeypatch.setattr(catalog, "_module_present", lambda name: True)
    monkeypatch.setattr(catalog.settings, "TTS_ENGINE", "edge-tts")
    monkeypatch.setattr(catalog.settings, "EDGE_VOICE", "vi-VN-HoaiMyNeural")
    return path


def test_catalog_uses_real_metadata_without_importing_model(presets, monkeypatch):
    monkeypatch.setitem(sys.modules, "vieneu", SimpleNamespace(Vieneu=lambda **kw: pytest.fail("Model loaded")))
    voices = catalog.list_voices()
    assert [v["id"] for v in voices if v["engine"] != "piper-tts"] == [
        "edge:vi-VN-HoaiMyNeural", "edge:vi-VN-NamMinhNeural", "vieneu:Hải Đăng", "vieneu:Trúc Ly",
    ]
    assert voices[3]["description"] == "Nữ · Bắc"
    assert all(v["available"] for v in voices if v["engine"] != "piper-tts")
    assert voices[-1]["offline"] is True
    assert voices[0]["offline"] is False
    assert len(VieNeuEngine().list_preset_voices()) == 2
    assert not any("speaker_emb" in v or "codes" in v for v in voices)


@pytest.mark.parametrize("voice,engine,expected", [
    (None, None, ("edge-tts", "vi-VN-HoaiMyNeural")),
    ("vi-VN-NamMinhNeural", None, ("edge-tts", "vi-VN-NamMinhNeural")),
    ("edge:vi-VN-HoaiMyNeural", "edge", ("edge-tts", "vi-VN-HoaiMyNeural")),
    ("vieneu:Trúc Ly", None, ("vieneu-tts", "Trúc Ly")),
    ("Hải Đăng", None, ("vieneu-tts", "Hải Đăng")),
    ("Minh Quân", "vieneu-tts", ("vieneu-tts", "Hải Đăng")),
    (None, "vieneu-tts", ("vieneu-tts", "Trúc Ly")),
    ("piper:2", None, ("piper-tts", "2")),
    ("Quang Huy", "piper-tts", ("piper-tts", "2")),
    ("0", "piper", ("piper-tts", "0")),
])
def test_resolve_legacy_and_catalog_ids(presets, voice, engine, expected):
    assert catalog.resolve_voice(voice, engine) == expected


@pytest.mark.parametrize("voice,engine", [
    ("vieneu:Trúc Ly", "edge-tts"), ("vi-VN-HoaiMyNeural", "vieneu-tts"),
    ("vieneu:Invented", None), ("edge:Trúc Ly", None), ("fake:voice", None),
    (None, "unknown"), ("vieneu:", None), ("piper:9", None), ("piper:1", "vieneu-tts"),
])
def test_resolve_rejects_unknown_or_cross_provider_voice(presets, voice, engine):
    with pytest.raises(ValueError):
        catalog.resolve_voice(voice, engine)


def test_unavailable_and_missing_metadata_are_not_fake_voices(presets, monkeypatch):
    monkeypatch.setattr(catalog, "_vieneu_available", lambda: False)
    assert all(not v["available"] for v in catalog.list_voices() if v["engine"] == "vieneu-tts")
    presets.write_text("{bad json", encoding="utf-8")
    assert not any(v["engine"] == "vieneu-tts" for v in catalog.list_voices())
    with pytest.raises(ValueError, match="Chưa có danh sách"):
        catalog.resolve_voice("Trúc Ly")


class FakeModel:
    def __init__(self):
        self.calls = []
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def infer(self, text, **kwargs):
        with self.lock:
            self.calls.append((text, kwargs))
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(0.015)
        with self.lock:
            self.active -= 1
        return b"\x10\x00" * 4800

    def save(self, audio, output):
        with wave.open(str(output), "wb") as destination:
            destination.setnchannels(1)
            destination.setsampwidth(2)
            destination.setframerate(48000)
            destination.writeframes(audio)


@pytest.fixture
def voice_engine(presets, monkeypatch, tmp_path):
    model = FakeModel()
    monkeypatch.setattr(VieNeuEngine, "_shared_model", None)
    monkeypatch.setattr(vieneu_module, "_create_local_model", lambda: model)
    engine = VieNeuEngine()
    engine.cache_dir = tmp_path / "cache"
    return engine, model


def test_synthesis_preserves_voice_and_reuses_cache_without_model_load(voice_engine, tmp_path):
    engine, model = voice_engine
    destination = tmp_path / "nested" / "first.wav"
    engine.synthesize("Xin chào", destination, voice="vieneu:Hải Đăng")
    assert model.calls == [("Xin chào", {"voice": "Hải Đăng"})]
    engine.model = None
    engine._ensure_loaded = lambda: pytest.fail("cached synthesis loaded a model")
    copied = engine.synthesize("Xin chào", tmp_path / "second.wav", voice="Minh Quân")
    assert copied.read_bytes() == destination.read_bytes()
    assert len(model.calls) == 1


def test_two_instances_share_one_model_and_serialize_inference(voice_engine, tmp_path):
    first, model = voice_engine
    second = VieNeuEngine()
    second.cache_dir = first.cache_dir
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(instance.synthesize, f"Câu {index}", tmp_path / f"{index}.wav", "Trúc Ly")
                   for index, instance in enumerate((first, second))]
        for future in futures:
            assert future.result().is_file()
    assert first.model is second.model is model
    assert model.max_active == 1


def test_reference_cache_uses_content_and_missing_reference_is_error(voice_engine, tmp_path):
    engine, model = voice_engine
    reference = tmp_path / "ref.wav"
    reference.write_bytes(b"first")
    engine.synthesize("Xin chào", tmp_path / "first.wav", ref_audio=reference)
    reference.write_bytes(b"changed")
    engine.synthesize("Xin chào", tmp_path / "second.wav", ref_audio=reference)
    assert len(model.calls) == 2
    with pytest.raises(ValueError, match="âm thanh mẫu"):
        engine.synthesize("Xin chào", tmp_path / "missing.wav", ref_audio=tmp_path / "missing-ref.wav")


def test_failed_audio_never_becomes_cache_or_output(voice_engine, tmp_path):
    engine, model = voice_engine
    model.save = lambda audio, output: Path(output).write_bytes(b"bad audio")
    output = tmp_path / "output.wav"
    with pytest.raises(RuntimeError, match="chưa tạo"):
        engine.synthesize("Xin chào", output)
    assert not output.exists()
    assert not list(engine.cache_dir.iterdir())


def test_local_loader_uses_cached_directories_without_sdk_network_factory(monkeypatch, tmp_path):
    from core.model_manager import ModelManager
    model_cache = tmp_path / "models--pnnbao-ump--VieNeu-TTS-v3-Turbo"
    model_dir = model_cache / "snapshots" / "revision"
    codec_dir = tmp_path / "models--OpenMOSS-Team--MOSS-Audio-Tokenizer-Nano-ONNX" / "snapshots" / "revision"
    (model_dir / "onnx_update").mkdir(parents=True)
    codec_dir.mkdir(parents=True)
    for path in (model_dir / "onnx_update" / "graph.onnx", model_dir / "speaker_encoder.onnx",
                 model_dir / "denoiser.onnx", codec_dir / "codec.onnx"):
        path.write_bytes(b"asset")
    calls = []

    class Base:
        def __init__(self):
            self.base_initialized = True

    class SDK:
        def __init__(self, **kwargs):
            pytest.fail("High-level SDK constructor may contact HF")

        def _load_v3_voices(self):
            self.voices_loaded = True

    class Runtime:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(ModelManager, "get_all_models", lambda: [{"engine": "VieNeu-TTS", "path": str(model_cache)}])
    monkeypatch.setitem(sys.modules, "vieneu.base", SimpleNamespace(BaseVieneuTTS=Base))
    monkeypatch.setitem(sys.modules, "vieneu.v3turbo", SimpleNamespace(V3TurboVieNeuTTS=SDK))
    monkeypatch.setitem(sys.modules, "vieneu._v3_turbo_engine.onnx_runtime_lite", SimpleNamespace(
        OnnxV3LiteEngine=Runtime, _GRAPH_FILES=["graph.onnx"], _CODEC_FILES=["codec.onnx"]))
    monkeypatch.setitem(sys.modules, "vieneu_utils.core_utils", SimpleNamespace(BABBLE_MAX_RETRIES=2))
    model = vieneu_module._create_local_model()
    assert calls == [{"checkpoint_path": str(model_dir), "onnx_dir": str(model_dir / "onnx_update"),
                      "codec_dir": str(codec_dir), "threads": 4}]
    assert model.backend == "onnx" and model.sample_rate == 48000
    assert model.voices_loaded and model.base_initialized
    assert model.max_batch_size == 1


@pytest.mark.parametrize("text,speed,voice", [(" ", 1, None), ("Chào", 0, None), ("Chào", float("nan"), None),
                                                ("Chào", 1, "vi-VN-HoaiMyNeural")])
def test_invalid_synthesis_fails_before_loading_model(voice_engine, tmp_path, text, speed, voice):
    engine, model = voice_engine
    with pytest.raises(ValueError):
        engine.synthesize(text, tmp_path / "output.wav", speed=speed, voice=voice)
    assert not model.calls
    assert engine.model is None
