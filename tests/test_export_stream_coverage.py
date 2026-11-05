"""Full decode and container length cannot prove both tracks cover the video."""
import json
import shutil

import pytest

from core.media_process import run_media
from core.streaming.export import HQExporter


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")
@pytest.mark.parametrize("video_length,audio_length", [(2, .3), (.3, 2), (2, 2)])
def test_real_export_requires_both_tracks_to_cover_the_source(tmp_path, video_length, audio_length):
    result = tmp_path / "tracks.mp4"
    run_media(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
               f"color=black:s=64x64:r=10:d={video_length}", "-f", "lavfi", "-i",
               f"sine=frequency=440:duration={audio_length}", "-c:v", "libx264",
               "-threads", "1", "-c:a", "aac", str(result)])
    metadata = json.loads(run_media(["ffprobe", "-v", "error", "-show_entries",
                                    "format=duration", "-of", "json", str(result)], capture_output=True))
    assert float(metadata["format"]["duration"]) == pytest.approx(2, abs=.05)
    # Both truncated tracks are valid media: decoding alone does not reject them.
    run_media(["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-i", str(result),
               "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"])
    if min(video_length, audio_length) < 2:
        with pytest.raises(ValueError, match="thời lượng không khớp"):
            HQExporter.validate_output(result, 2)
    else:
        HQExporter.validate_output(result, 2)


@pytest.mark.parametrize("field,value", [("start_time", "1"), ("start_time", "nan"),
    ("duration", "inf"), ("duration", "N/A"), ("duration", None), ("duration", "0")])
def test_missing_or_invalid_track_timing_does_not_inherit_container_duration(tmp_path, monkeypatch, field, value):
    from core.streaming import export
    result = tmp_path / "metadata.mp4"
    result.write_bytes(b"mock media")
    metadata = {"format": {"duration": "2"}, "streams": [
        {"codec_type": "video", "width": 64, "height": 64, "start_time": "0", "duration": "2"},
        {"codec_type": "audio", "start_time": "0", "duration": "2"}]}
    if value is None:
        metadata["streams"][1].pop(field)
    else:
        metadata["streams"][1][field] = value
    commands = []
    def media(command, *_args, **_kwargs):
        commands.append(command)
        return json.dumps(metadata).encode()
    monkeypatch.setattr(export, "run_media", media)
    with pytest.raises(ValueError, match="thời lượng không khớp"):
        HQExporter.validate_output(result, 2)
    assert len(commands) == 1  # Reject before spending time on a full decode.
