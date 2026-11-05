"""Real-PCM regressions for direct alignment callers retaining prior audio."""
from array import array
import math
import shutil
import subprocess
import wave

import pytest

from config import settings
from core.engines.alignment.timing_aligner import TimingBudgetAligner, SpeechBudgetError


def write_voice(path, seconds):
    rate = 24000
    pcm = array("h", (round(6000 * math.sin(2 * math.pi * 440 * frame / rate))
                      for frame in range(round(seconds * rate))))
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(pcm.tobytes())


@pytest.fixture
def aligner(tmp_path, monkeypatch):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("Real FFmpeg and FFprobe are required")
    monkeypatch.setattr(settings, "TEMP_DIR", tmp_path)
    result = TimingBudgetAligner()
    result._rubberband_available = False
    return result


@pytest.mark.parametrize("previous_exists", [True, False])
def test_unfit_direct_render_never_publishes_overlong_audio(tmp_path, aligner, previous_exists):
    source, output = tmp_path / "source.wav", tmp_path / "published.wav"
    write_voice(source, 1.4)
    if previous_exists:
        write_voice(output, .4)
    previous = output.read_bytes() if previous_exists else None

    with pytest.raises(SpeechBudgetError):
        aligner.apply_atempo(source, output, 1.0, fit_duration=1.0)

    assert output.exists() is previous_exists
    if previous_exists:
        assert output.read_bytes() == previous
    assert not list(tmp_path.glob("align-fit-*"))


def test_ffmpeg_failure_keeps_previous_published_audio(tmp_path, aligner):
    source, output = tmp_path / "invalid.wav", tmp_path / "published.wav"
    source.write_bytes(b"invalid audio input")
    write_voice(output, .4)
    previous = output.read_bytes()

    with pytest.raises(subprocess.CalledProcessError):
        aligner.apply_atempo(source, output, 1.0, fit_duration=1.0)

    assert output.read_bytes() == previous
    assert not list(tmp_path.glob("align-fit-*"))


def test_successful_direct_fit_publishes_complete_pcm(tmp_path, aligner):
    source, output = tmp_path / "source.wav", tmp_path / "published.wav"
    write_voice(source, .75)
    write_voice(output, .4)
    previous = output.read_bytes()

    assert aligner.apply_atempo(source, output, 1.0, fit_duration=1.0) == 1.0

    assert output.read_bytes() != previous
    assert aligner.get_audio_duration(output) == pytest.approx(.75, abs=.015)
    with wave.open(str(output), "rb") as audio:
        pcm = array("h", audio.readframes(audio.getnframes()))
    assert max(abs(sample) for sample in pcm[-480:]) > 100
    assert not list(tmp_path.glob("align-fit-*"))


def test_nonfinite_media_probe_cannot_replace_prior_audio(tmp_path, aligner, monkeypatch):
    source, output = tmp_path / "source.wav", tmp_path / "published.wav"
    write_voice(source, .75)
    write_voice(output, .4)
    previous = output.read_bytes()
    monkeypatch.setattr(aligner, "get_audio_duration", lambda path: float("nan"))

    with pytest.raises(RuntimeError, match="Không đọc được"):
        aligner.apply_atempo(source, output, 1.0)

    assert output.read_bytes() == previous
    assert not list(tmp_path.glob("align-fit-*"))
