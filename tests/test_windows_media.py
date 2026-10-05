"""Offline regression checks using small, real FFmpeg inputs on Windows paths."""

import json
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from config import settings
from core.audio_ducking import PremiumAudioMixer
from core.engines.alignment.timing_aligner import TimingBudgetAligner
from core.engines.tts.edge_fallback import EdgeTTSFallbackEngine
from core.streaming.segmenter import AudioSegmenter
from core.subtitle import SubtitleGenerator
from core.video_composer import VideoComposer


def ffmpeg(*args):
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *map(str, args)],
        capture_output=True,
        check=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def probe(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True,
        check=True,
        timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def media():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and ffprobe are required for offline media integration checks")
    settings.TEMP_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="media-regression-", dir=settings.TEMP_DIR) as tmp:
        folder = Path(tmp) / "Tiếng Việt O'Brien [bản 1],x;"
        folder.mkdir()
        video = folder / "video gốc.mp4"
        bgm = folder / "nhạc nền.wav"
        voice = folder / "giọng đọc.wav"
        subtitles = folder / "phụ đề O'Brien [1],x;.ass"
        ffmpeg("-f", "lavfi", "-i", "color=c=black:s=180x320:r=15:d=1.2", "-c:v", "libx264", "-pix_fmt", "yuv420p", video)
        ffmpeg("-f", "lavfi", "-i", "sine=frequency=220:duration=1.2", bgm)
        ffmpeg("-f", "lavfi", "-i", "sine=frequency=880:duration=0.6", voice)
        SubtitleGenerator().generate_ass(
            [{"start": 0, "end": 1.2, "vi_text": "Xin chào Việt Nam"}], subtitles
        )
        yield folder, video, bgm, voice, subtitles


@pytest.mark.parametrize("duration", [None, 1.5])
def test_mixer_splits_sidechain_and_preserves_duration(media, duration):
    folder, _, bgm, voice, _ = media
    output = folder / f"mix {duration}.wav"
    PremiumAudioMixer().mix(bgm, voice, output, total_duration=duration)
    metadata = probe(output)
    assert float(metadata["format"]["duration"]) == pytest.approx(duration or 1.2, abs=0.03)
    assert metadata["streams"][0]["channels"] == 2


@pytest.mark.parametrize("mask", [False, True])
def test_compose_unicode_paths_burns_subtitles_and_keeps_video_length(media, mask):
    folder, video, bgm, voice, subtitles = media
    mixed = folder / f"master {mask}.wav"
    # Short audio must be padded by the composer instead of cutting off video.
    PremiumAudioMixer().mix(bgm, voice, mixed, total_duration=0.8)
    output = folder / "xuất video" / f"kết quả {mask}.mp4"
    VideoComposer().compose(video, mixed, subtitles, output, mask_chinese_sub=mask)
    metadata = probe(output)
    assert {stream["codec_name"] for stream in metadata["streams"]} == {"h264", "aac"}
    assert float(metadata["format"]["duration"]) == pytest.approx(1.2, abs=0.12)
    frame = ffmpeg("-ss", "0.4", "-i", output, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1").stdout
    # The source is black. Yellow pixels prove that the ASS text was burned in.
    assert sum(r > 100 and g > 100 and b < 80 for r, g, b in zip(frame[::3], frame[1::3], frame[2::3])) > 10


def test_missing_subtitles_reports_failure_instead_of_silent_fallback(media):
    folder, video, bgm, _, _ = media
    with pytest.raises(RuntimeError, match="FFmpeg video composition failed"):
        VideoComposer().compose(video, bgm, folder / "missing.ass", folder / "failed.mp4", False)


def test_subtitle_rounding_carries_to_the_next_second():
    generator = SubtitleGenerator()
    assert generator._format_time_ass(59.9999) == "0:01:00.00"
    assert generator._format_time_srt(59.9999) == "00:01:00,000"
    assert generator._format_time_ass(-0.1) == "0:00:00.00"


def test_mixer_honors_saved_machine_gains(monkeypatch):
    monkeypatch.setattr(settings, "BGM_VOLUME_DUCKED_DB", -24)
    monkeypatch.setattr(settings, "BGM_VOLUME_NORMAL_DB", -5)
    monkeypatch.setattr(settings, "VOICE_VOLUME_BOOST_DB", 1)
    mixer = PremiumAudioMixer()
    assert (mixer.duck_amount_db, mixer.bgm_gain_db, mixer.voice_gain_db) == (-24, -5, 1)


@contextmanager
def background_launches(target):
    """Reject visible launches before running real tiny media subprocesses."""
    real_run = subprocess.run

    def checked_run(*args, **kwargs):
        assert kwargs.get("creationflags") == getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return real_run(*args, **kwargs)

    with patch(target, side_effect=checked_run) as launch:
        yield launch


def test_edge_converts_service_mp3_to_real_pcm_without_console_windows(media):
    folder, _, _, voice, _ = media
    service_audio = folder / "service.mp3"
    ffmpeg("-i", voice, service_audio)
    service_bytes = service_audio.read_bytes()

    async def save_response(path):
        Path(path).write_bytes(service_bytes)

    output = folder / "giọng Edge.wav"
    with patch("core.engines.tts.edge_fallback.edge_tts.Communicate",
               return_value=SimpleNamespace(save=save_response)), \
            background_launches("core.engines.tts.edge_fallback.subprocess.run") as launch:
        result = EdgeTTSFallbackEngine().synthesize("Xin chào", output)
    assert [call.args[0][0] for call in launch.call_args_list] == ["ffmpeg"]
    assert result == output
    metadata = probe(output)
    audio = metadata["streams"][0]
    assert (audio["codec_name"], audio["sample_rate"], audio["channels"]) == ("pcm_s16le", "24000", 1)
    assert float(metadata["format"]["duration"]) == pytest.approx(0.6, abs=0.03)
    assert not list(folder.glob("edge_tts_*"))


def test_repeated_alignment_duration_probes_stay_in_background(media, monkeypatch):
    folder, _, bgm, voice, _ = media
    monkeypatch.setattr(settings, "TEMP_DIR", folder)
    aligner = TimingBudgetAligner()
    with background_launches("core.engines.alignment.timing_aligner.subprocess.run") as launch:
        durations = [aligner.get_audio_duration(path) for path in (voice, bgm, voice)]
    assert durations == pytest.approx([0.6, 1.2, 0.6], abs=0.03)
    assert [call.args[0][0] for call in launch.call_args_list] == ["ffprobe"] * 3


def test_sentence_segmentation_probes_and_detects_silence_in_background(media):
    folder, *_ = media
    source = folder / "hai câu.wav"
    ffmpeg("-f", "lavfi", "-i",
           "sine=frequency=440:duration=1.4[a];anullsrc=r=44100:cl=mono:d=0.6[b];"
           "sine=frequency=880:duration=1.4[c];[a][b][c]concat=n=3:v=0:a=1", source)
    with background_launches("core.streaming.segmenter.subprocess.run") as launch:
        segments = AudioSegmenter().segment_audio(source)
    assert len(segments) == 2
    assert [segment["start"] for segment in segments] == pytest.approx([0, 2.0], abs=0.02)
    assert [segment["end"] for segment in segments] == pytest.approx([1.4, 3.4], abs=0.02)
    assert [call.args[0][0] for call in launch.call_args_list] == ["ffprobe", "ffmpeg"]
