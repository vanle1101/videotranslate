from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.engines.asr.sensevoice_engine import SenseVoiceEngine
from core.engines.asr.native_process import ASRProcessError


@pytest.fixture
def decode(tmp_path, monkeypatch):
    import soundfile
    import subprocess
    from config import settings
    monkeypatch.setattr(settings, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(subprocess, "run", Mock())
    monkeypatch.setattr(soundfile, "read", lambda _: ([0.] * 32000, 16000))

    def run(result, *, progress_callback=None):
        engine = SenseVoiceEngine()
        stream = SimpleNamespace(result=result, accept_waveform=Mock())
        recognizer = SimpleNamespace(create_stream=lambda: stream, decode_stream=Mock())
        engine.recognizer = recognizer
        rows = engine.transcribe(tmp_path / "source.wav", language="zh", progress_callback=progress_callback)
        assert engine.recognizer is recognizer and engine.language == ""
        return rows
    return run


@pytest.mark.parametrize("label,code,text", [
    ("<|ko|>", "ko", "우리 친구잖아?"), ("<|ja|>", "ja", "友達ですよ。"),
    ("<|en|>", "en", "We are friends."), ("<|zh|>", "zh", "我们是朋友。"),
    (" KO ", "ko", "우리 친구잖아?"), ("zh", "zh", "我们是朋友。"),
])
def test_detected_language_routes_even_when_legacy_argument_is_chinese(decode, label, code, text):
    rows = decode(SimpleNamespace(text=text, lang=label, emotion="<|NEUTRAL|>"))
    assert rows and all(row["source_language"] == code for row in rows)
    assert "".join(row["text_zh"] for row in rows) == text
    assert all(row["source_language_uncertain"] is True and row["source_language_probability"] == 0.
               and "verification" not in row for row in rows)


@pytest.mark.parametrize("label", [None, "", "auto", "<|nospeech|>", "zz", "<|xx|>", 3, {}, "<|ko|><|zh|>"])
def test_missing_or_unsupported_label_never_defaults_to_chinese(decode, label):
    with pytest.raises(ASRProcessError) as caught:
        decode(SimpleNamespace(text="우리 친구잖아?", lang=label))
    assert caught.value.code == "source_language_unrecognized"


def test_absent_language_attribute_is_conservative(decode):
    with pytest.raises(ASRProcessError):
        decode(SimpleNamespace(text="We are friends."))


def test_empty_result_retains_existing_empty_behavior(decode):
    assert decode(SimpleNamespace(text=" ")) == []


def test_progress_is_multilingual_and_existing_timing_allocation_retained(decode):
    progress = Mock()
    rows = decode(SimpleNamespace(text="こんにちは。元気？", lang="<|ja|>"), progress_callback=progress)
    assert [row["start"] for row in rows] == [0., 1.]
    assert [row["end"] for row in rows] == [1., 2.]
    assert not any("tiếng Trung" in call.args[1] for call in progress.call_args_list)


def test_unknown_language_temp_audio_is_cleaned(decode, tmp_path):
    with pytest.raises(ASRProcessError):
        decode(SimpleNamespace(text="hello", lang="unknown"))
    assert not list(tmp_path.glob("sensevoice-*"))
