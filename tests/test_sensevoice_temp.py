"""Offline isolation/cleanup checks; no ASR model or subprocess is started."""
from concurrent.futures import ThreadPoolExecutor
import sys
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from config import settings
from core.engines.asr.sensevoice_engine import SenseVoiceEngine


def prepared_engine():
    engine = SenseVoiceEngine()
    engine._ensure_loaded = Mock()
    stream = SimpleNamespace(accept_waveform=Mock(), result=SimpleNamespace(text="你好。", lang="<|zh|>"))
    engine.recognizer = SimpleNamespace(create_stream=lambda: stream, decode_stream=Mock())
    return engine


@pytest.mark.parametrize("language", ["", "zh"])
def test_explicit_evidence_language_reaches_native_recognizer(tmp_path, monkeypatch, language):
    for filename in ["model.int8.onnx", "tokens.txt"]:
        (tmp_path / filename).touch()
    factory = Mock(return_value=object())
    monkeypatch.setitem(sys.modules, "sherpa_onnx", SimpleNamespace(
        OfflineRecognizer=SimpleNamespace(from_sense_voice=factory)))
    engine = SenseVoiceEngine(tmp_path, language=language)
    engine._ensure_loaded()
    assert factory.call_args.kwargs["language"] == language
    assert factory.call_args.kwargs["use_itn"] is True


def test_sensevoice_respects_two_thread_cpu_ceiling(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "ASR_CPU_THREADS", 6)
    for filename in ["model.int8.onnx", "tokens.txt"]:
        (tmp_path / filename).touch()
    factory = Mock(return_value=object())
    monkeypatch.setitem(sys.modules, "sherpa_onnx", SimpleNamespace(
        OfflineRecognizer=SimpleNamespace(from_sense_voice=factory)))
    SenseVoiceEngine(tmp_path)._ensure_loaded()
    assert factory.call_args.kwargs["num_threads"] == 2


def test_simultaneous_inferences_never_share_pcm(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TEMP_DIR", tmp_path)
    barrier = Barrier(2)
    paths = []

    def convert(cmd, **kwargs):
        output = Path(cmd[-1])
        paths.append(output)
        output.write_bytes(b"test-pcm")
        barrier.wait(timeout=5)

    monkeypatch.setattr("subprocess.run", convert)
    monkeypatch.setattr("soundfile.read", lambda path: ([0.0] * 160, 16000))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(prepared_engine().transcribe, Path("source.wav")) for _ in range(2)]
        assert all(future.result()[0]["text_zh"] == "你好。" for future in futures)
    assert len(paths) == len(set(paths)) == 2
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("failure_stage", ["convert", "read", "decode", "callback"])
def test_inference_failure_always_cleans_owned_pcm(tmp_path, monkeypatch, failure_stage):
    monkeypatch.setattr(settings, "TEMP_DIR", tmp_path)
    engine = prepared_engine()

    def fail():
        raise RuntimeError("synthetic failure")

    def convert(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"test-pcm")
        if failure_stage == "convert":
            fail()

    def read(path):
        if failure_stage == "read":
            fail()
        return [0.0] * 160, 16000

    def progress(percent, message):
        if failure_stage == "callback" and percent == 40:
            fail()

    monkeypatch.setattr("subprocess.run", convert)
    monkeypatch.setattr("soundfile.read", read)
    if failure_stage == "decode":
        engine.recognizer.decode_stream.side_effect = RuntimeError("synthetic failure")
    with pytest.raises(RuntimeError, match="synthetic failure"):
        engine.transcribe(Path("source.wav"), progress_callback=progress)
    assert not list(tmp_path.iterdir())
