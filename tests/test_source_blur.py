"""Selective source-subtitle blur, including real FFmpeg pixel checks."""

import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pytest

from config import settings
from core.video_composer import VideoComposer, _composition_filter, _normalize_source_masks


def test_source_masks_require_finite_time_and_real_intersecting_boxes():
    masks = [
        {"start": -1, "end": 2, "bbox": [-.1, .2, .4, .3]},
        {"start": 2, "end": 3, "bbox": [0, .2, .3, .3]},
        {"start": 1, "end": 2, "bbox": [0, .2, .3, .3]},
        {"start": 2, "end": 1, "bbox": [0, 0, 1, 1]},
        {"start": float("nan"), "end": 1, "bbox": [0, 0, 1, 1]},
        {"start": 0, "end": 1, "bbox": [0, float("inf"), 1, 1]},
        {"start": 0, "end": 1, "bbox": [1.2, 0, .1, .2]},
        {"start": 0, "end": 1, "bbox": [0, 0, -.2, .2]},
        {"start": 0, "end": 1, "bbox": [0, 0, 0, .2]},
        {"start": 0, "end": 1, "bbox": [1 - 1e-10, 0, 1e-10, .2]},
        {"start": 0, "end": 1, "bbox": [0, 0, .2]},
        None,
        {"start": "bad", "end": 1, "bbox": [0, 0, .2, .2]},
    ]
    assert _normalize_source_masks(masks) == [{"bbox": (0, .2, .3, .3), "windows": [[0, 3]]}]
    assert _normalize_source_masks(None) == []
    with pytest.raises(ValueError, match="danh sách"):
        _normalize_source_masks("not a mask list")


def test_blur_graph_is_bounded_and_repeat_observations_share_one_region():
    mask = {"start": 0, "end": 1, "bbox": [.1, .2, .3, .1]}
    assert len(_normalize_source_masks([mask] * 1000)) == 1
    distinct = [{**mask, "bbox": [i / 1000, .2, .3, .1]} for i in range(129)]
    with pytest.raises(ValueError, match="quá nhiều vùng"):
        _normalize_source_masks(distinct)
    filters = _composition_filter(Path("captions.ass"), [mask, {**mask, "start": 2, "end": 3}])
    assert filters.count("boxblur=") == 1
    assert "gte(t,0.00000000)*lt(t,1.00000000)+gte(t,2.00000000)*lt(t,3.00000000)" in filters
    assert filters.index("boxblur=") < filters.index("subtitles=")
    assert "crop=" not in _composition_filter(Path("captions.ass"))


def _ffmpeg(*args):
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *map(str, args)],
        capture_output=True, check=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout


@pytest.fixture(scope="module")
def rendered_source_masks():
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg required for source-region pixel checks")
    settings.TEMP_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="source-blur-qa-", dir=settings.TEMP_DIR) as directory:
        folder = Path(directory)
        video, audio, ass = folder / "source.mkv", folder / "voice.wav", folder / "caption.ass"
        _ffmpeg("-f", "lavfi", "-i", "nullsrc=s=160x96:r=10:d=1.6,geq=lum='if(lt(mod(X,8),4),16,235)':cb=128:cr=128",
                "-c:v", "ffv1", video)
        _ffmpeg("-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono:d=1.6", audio)
        # A crisp red ASS square inside the source region proves captions are
        # painted after blur, instead of blurring the translated caption too.
        ass.write_text("""[Script Info]
ScriptType: v4.00+
PlayResX: 160
PlayResY: 96
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,12,&H000000FF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:01.60,Default,,0,0,0,,{\\pos(62,34)\\an7\\p1\\bord0\\shad0}m 0 0 l 10 0 10 10 0 10
""", encoding="utf-8")
        outputs = {}
        for enabled in (False, True):
            output = folder / f"blur-{enabled}.mp4"
            VideoComposer().compose(video, audio, ass, output, source_masks=(
                [{"start": .4, "end": 1.2, "bbox": [.25, .25, .5, .5]}] if enabled else None))
            raw = _ffmpeg("-i", output, "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1")
            outputs[enabled] = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 96, 160, 3)
        yield outputs


def test_actual_blur_only_changes_source_region_during_its_window(rendered_source_masks):
    original, blurred = rendered_source_masks[False], rendered_source_masks[True]
    assert original.shape == blurred.shape == (16, 96, 160, 3)
    # Skip the ASS square: this crop consists entirely of source stripe pixels.
    plain_roi, blurred_roi = original[7, 48:66, 48:112], blurred[7, 48:66, 48:112]
    assert np.std(plain_roi.astype(float)) > 80
    assert np.std(blurred_roi.astype(float)) < np.std(plain_roi.astype(float)) * .30
    # Lossy output can change a pixel by a few levels even outside the mask.
    delta = np.abs(original.astype(float) - blurred.astype(float))
    assert delta[7, :16, :].mean() < 3
    assert delta[7, :, :24].mean() < 3
    assert delta[1].mean() < 3, "Blur must not start before the source appears"
    assert delta[13].mean() < 3, "Blur must end when the source disappears"


def test_vietnamese_ass_layer_remains_sharp_over_blurred_source(rendered_source_masks):
    frame = rendered_source_masks[True][7]
    square = frame[36:42, 64:70].astype(float)
    assert square[:, :, 0].mean() > 220
    assert square[:, :, 1:].mean() < 30


def test_tiny_edge_region_is_safe_for_real_ffmpeg():
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg required")
    # Validate the potentially tricky 2x2/chroma-radius-zero case directly.
    _ffmpeg("-f", "lavfi", "-i", "color=white:s=16x16:d=0.1", "-vf",
            "crop=2:2:14:14,boxblur=luma_radius='min(20,min(w,h)/10)':luma_power=2:"
            "chroma_radius='min(10,min(cw,ch)/10)':chroma_power=2", "-f", "null", "-")
