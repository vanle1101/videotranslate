"""Legacy saved MP4s must validate each track, not just the longest track."""
import shutil

import pytest

from core.media_process import run_media
from core.streaming.session_store import _valid_output


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")
@pytest.mark.parametrize("video_length,audio_length", [(2, .3), (.3, 2), (2, 2)])
def test_saved_output_checks_each_track_without_mutating_media(tmp_path, video_length, audio_length):
    path = tmp_path / "saved.mp4"
    run_media(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
               f"color=black:s=64x64:r=10:d={video_length}", "-f", "lavfi", "-i",
               f"sine=frequency=440:duration={audio_length}", "-c:v", "libx264",
               "-threads", "1", "-c:a", "aac", str(path)])
    before = path.read_bytes()
    assert _valid_output(path, 2) is (video_length == audio_length)
    assert path.read_bytes() == before
