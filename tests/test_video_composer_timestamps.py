import json

from core import video_composer
from core.video_composer import VideoComposer


def test_video_start_time_uses_first_frame_when_stream_header_lies(tmp_path, monkeypatch):
    source = tmp_path / "offset.webm"
    source.write_bytes(b"fixture")
    metadata = {
        "format": {"duration": "30.000000"},
        "streams": [{"start_time": "0.000000"}],
        "frames": [{"best_effort_timestamp_time": "9.979000"}],
    }
    commands = []

    def probe(command, *_args, **_kwargs):
        commands.append(command)
        return json.dumps(metadata).encode()

    monkeypatch.setattr(video_composer, "run_media", probe)

    assert VideoComposer._video_start_time(source) == 9.979
    assert "-show_frames" in commands[0]
    assert any("best_effort_timestamp_time" in arg for arg in commands[0])
