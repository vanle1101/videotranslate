"""User caption styles share a measured layout between preview and export."""
import shutil
import subprocess

import pytest

from core.subtitle import SubtitleGenerator
from core.subtitle_cues import CAPTION_FONTS, build_caption_layout, normalize_caption_style
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
    assert defaults == {"background_color": None, "text_color": "#000000", "position": "auto", "blur_original": False,
                        "background_opacity": 100, "font_family": "Arial", "font_scale": 100,
                        "bold": True, "outline": False}
    assert build_caption_layout([speech()], [source()], (1920, 1080), defaults) == build_caption_layout([speech()], [source()], (1920, 1080))
    options = {"background_color": "#fe12ab", "text_color": "#12aBfE"}
    assert normalize_caption_style(options)["background_color"] == "#FE12AB"
    assert normalize_caption_style(options)["text_color"] == "#12ABFE"
    assert options["background_color"] == "#fe12ab"


@pytest.mark.parametrize("style", [False, [], "yellow", {"unknown": True}, {"background_color": "red"},
    {"background_color": "#fff"}, {"text_color": None}, {"text_color": "#FFFFFF;bad"},
    {"text_color": "#FFFFFF\\1a&HFF&"}, {"position": "left"}, {"position": []},
    {"blur_original": 1}, {"blur_original": "false"},
    {"background_opacity": -1}, {"background_opacity": 101}, {"background_opacity": True},
    {"background_opacity": "0"}, {"background_opacity": 50.0}, {"background_opacity": None},
    {"font_scale": 49}, {"font_scale": 201}, {"font_scale": True}, {"font_scale": "100"},
    {"font_scale": 100.0}, {"font_scale": float("nan")}, {"font_scale": float("inf")},
    {"font_family": "Arial\\1a&HFF&"}, {"font_family": "Arial,1"}, {"font_family": "arial"},
    {"font_family": None}, {"font_family": []}, {"bold": 1}, {"bold": "true"},
    {"outline": 0}, {"outline": "false"}])
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


def test_blur_masks_follow_source_time_and_ignore_tts_page_gaps():
    segment = speech(subtitle_cues=[{"start": .4, "end": 1.2, "text": "Một câu."},
                                   {"start": 1.2, "end": 1.8, "text": "Câu tiếp."},
                                   {"start": 2.4, "end": 2.8, "text": "Sau khoảng nghỉ."}])
    plan = build_caption_layout([segment], [source(start=.7, end=2.6), source(start=10, end=12)],
                                caption_style={"blur_original": True})
    assert plan["source_masks"] == [{"start": .6, "end": 2.7, "bbox": [.3, .7, .4, .04]}]
    assert "source_masks" not in build_caption_layout([segment], [source()])


@pytest.mark.parametrize("changes", [{"kind": "title"}, {"needs_review": True}, {"confidence": .89},
    {"source_method": "video-ai"}, {"bbox": [-.1, .7, .4, .04]}, {"bbox": [.1, .7, .9, .2]},
    {"bbox": [.8, .7, .4, .04]}, {"end": float("inf")}])
def test_untrusted_ocr_or_title_never_blurred(changes):
    plan = build_caption_layout([speech()], [source(**changes)], caption_style={"blur_original": True})
    assert plan["source_masks"] == []


@pytest.mark.parametrize("changes", [{"needs_review": True},
                                    {"confirmed_silence": True}, {"subtitle_cues": []}])
def test_speech_quality_cannot_suppress_independently_verified_source_blur(changes):
    plan = build_caption_layout([speech(**changes)], [source()], caption_style={"blur_original": True})
    assert plan["cues"] == []
    assert plan["source_masks"] == [{"start": 0, "end": 3.1, "bbox": [.3, .7, .4, .04]}]


def test_playable_preview_blur_uses_verified_geometry_without_approving_draft_translation():
    segment = speech(needs_review=True, preview_is_draft=True)
    plan = build_caption_layout([segment], [source()], caption_style={"blur_original": True})
    assert plan["source_masks"] == [{"start": 0, "end": 3.1, "bbox": [.3, .7, .4, .04]}]
    assert segment["needs_review"] is True and segment["preview_is_draft"] is True
    assert build_caption_layout([segment], [source(source_region_verified=False)],
        caption_style={"blur_original": True})["source_masks"] == []


@pytest.mark.parametrize("style", [None, {}, {"background_color": None}, {"background_color": "#123456"}])
def test_one_selected_palette_survives_ocr_gaps_speaker_changes_and_bottom_toggle(style):
    first = speech()
    second = speech(id=2, start=3, end=6, speech_start=3.4, speech_end=5.8,
                   subtitle_cues=[{"start": 3.4, "end": 5.8, "text": "Người thứ hai trả lời."}])
    palette = lambda cue: cue.get("background_color") or ("#FFE500" if cue["background"] == "yellow" else "#FFFFFF")
    auto = build_caption_layout([first, second], [source()], (1920, 1080), style)["cues"]
    bottom = build_caption_layout([first, second], [], (1920, 1080), style)["cues"]
    expected = "#123456" if style and style.get("background_color") else "#FFE500"
    assert {palette(cue) for cue in auto + bottom} == {expected}
    assert [cue["placement"] for cue in auto] == ["below-source", "bottom"]
    assert [(c["start"], c["end"], c["text"]) for c in auto] == [(c["start"], c["end"], c["text"]) for c in bottom]


def test_independently_verified_region_remains_usable_when_ocr_translation_needs_review():
    plan = build_caption_layout([speech()], [source(needs_review=True, source_region_verified=True)],
                                caption_style={"blur_original": True})
    assert plan["source_masks"] == [{"start": 0, "end": 3.1, "bbox": [.3, .7, .4, .04]}]


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


@pytest.mark.parametrize("font", CAPTION_FONTS)
def test_selected_font_weight_and_outline_are_retained_in_precomputed_ass(tmp_path, font):
    style = {"font_family": font, "font_scale": 150, "bold": False, "outline": True,
             "background_opacity": 0, "text_color": "#FFFFFF"}
    plan = build_caption_layout([speech()], [source()], (1920, 1080), style)
    cue = plan["cues"][0]
    assert cue["font_family"] == font and cue["bold"] is False
    assert cue["outline_width"] > 0 and cue["outline_color"] == "#000000"
    # Exporter supplies the layout without a second caption_style argument.
    ass = SubtitleGenerator().generate_ass([speech()], tmp_path / "font.ass",
        video_size=(1920, 1080), caption_layout=plan).read_text(encoding="utf-8")
    assert f"\\fn{font}\\b0" in ass
    assert f"\\bord{cue['outline_width']:.3f}" in ass
    assert r"\3c&H000000&" in ass and r"\1a&H00&" in ass
    assert "Dialogue: 0," not in ass and r"\p1" not in ass
    assert "Về nhất, phá kỷ" in ass and "lục của trường." in ass


@pytest.mark.parametrize("opacity,alpha", [(100, "00"), (75, "40"), (50, "80"), (25, "BF"), (1, "FC")])
def test_partial_background_alpha_does_not_fade_text(tmp_path, opacity, alpha):
    plan = build_caption_layout([speech()], [], (1920, 1080), {"background_opacity": opacity})
    ass = SubtitleGenerator().generate_ass([speech()], tmp_path / "alpha.ass",
        video_size=(1920, 1080), caption_layout=plan).read_text(encoding="utf-8")
    background, text = (line for line in ass.splitlines() if line.startswith("Dialogue:"))
    assert f"\\1a&H{alpha}&" in background
    assert r"\1a&H00&" in text and r"\b-1" in text
    assert r"\bord0.000" in text


def test_dark_text_has_opposing_light_outline():
    dark = build_caption_layout([speech()], [], (1920, 1080), {"outline": True})["cues"][0]
    assert dark["outline_width"] > 0 and dark["outline_color"] == "#FFFFFF"


def test_each_cue_controls_ass_style_and_rejects_unsafe_precomputed_font(tmp_path):
    plan = build_caption_layout([speech()], [], (1920, 1080))
    cue = plan["cues"][0]
    cue.update(background_opacity=0, font_family="Tahoma", bold=False,
               outline_width=1.5, outline_color="#FFFFFF")
    ass = SubtitleGenerator().generate_ass([speech()], tmp_path / "cue.ass",
        video_size=(1920, 1080), caption_layout=plan).read_text(encoding="utf-8")
    assert "Dialogue: 0," not in ass and r"\fnTahoma\b0" in ass and r"\bord1.500" in ass
    cue["font_family"] = r"Arial\alpha&HFF&"
    with pytest.raises(ValueError, match="Font"):
        SubtitleGenerator().generate_ass([speech()], tmp_path / "unsafe.ass", caption_layout=plan)
    assert not (tmp_path / "unsafe.ass").exists()


@pytest.mark.parametrize("outline", [-1, float("nan"), float("inf"), True, "1", 200])
def test_corrupt_precomputed_outline_is_rejected_before_export(tmp_path, outline):
    plan = build_caption_layout([speech()], [], (1920, 1080), {"outline": True})
    plan["cues"][0]["outline_width"] = outline
    with pytest.raises(ValueError, match="viền"):
        SubtitleGenerator().generate_ass([speech()], tmp_path / "unsafe.ass", caption_layout=plan)
    assert not (tmp_path / "unsafe.ass").exists()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg/libass required")
@pytest.mark.parametrize("opacity", [0, 50, 100])
def test_real_libass_transparency_changes_only_background(tmp_path, opacity):
    style = {"background_color": "#FF0000", "text_color": "#00FF00", "position": "middle",
             "background_opacity": opacity, "font_family": "Tahoma", "font_scale": 150,
             "bold": False, "outline": False}
    path = SubtitleGenerator().generate_ass([speech()], tmp_path / "alpha-render.ass",
                                            video_size=(640, 360), caption_style=style)
    completed = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "lavfi", "-i", "color=c=blue:s=640x360:d=0.1", "-vf",
        f"setpts=PTS+1/TB,ass=filename={_escape_filter_filename(path)}", "-frames:v", "1",
        "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"], capture_output=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    pixels = list(zip(completed.stdout[::3], completed.stdout[1::3], completed.stdout[2::3]))
    assert len(pixels) == 640 * 360
    red = sum(r > 180 and g < 50 and b < 60 for r, g, b in pixels)
    purple = sum(95 < r < 160 and g < 50 and 95 < b < 170 for r, g, b in pixels)
    green = sum(g > 180 and r < 60 and b < 80 for r, g, b in pixels)
    # 15 px regular Tahoma is antialiased, and YUV chroma subsampling changes
    # edge pixels. Its opaque interiors must still exceed a faded 50% green.
    assert green > 5, "Text must remain opaque at all background opacity settings"
    if opacity == 100:
        assert red > 150 and purple < 30
    elif opacity == 50:
        assert red == 0 and purple > 150
    else:
        assert red == 0 and purple < 30


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg/libass required")
@pytest.mark.parametrize("font", CAPTION_FONTS)
@pytest.mark.parametrize("size", [(360, 640), (640, 360)])
def test_real_maximum_font_size_and_outline_stay_inside_caption_box(tmp_path, font, size):
    style = {"background_opacity": 0, "font_family": font, "font_scale": 200,
             "position": "bottom", "outline": True}
    plan = build_caption_layout([speech()], [], size, style)
    path = SubtitleGenerator().generate_ass([speech()], tmp_path / "bounds.ass",
        video_size=size, caption_layout=plan)
    completed = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "lavfi", "-i", f"color=c=blue:s={size[0]}x{size[1]}:d=0.1", "-vf",
        f"setpts=PTS+1/TB,ass=filename={_escape_filter_filename(path)}", "-frames:v", "1",
        "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"], capture_output=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    x, y, w, h = plan["cues"][0]["bbox"]
    glyphs = [(index % size[0], index // size[0]) for index, (r, g, b) in enumerate(zip(
        completed.stdout[::3], completed.stdout[1::3], completed.stdout[2::3]))
        if (r > 150 and g > 150 and b > 150) or max(r, g, b) < 35]
    assert len(glyphs) > 30, "Vietnamese text and its contrasting outline must actually render"
    assert all(x * size[0] - 2 <= px <= (x + w) * size[0] + 2
               and y * size[1] - 2 <= py <= (y + h) * size[1] + 2 for px, py in glyphs)


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
