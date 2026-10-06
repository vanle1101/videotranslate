import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace
import wave

import pytest

from core.engines.tts import piper_engine as piper


def test_availability_checks_assets_without_loading_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(piper, "piper_model_dir", lambda: tmp_path)
    monkeypatch.setattr(piper, "PIPER_ASSETS", (("test.onnx", 3, "unused"),))
    monkeypatch.setattr(piper.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(piper.PiperEngine, "_ensure_loaded", lambda: pytest.fail("model loaded"))
    assert not piper.piper_available()
    (tmp_path / "test.onnx").write_bytes(b"123")
    assert piper.piper_available()
    (tmp_path / "test.onnx").write_bytes(b"12")
    assert not piper.piper_available()


def test_corrupt_same_size_weights_rejected(monkeypatch, tmp_path):
    expected = hashlib.sha256(b"abc").hexdigest()
    monkeypatch.setattr(piper, "PIPER_ASSETS", (("test.onnx", 3, expected),))
    (tmp_path / "test.onnx").write_bytes(b"xyz")
    with pytest.raises(RuntimeError, match="mã kiểm tra"):
        piper._verify_assets(tmp_path)
    (tmp_path / "test.onnx").write_bytes(b"abc")
    piper._verify_assets(tmp_path)


@pytest.mark.parametrize("voice", ["5", "-1", "vi-VN-HoaiMyNeural", "Unknown"])
def test_wrong_provider_or_speaker_rejected_before_model_load(monkeypatch, tmp_path, voice):
    monkeypatch.setattr(piper.PiperEngine, "_ensure_loaded", lambda: pytest.fail("model loaded"))
    with pytest.raises(ValueError, match="không hợp lệ"):
        piper.PiperEngine().synthesize("Xin chào", tmp_path / "out.wav", voice=voice)
    assert not (tmp_path / "out.wav").exists()


@pytest.mark.parametrize("speed", [0, -1, float("nan"), float("inf"), 2.1])
def test_invalid_speed_rejected_before_load(monkeypatch, tmp_path, speed):
    monkeypatch.setattr(piper.PiperEngine, "_ensure_loaded", lambda: pytest.fail("model loaded"))
    with pytest.raises(ValueError, match="Tốc độ"):
        piper.PiperEngine().synthesize("Xin chào", tmp_path / "out.wav", speed=speed)


def test_reference_audio_not_silently_ignored(monkeypatch, tmp_path):
    monkeypatch.setattr(piper.PiperEngine, "_ensure_loaded", lambda: pytest.fail("model loaded"))
    with pytest.raises(ValueError, match="không hỗ trợ"):
        piper.PiperEngine().synthesize("Xin chào", tmp_path / "out.wav", ref_audio=Path("sample.wav"))


def test_normalized_text_selected_speaker_speed_and_audio(monkeypatch, tmp_path):
    calls = []

    class FakeVoice:
        def synthesize_wav(self, text, output, syn_config):
            calls.append((text, syn_config))
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(22050)
            output.writeframes(b"\x01\x00" * 100)

    monkeypatch.setattr(piper.PiperEngine, "_ensure_loaded", lambda self: FakeVoice())
    monkeypatch.setattr(piper.PiperEngine, "_normalizer", SimpleNamespace(normalize=lambda text: text.replace("20", "hai mươi")))
    monkeypatch.setitem(sys.modules, "piper", SimpleNamespace(SynthesisConfig=lambda **kw: kw))
    progress = []
    output = tmp_path / "audio.wav"
    result = piper.PiperEngine().synthesize("  Giá 20 đồng.  ", output, voice="piper:2", speed=1.5, progress_callback=progress.append)
    assert result == output
    assert calls[0][0] == "Giá hai mươi đồng."
    assert calls[0][1]["speaker_id"] == 2
    assert calls[0][1]["length_scale"] == pytest.approx(0.8)
    assert progress == [100]
    with wave.open(str(output), "rb") as stream:
        assert stream.getnframes() == 100
        assert stream.getframerate() == 22050


def test_instances_share_single_cpu_model_with_bounded_threads(monkeypatch, tmp_path):
    made = []
    (tmp_path / (piper.PIPER_MODEL_FILENAME + ".json")).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(piper, "piper_model_dir", lambda: tmp_path)
    monkeypatch.setattr(piper, "_verify_assets", lambda root: None)
    for field in ("_model", "_normalizer", "_model_root"):
        monkeypatch.setattr(piper.PiperEngine, field, None)
    fake_ort = SimpleNamespace(
        SessionOptions=SimpleNamespace, ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL="seq"),
        InferenceSession=lambda path, **kwargs: made.append(kwargs) or object(),
    )
    monkeypatch.setitem(sys.modules, "onnxruntime", fake_ort)
    monkeypatch.setitem(sys.modules, "piper", SimpleNamespace(PiperVoice=lambda **kwargs: SimpleNamespace(**kwargs)))
    monkeypatch.setitem(sys.modules, "piper.config", SimpleNamespace(PiperConfig=SimpleNamespace(from_dict=lambda d: d)))
    monkeypatch.setitem(sys.modules, "piper.phonemize_espeak", SimpleNamespace(ESPEAK_DATA_DIR=Path("data")))
    monkeypatch.setitem(sys.modules, "sea_g2p", SimpleNamespace(Normalizer=lambda **kw: object()))
    first = piper.PiperEngine()._ensure_loaded()
    second = piper.PiperEngine()._ensure_loaded()
    assert first is second
    assert len(made) == 1
    assert made[0]["providers"] == ["CPUExecutionProvider"]
    assert made[0]["sess_options"].intra_op_num_threads == 4
    assert made[0]["sess_options"].inter_op_num_threads == 1


def test_acquisition_plan_is_pinned_and_matches_engine():
    from scripts.download_piper import build_plan
    plan = build_plan()
    assert len(plan) == 2
    assert sum(item["size"] for item in plan) == 77105908
    for item in plan:
        assert f"/resolve/{piper.PIPER_REVISION}/" in item["url"]
        assert len(item["sha256"]) == 64
        assert Path(item["path"]).parent == piper.piper_model_dir()


@pytest.mark.skipif(piper.os.name != "nt", reason="Windows native eSpeak path handling")
def test_unicode_workspace_uses_existing_ascii_relative_data(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    (data / "phontab").write_bytes(b"table")
    monkeypatch.setattr(piper.os.path, "relpath", lambda path: "data")
    original_cwd = Path.cwd()
    assert piper._espeak_data_path(Path("D:/Dịch Video/data")) == Path("data")
    assert Path.cwd() == original_cwd


@pytest.mark.skipif(piper.os.name != "nt", reason="Windows native eSpeak path handling")
def test_unusable_native_data_path_fails_before_espeak_can_exit(monkeypatch):
    monkeypatch.setattr(piper.os.path, "relpath", lambda path: "Dịch Video/data")
    with pytest.raises(RuntimeError, match="shortcut"):
        piper._espeak_data_path(Path("D:/Dịch Video/data"))
