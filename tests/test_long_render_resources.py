"""Real small RF64/media paths plus bounded fault tests; no multi-hour run claimed."""
import json
import math
import shutil
import sys
import time
import wave
import errno
from pathlib import Path

import psutil
import pytest

from config import settings
from core.audio_ducking import PremiumAudioMixer
from core.engines.separator import realtime_suppressor as dsp
from core.media_process import run_media
from core.streaming import export
from core.streaming.export import HQExporter


has_media = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="Real FFmpeg required")


@pytest.fixture
def audio(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("Real FFmpeg required")
    path = tmp_path / "voice.wav"
    run_media(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
               "sine=frequency=880:sample_rate=44100:duration=0.5", "-ac", "2", str(path)])
    return path


def decoded_pcm(path):
    return run_media(["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-i", str(path),
                      "-f", "s16le", "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2", "pipe:1"],
                     capture_output=True)


def test_container_selection_crosses_real_32bit_riff_boundary_without_allocating_gigabytes():
    frames = export.RIFF_MAX_DATA_BYTES // 4
    assert export._voice_container(frames) == "WAV"
    assert export._voice_container(frames + 1) == "RF64"
    assert (frames + 1) / 44100 == pytest.approx(6.763 * 3600, rel=.001)


def test_small_voice_timeline_remains_wave_compatible(audio, tmp_path):
    destination = tmp_path / "timeline.wav"
    HQExporter._assemble_voice_timeline([{"id": 1, "start": .2, "end": .8,
        "audio_path": str(audio)}], 1, destination)
    assert destination.read_bytes()[:4] == b"RIFF"
    with wave.open(str(destination)) as stream:
        assert stream.getnframes() == 44100 and stream.getnchannels() == 2
        assert not any(stream.readframes(8820))
        assert any(stream.readframes(22050))


def test_actual_rf64_voice_timeline_decodes_identically_to_standard_wave(audio, tmp_path, monkeypatch):
    rows = [{"id": 1, "start": .2, "end": .8, "audio_path": str(audio)}]
    normal, large = tmp_path / "normal.wav", tmp_path / "rf64.wav"
    HQExporter._assemble_voice_timeline(rows, 1, normal)
    monkeypatch.setattr(export, "RIFF_MAX_DATA_BYTES", 1)  # Exercise RF64 using small real media.
    HQExporter._assemble_voice_timeline(rows, 1, large)
    assert large.read_bytes()[:4] == b"RF64"
    assert decoded_pcm(large) == decoded_pcm(normal)
    metadata = json.loads(run_media(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                    "-of", "json", str(large)], capture_output=True))
    assert float(metadata["format"]["duration"]) == pytest.approx(1, abs=1 / 44100)


def test_large_planned_timeline_uses_rf64_and_can_cancel_before_writing_large_artifact(tmp_path):
    # Seven hours is only the requested duration. Cancel after <=2 PCM chunks;
    # this proves header selection/cancellation, not a completed seven-hour run.
    checks = 0
    def cancel():
        nonlocal checks
        checks += 1
        return checks >= 3
    destination = tmp_path / "cancelled-rf64.wav"
    with pytest.raises(RuntimeError, match="hủy"):
        HQExporter._assemble_voice_timeline([], 7 * 3600, destination, cancel)
    assert destination.read_bytes()[:4] == b"RF64"
    assert destination.stat().st_size < 1024 * 1024


def test_disk_full_writing_timeline_propagates_as_failure(tmp_path, monkeypatch):
    def full_disk(_self, _data):
        raise OSError(errno.ENOSPC, "fault: disk full")
    monkeypatch.setattr(wave.Wave_write, "writeframesraw", full_disk)
    with pytest.raises(OSError) as caught:
        HQExporter._assemble_voice_timeline([], 1, tmp_path / "failed.wav")
    assert caught.value.errno == errno.ENOSPC


def test_file_backed_voice_decode_preserves_samples_and_removes_only_owned_scratch(audio, tmp_path, monkeypatch):
    rows = [{"id": 1, "start": .2, "end": .8, "audio_path": str(audio)}]
    normal, backed = tmp_path / "normal.wav", tmp_path / "backed.wav"
    keep = tmp_path / "user-existing.s16le"
    keep.write_bytes(b"keep user file")
    HQExporter._assemble_voice_timeline(rows, 1, normal)
    monkeypatch.setattr(export, "MAX_CAPTURE_PCM_SECONDS", .1)
    observed = []
    original = export.run_media
    def record(command, cancel_check=None, capture_output=False):
        observed.append((command, capture_output))
        return original(command, cancel_check, capture_output)
    monkeypatch.setattr(export, "run_media", record)
    HQExporter._assemble_voice_timeline(rows, 1, backed)
    assert all(not capture for _, capture in observed)
    assert decoded_pcm(backed) == decoded_pcm(normal)
    assert not list(tmp_path.glob("voice-decode-*"))
    assert keep.read_bytes() == b"keep user file"


def test_file_backed_oversize_speech_is_rejected_without_truncation_and_scratch_leak(audio, tmp_path, monkeypatch):
    monkeypatch.setattr(export, "MAX_CAPTURE_PCM_SECONDS", .1)
    with pytest.raises(ValueError, match="vượt khung lồng tiếng"):
        HQExporter._assemble_voice_timeline([{"id": 1, "start": .2, "end": .5,
                                            "audio_path": str(audio)}], 1, tmp_path / "bad.wav")
    assert not list(tmp_path.glob("voice-decode-*"))


def test_file_backed_decoder_failure_cleans_partial_pcm(audio, tmp_path, monkeypatch):
    monkeypatch.setattr(export, "MAX_CAPTURE_PCM_SECONDS", .1)
    def broken(command, *_args, **_kwargs):
        Path(command[-1]).write_bytes(b"partial decode")
        raise RuntimeError("fault: decoder failed")
    monkeypatch.setattr(export, "run_media", broken)
    with pytest.raises(RuntimeError, match="decoder failed"):
        HQExporter._assemble_voice_timeline([{"start": 0, "end": .7, "audio_path": str(audio)}],
                                            1, tmp_path / "bad.wav")
    assert not list(tmp_path.glob("voice-decode-*"))


@pytest.mark.parametrize("duration", [0, -1, float("nan"), float("inf")])
def test_invalid_timeline_duration_cannot_create_output(tmp_path, duration):
    destination = tmp_path / "invalid.wav"
    with pytest.raises(ValueError):
        HQExporter._assemble_voice_timeline([], duration, destination)
    assert not destination.exists()


def test_multi_hour_output_cannot_pass_with_minutes_of_missing_media(tmp_path, monkeypatch):
    path = tmp_path / "fixture.mp4"
    path.write_bytes(b"fault injection fixture")
    metadata = {"format": {"duration": "7100"}, "streams": [
        {"codec_type": "video", "width": 1920, "height": 1080}, {"codec_type": "audio"}]}
    # Old 1% tolerance accepted 71 seconds missing from 7200s.
    metadata["format"]["duration"] = "7135"
    monkeypatch.setattr(export, "run_media", lambda *_args, **_kwargs: json.dumps(metadata).encode())
    with pytest.raises(ValueError, match="thời lượng không khớp"):
        HQExporter.validate_output(path, 7200)


@pytest.mark.parametrize("response", [b"bad JSON", b"{}", b'{"streams": []}',
    b'{"streams": [{"sample_rate":"44100"}]}',
    b'{"streams": [{"channels":true,"sample_rate":"44100"}]}',
    b'{"streams": [{"channels":2,"sample_rate":true}]}',
    b'{"streams": [{"channels":2,"sample_rate":44100.5}]}',
    b'{"streams": [{"channels":2,"sample_rate":"0"}]}'])
def test_analysis_schema_errors_never_succeed_with_assumed_stereo(response, monkeypatch):
    monkeypatch.setattr(dsp, "run_media", lambda *_args, **_kwargs: response)
    with pytest.raises(ValueError, match="không tự giả định"):
        dsp.RealtimeVocalSuppressor().analyze_audio_properties(Path("source.wav"))


@pytest.mark.parametrize("pcm", [b"", b"odd", bytes(5 * 22050 * 4 + 4)], ids=["empty", "misaligned", "oversized"])
def test_bad_pcm_cannot_be_reported_as_measured_dual_mono(pcm, monkeypatch):
    outputs = iter([b'{"streams": [{"channels":2,"sample_rate":"44100"}]}', pcm])
    monkeypatch.setattr(dsp, "run_media", lambda *_args, **_kwargs: next(outputs))
    with pytest.raises(ValueError, match="mẫu PCM"):
        dsp.RealtimeVocalSuppressor().analyze_audio_properties(Path("source.wav"))


def sleeping_child_command(pid_file):
    script = "import os,time; from pathlib import Path; Path(%r).write_text(str(os.getpid())); time.sleep(60)" % str(pid_file)
    return [sys.executable, "-B", "-c", script]


def test_actual_hung_probe_times_out_and_child_is_reaped(tmp_path):
    pid_file = tmp_path / "child.pid"
    suppressor = dsp.RealtimeVocalSuppressor(analysis_timeout=.8)
    started = time.monotonic()
    children = []

    def observe_without_cancelling():
        if pid_file.is_file() and not children:
            pid = pid_file.read_text().strip()
            if pid.isdigit():
                # The timeout must reap this original process, not an instant
                # numeric PID which Windows may retain or recycle afterward.
                children.append(psutil.Process(int(pid)))
        return False

    with pytest.raises(TimeoutError, match="quá hạn"):
        suppressor._run_analysis_command(sleeping_child_command(pid_file),
                                        cancel_check=observe_without_cancelling)
    assert time.monotonic() - started < 3
    assert pid_file.is_file(), "Real child must have started before timeout"
    assert children and children[0].pid == int(pid_file.read_text())
    deadline = time.monotonic() + 1
    while children[0].is_running() and time.monotonic() < deadline:
        time.sleep(.01)
    assert not children[0].is_running()


def test_actual_user_cancel_is_not_mislabeled_timeout_and_reaps_child(tmp_path):
    pid_file = tmp_path / "child.pid"
    started = time.monotonic()
    children = []
    def cancel_check():
        if pid_file.is_file() and not children:
            # Compare process identity, not a PID that Windows can immediately
            # recycle for another application's new process.
            children.append(psutil.Process(int(pid_file.read_text())))
        return time.monotonic() - started >= .8
    with pytest.raises(RuntimeError, match="hủy"):
        dsp.RealtimeVocalSuppressor(analysis_timeout=30)._run_analysis_command(
            sleeping_child_command(pid_file), cancel_check=cancel_check)
    assert pid_file.is_file()
    assert children
    deadline = time.monotonic() + 1
    while children[0].is_running() and time.monotonic() < deadline:
        time.sleep(.01)
    assert not children[0].is_running()


@has_media
def test_actual_suppression_and_mixing_produce_decodable_full_duration_wave(audio, tmp_path):
    background, mixed = tmp_path / "background.wav", tmp_path / "master.wav"
    report = dsp.RealtimeVocalSuppressor().process_file(audio, background)
    assert report["duration"] == pytest.approx(.5, abs=.01)
    assert report["correlation"] > .99 and report["mode"] == "DSP_MONO_ADAPTIVE_FORMANT"
    PremiumAudioMixer().mix(background, audio, mixed, total_duration=.5)
    assert mixed.read_bytes()[:4] == b"RIFF"
    assert len(decoded_pcm(mixed)) == round(.5 * 44100) * 4
    assert math.isfinite(report["processing_time"]) and report["processing_time"] > 0


@has_media
def test_real_longer_audio_analysis_samples_exactly_five_seconds(tmp_path, monkeypatch):
    source = tmp_path / "stereo-six-seconds.wav"
    run_media(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
               "sine=frequency=440:sample_rate=44100:duration=6", "-f", "lavfi", "-i",
               "sine=frequency=880:sample_rate=44100:duration=6", "-filter_complex",
               "[0:a][1:a]amerge=inputs=2[a]", "-map", "[a]", str(source)])
    sizes = []
    original = dsp.run_media
    def record(command, cancel_check=None, capture_output=False):
        result = original(command, cancel_check, capture_output)
        if command[-1] == "pipe:1":
            sizes.append(len(result))
        return result
    monkeypatch.setattr(dsp, "run_media", record)
    analysis = dsp.RealtimeVocalSuppressor().analyze_audio_properties(source)
    assert sizes == [5 * 22050 * 4]
    assert analysis["mode"] == "DSP_STEREO_CENTER_CANCEL"


@pytest.mark.parametrize("duration", [b"", b"N/A", b"0", b"nan", b"inf"])
def test_missing_output_duration_never_returns_fake_one_second_success(duration, monkeypatch, tmp_path):
    source, output = tmp_path / "source.wav", tmp_path / "result.wav"
    outputs = iter([b'{"streams": [{"channels":1,"sample_rate":"44100"}]}', duration])
    def fake(command, cancel_check=None, capture_output=False):
        if capture_output:
            return next(outputs)
        output.write_bytes(b"test fault output")
        return b""
    monkeypatch.setattr(dsp, "run_media", fake)
    with pytest.raises(ValueError, match="chưa thể báo"):
        dsp.RealtimeVocalSuppressor().process_file(source, output)


def test_actual_rf64_timeline_can_be_suppressed_mixed_rendered_and_fully_decoded(audio, tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    run_media(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
               "color=c=blue:s=160x240:r=10:d=1", "-f", "lavfi", "-i",
               "sine=frequency=440:sample_rate=44100:duration=1", "-c:v", "libx264",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)])
    for name in ("TEMP_DIR", "OUTPUT_DIR", "WORKSPACE_DIR"):
        monkeypatch.setattr(settings, name, tmp_path)
    monkeypatch.setattr(settings, "SEPARATION_ENGINE", "dsp")
    monkeypatch.setattr(export, "RIFF_MAX_DATA_BYTES", 1)
    result = HQExporter().export("real-rf64", source, [{"id": 1, "start": .2, "end": .8,
        "audio_path": str(audio), "final_vi": "Xin chào"}], 1)
    HQExporter.validate_output(result["output_path"], 1)  # Full actual audio/video decode.
    assert Path(result["output_path"]).stat().st_size > 0
    assert not list(tmp_path.glob("hq_export_*"))
