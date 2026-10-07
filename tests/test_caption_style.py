"""User caption styles share a measured layout between preview and export."""
import shutil
import subprocess

import pytest

from core.subtitle import SubtitleGenerator
from core.subtitle_cues import build_caption_layout, normalize_caption_style
from core.video_composer import _escape_filter_filename


def speech(**changes):
    return {"id": 1, "start": 0, "end": 3, "final_vi": "Về nhất, phá kỷ lục của trường.",
            "speech_start": .4, "speech_end": 2.8, "subtitle_timing_source": "edge-word-boundary",
            "subtitle_cues": [{"start": .4, "end": 2.8, "text": "Về nhất, phá kỷ\nlục của trường."}],
            **changes}


def source(**changes):
    return {"id": "ocr1", "start": 0, "end": 3, "kind": "subtitle", "source_method": "local-ocr",
            "confidence": .98, "needs_review": False, "bbox": [.3, .7, .4, .04], **changes}


def test_style_defaults_are_compatible_and_independent():
    defaults = normalize_caption_style()
    assert defaults == {"background_color": None, "text_color": "#000000", "position": "auto", "blur_original": False}
    assert build_caption_layout([speech()], [source()], (1920, 1080), defaults) == build_caption_layout([speech()], [source()], (1920, 1080))
    options = {"background_color": "#fe12ab", "text_color": "#12aBfE"}
    assert normalize_caption_style(options)["background_color"] == "#FE12AB"
    assert normalize_caption_style(options)["text_color"] == "#12ABFE"
    assert options["background_color"] == "#fe12ab"


@pytest.mark.parametrize("style", [False, [], "yellow", {"unknown": True}, {"background_color": "red"},
    {"background_color": "#fff"}, {"text_color": None}, {"text_color": "#FFFFFF;bad"},
    {"text_color": "#FFFFFF\\1a&HFF&"}, {"position": "left"}, {"position": []},
    {"blur_original": 1}, {"blur_original": "false"}])
def test_malformed_style_is_rejected(style):
    with pytest.raises(ValueError):
        normalize_caption_style(style)
    with pytest.raises(ValueError):
        build_caption_layout([speech()], [source()], caption_style=style)


@pytest.mark.parametrize("position", ["top", "middle", "bottom"])
def test_placement_and_colors_change_without_altering_timing_or_words(position):
    style = {"position": position, "background_color": "#123456", "text_color": "#FEDCBA"}
    base = build_caption_layout([speech()], [source()], (1920, 1080))["cues"][0]
    cue = build_caption_layout([speech()], [source()], (1920, 1080), style)["cues"][0]
    assert (cue["start"], cue["end"], cue["text"]) == (base["start"], base["end"], base["text"])
    assert cue["text"] == speech()["final_vi"]
    assert cue["background"] == "custom" and cue["background_color"] == "#123456" and cue["color"] == "#FEDCBA"
    x, y, w, h = cue["bbox"]
    assert x + w / 2 == pytest.approx(.5)
    assert .039 <= y <= y + h <= .961
    assert {"top": y, "middle": y + h / 2, "bottom": y + h}[position] == pytest.approx({"top": .04, "middle": .5, "bottom": .96}[position])
    assert cue["placement"] == position


def test_custom_placement_is_stable_across_pages_and_ocr():
    segment = speech(subtitle_cues=[{"start": .4, "end": 1.5, "text": "Ngắn."},
                                   {"start": 1.5, "end": 2.8, "text": "Câu dài hơn nhưng khung không nhảy."}])
    cues = build_caption_layout([segment], [source(end=1.2), source(start=1.2, bbox=[.2, .85, .5, .05])],
                                (1920, 1080), {"position": "top"})["cues"]
    assert cues[0]["bbox"] == cues[1]["bbox"]
    assert cues[0]["font_size"] == cues[1]["font_size"]
    assert [(c["start"], c["end"]) for c in cues] == [(.4, 1.5), (1.5, 2.8)]


def test_blur_masks_are_only_measured_overlaps_and_merge_page_boundaries():
    segment = speech(subtitle_cues=[{"start": .4, "end": 1.2, "text": "Một câu."},
                                   {"start": 1.2, "end": 1.8, "text": "Câu tiếp."},
                                   {"start": 2.4, "end": 2.8, "text": "Sau khoảng nghỉ."}])
    plan = build_caption_layout([segment], [source(start=.7, end=2.6), source(start=10, end=12)],
                                caption_style={"blur_original": True})
    assert plan["source_masks"] == [{"start": .7, "end": 1.8, "bbox": [.3, .7, .4, .04]},
                                    {"start": 2.4, "end": 2.6, "bbox": [.3, .7, .4, .04]}]
    assert "source_masks" not in build_caption_layout([segment], [source()])


@pytest.mark.parametrize("changes", [{"kind": "title"}, {"needs_review": True}, {"confidence": .89},
    {"source_method": "video-ai"}, {"bbox": [-.1, .7, .4, .04]}, {"bbox": [.1, .7, .9, .2]},
    {"bbox": [.8, .7, .4, .04]}, {"end": float("inf")}])
def test_untrusted_ocr_or_title_never_blurred(changes):
    plan = build_caption_layout([speech()], [source(**changes)], caption_style={"blur_original": True})
    assert plan["source_masks"] == []


@pytest.mark.parametrize("changes", [{"needs_review": True}, {"needs_review": True, "preview_is_draft": True},
                                    {"confirmed_silence": True}, {"subtitle_cues": []}])
def test_unapproved_or_silent_speech_never_masks_source(changes):
    assert build_caption_layout([speech(**changes)], [source()], caption_style={"blur_original": True})["source_masks"] == []


def test_independently_verified_region_remains_usable_when_ocr_translation_needs_review():
    plan = build_caption_layout([speech()], [source(needs_review=True, source_region_verified=True)],
                                caption_style={"blur_original": True})
    assert plan["source_masks"] == [{"start": .4, "end": 2.8, "bbox": [.3, .7, .4, .04]}]


def test_ass_uses_rgb_colors_as_bgr_and_matches_preview_coordinates(tmp_path):
    style = {"background_color": "#123456", "text_color": "#ABCDEF", "position": "top", "blur_original": True}
    plan = build_caption_layout([speech()], [source()], (1920, 1080), style)
    ass = SubtitleGenerator().generate_ass([speech()], tmp_path / "style.ass", screen_texts=[source()],
                                           video_size=(1920, 1080), caption_style=style).read_text(encoding="utf-8")
    assert r"\1c&H563412&" in ass and r"\1c&HEFCDAB&" in ass
    assert "0:00:00.40,0:00:02.80" in ass
    x, y, w, h = plan["cues"][0]["bbox"]
    assert f"\\pos({(x+w/2)*1920:.3f},{(y+h/2)*1080:.3f})" in ass
    assert ass.count("Dialogue:") == 2, "ASS must not substitute an opaque rectangle for source blur"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg/libass required")
def test_real_libass_frame_has_chosen_background_and_text_in_requested_position(tmp_path):
    style = {"background_color": "#FF0000", "text_color": "#00FF00", "position": "top"}
    size = (640, 360)
    plan = build_caption_layout([speech()], [source()], size, style)
    path = SubtitleGenerator().generate_ass([speech()], tmp_path / "render.ass", screen_texts=[source()],
                                            video_size=size, caption_style=style)
    completed = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "lavfi", "-i", "color=c=blue:s=640x360:d=0.1", "-vf",
        f"setpts=PTS+1/TB,ass=filename={_escape_filter_filename(path)}", "-frames:v", "1",
        "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"], capture_output=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    frame = completed.stdout
    assert len(frame) == size[0] * size[1] * 3
    red, green = [], []
    for pixel, (r, g, b) in enumerate(zip(frame[::3], frame[1::3], frame[2::3])):
        if r > 150 and g < 90 and b < 90:
            red.append((pixel % size[0], pixel // size[0]))
        if g > 90 and r < 130 and b < 100:
            green.append((pixel % size[0], pixel // size[0]))
    assert len(red) > 100 and len(green) > 5
    x, y, w, h = plan["cues"][0]["bbox"]
    assert all(x * size[0] - 2 <= px <= (x+w) * size[0] + 2 and
               y * size[1] - 2 <= py <= (y+h) * size[1] + 2 for px, py in red + green)
