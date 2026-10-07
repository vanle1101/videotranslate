"""Automatic caption layout uses measured utterance timing and preserves source."""
import json
import shutil
import subprocess
from unittest.mock import patch

import pytest

from config import settings
from core.subtitle import SubtitleGenerator
from core.subtitle_cues import build_caption_layout
from core.streaming.export import HQExporter
from core.video_composer import VideoComposer, _escape_filter_filename


def layout(*args, **kwargs):
    return build_caption_layout(*args, **kwargs)["cues"]


def speech(identifier=0, start=0, end=3, **changes):
    return {"id": identifier, "start": start, "end": end, "final_vi": "Câu chưa được phép xuất hiện sớm",
            "speech_start": start + .4, "speech_end": end - .2,
            "subtitle_cues": [{"start": start + .4, "end": end - .2,
                               "text": "Anh đã đến rồi.", "words": [{"text": "Anh", "start": start + .4, "end": start + .7}]}],
            **changes}


def region(**changes):
    return {"id": "o0", "start": 0, "end": 3, "source_method": "local-ocr", "confidence": .98,
            "kind": "subtitle", "text_zh": "你来了", "text_vi": "Bản OCR lỗi thời", "bbox": [.3, .70, .4, .04],
            "needs_review": False, **changes}


def test_no_pre_onset_or_future_speaker_full_current_utterance_only():
    source = [speech(), speech(identifier=1, start=3, end=6,
              subtitle_cues=[{"start": 3.5, "end": 5.7, "text": "Đến lượt người thứ hai."}])]
    plan = layout(source, [], (1080, 1920))
    assert [row["text"] for row in plan] == ["Anh đã đến rồi.", "Đến lượt người thứ hai."]
    assert [(row["start"], row["end"]) for row in plan] == [(.4, 2.8), (3.5, 5.7)]
    assert all(row["background"] == "white" and row["placement"] == "bottom" for row in plan)
    assert plan[0]["font_size"] == 38 and plan[0]["bbox"][2] < .5
    assert plan[0]["bbox"][1] + plan[0]["bbox"][3] == pytest.approx(.90)
    assert not any(row["start"] <= .2 < row["end"] for row in plan)
    assert [row["text"] for row in plan if row["start"] <= 1 < row["end"]] == ["Anh đã đến rồi."]


@pytest.mark.parametrize("value", [[], None, "bad"])
def test_explicit_empty_or_unavailable_cues_never_invent_timing(value):
    assert layout([speech(subtitle_cues=value)], [region()]) == []


def test_malformed_segments_are_ignored_before_reflow():
    assert layout([None, [], "invalid"], [region()]) == []


@pytest.mark.parametrize("changes", [{"needs_review": True}, {"confirmed_silence": True}])
def test_unreviewed_or_silent_segment_has_no_visible_caption(changes):
    assert layout([speech(**changes)], [region()]) == []


def test_synthesized_draft_preview_keeps_measured_caption_and_review_flag():
    draft = speech(needs_review=True, preview_is_draft=True)
    cues = layout([draft], [region()])
    assert [(cue["start"], cue["end"], cue["text"]) for cue in cues] == [(.4, 2.8, "Anh đã đến rồi.")]
    assert draft["needs_review"] is True
    assert layout([speech(needs_review=True, preview_is_draft=True, confirmed_silence=True)]) == []


@pytest.mark.parametrize("mask_only", [False, True])
def test_yellow_caption_below_measured_chinese_without_mask_or_stale_ocr(mask_only):
    plan = layout([speech()], [region(mask_only=mask_only, text_vi="")], (1080, 1920))
    assert len(plan) == 1
    cue = plan[0]
    assert cue["text"] == "Anh đã đến rồi." and cue["background"] == "yellow"
    assert cue["placement"] == "below-source"
    assert cue["bbox"][1] > .74
    assert cue["source_bbox"] == [.3, .7, .4, .04]
    assert cue["video_size"] == [1080, 1920]
    assert "mask" not in cue


@pytest.mark.parametrize("changes", [{"kind": "title"}, {"kind": "ignore"}, {"needs_review": True},
                                      {"confidence": .8}, {"source_method": "video-ai"},
                                      {"bbox": [0, .7, 1, .2]}, {"bbox": [-.1, .7, .4, .04]}])
def test_untrusted_title_or_large_region_does_not_anchor_or_mask(changes):
    cue = layout([speech()], [region(**changes)], (1080, 1920))[0]
    assert cue["background"] == "white" and cue["source_bbox"] is None


def test_low_chinese_region_places_caption_above_without_covering_source():
    cue = layout([speech()], [region(bbox=[.3, .94, .4, .05])], (1080, 1920))[0]
    assert cue["placement"] == "above-source"
    assert cue["bbox"][1] + cue["bbox"][3] < .94


def test_caption_keeps_full_utterance_across_measured_region_boundaries():
    plan = layout([speech()], [region(start=1, end=2)], (1080, 1920))
    assert [(row["start"], row["end"], row["background"]) for row in plan] == [(.4, 2.8, "yellow")]
    assert all(row["text"] == "Anh đã đến rồi." for row in plan)


def test_stable_caption_does_not_cover_a_later_lower_source_line():
    plan = layout([speech()], [region(end=2), region(id="o1", start=2, end=2.8, bbox=[.3, .76, .4, .04])], (1080, 1920))
    assert len(plan) == 1
    assert plan[0]["bbox"][1] > .80
    assert (plan[0]["start"], plan[0]["end"]) == (.4, 2.8)


def test_multiple_pages_keep_anchor_color_font_and_box_through_ocr_gap():
    segment = speech(end=6, speech_start=0, speech_end=6, subtitle_cues=[
        {"start": 0, "end": 3, "text": "Câu mở đầu."},
        {"start": 3, "end": 6, "text": "Đây là phần tiếp theo của cùng câu thoại dài."},
    ])
    plan = layout([segment], [region(end=2.9)], (1080, 1920))
    assert len(plan) == 2
    assert [cue["background"] for cue in plan] == ["yellow", "yellow"]
    for key in ("bbox", "font_size", "placement", "source_bbox"):
        assert plan[0][key] == plan[1][key]
    assert [(cue["start"], cue["end"]) for cue in plan] == [(0, 3), (3, 6)]
    assert [cue["text"] for cue in plan if cue["start"] <= 1 < cue["end"]] == ["Câu mở đầu."]
    assert "tiếp theo" not in plan[0]["text"]


@pytest.mark.parametrize("source_regions", [[], [region(end=3), region(start=3, end=6, bbox=[.3, .77, .4, .04])]])
def test_all_pages_share_size_for_long_manual_line_and_clear_later_source(source_regions):
    segment = speech(end=6, speech_start=0, speech_end=6, subtitle_cues=[
        {"start": 0, "end": 3, "text": "Ngắn."},
        {"start": 3, "end": 6, "text": "W" * 80},
    ])
    first, second = layout([segment], source_regions, (1080, 1920))
    assert first["bbox"] == second["bbox"]
    assert first["font_size"] == second["font_size"] < 38
    if source_regions:
        assert first["bbox"][1] > .81
    else:
        assert first["bbox"][1] + first["bbox"][3] == pytest.approx(.9)


def test_utterance_anchor_does_not_leak_to_next_speaker():
    first = speech()
    second = speech(identifier=1, start=3, end=6,
                    subtitle_cues=[{"start": 3.4, "end": 5.8, "text": "Người thứ hai trả lời."}])
    plan = layout([first, second], [region(end=3)], (1080, 1920))
    assert [cue["background"] for cue in plan] == ["yellow", "white"]
    assert not any(cue["segment_id"] == 1 and cue["start"] < 3.4 for cue in plan)


def test_source_region_at_edge_does_not_crash_after_caption_margin_clamping():
    cue = layout([speech()], [region(bbox=[0, .7, .01, .04])], (1080, 1920))[0]
    assert cue["placement"] == "below-source"
    assert cue["bbox"][0] >= .04


def test_measured_sample_reflows_horizontally_without_changing_speech_timing(tmp_path):
    segment = speech(start=2.44, end=4.5, speech_start=2.48, speech_end=4.35,
        final_vi="Về nhất, phá kỷ lục của trường.", subtitle_timing_source="edge-word-boundary",
        subtitle_cues=[{"start": 2.48, "end": 4.35, "text": "Về nhất, phá kỷ\nlục của trường."}])
    source = region(start=2.4, end=4.5, bbox=[.3573, .8167, .2844, .0796])
    cue = layout([segment], [source], (1920, 1080))[0]
    assert cue["text"] == segment["final_vi"]
    assert (cue["start"], cue["end"]) == (2.48, 4.35)
    assert cue["placement"] == "below-source" and cue["background"] == "yellow"
    assert cue["bbox"][2] > .30 and cue["bbox"][3] < .05
    assert segment["subtitle_cues"][0]["text"].count("\n") == 1
    ass = SubtitleGenerator().generate_ass([segment], tmp_path / "wide.ass",
        screen_texts=[source], video_size=(1920, 1080)).read_text(encoding="utf-8")
    text_row = next(line for line in ass.splitlines() if line.startswith("Dialogue: 1,"))
    assert text_row.endswith(segment["final_vi"]) and r"\N" not in text_row


def test_automatic_reflow_uses_portrait_width_and_preserves_all_words():
    text = "Trải nghiệm 400 mét hội thao qua góc nhìn thứ nhất."
    segment = speech(final_vi=text, subtitle_timing_source="audio-onset-estimate",
                     subtitle_cues=[{"start": .4, "end": 2.8, "text": text}])
    wide, portrait = (layout([segment], [], size)[0] for size in ((1920, 1080), (1080, 1920)))
    assert wide["text"] == text
    assert portrait["text"].count("\n") == 1
    assert " ".join(portrait["text"].split()) == text
    assert portrait["bbox"][0] >= .04 and sum(portrait["bbox"][::2]) <= .96
    assert (wide["start"], wide["end"]) == (portrait["start"], portrait["end"])


@pytest.mark.parametrize("timing_source", [None, "edge-word-boundary", "audio-pause-estimate"])
def test_user_line_breaks_survive_wider_video(timing_source):
    text = "Về nhất, phá kỷ\nlục của trường."
    segment = speech(final_vi=text, subtitle_timing_source=timing_source,
                     subtitle_cues=[{"start": .4, "end": 2.8, "text": text}])
    assert layout([segment], [], (1920, 1080))[0]["text"] == text


def test_placement_can_use_verified_source_region_without_approving_translation():
    uncertain = region(needs_review=True, source_region_verified=True, text_vi="Bản dịch chưa rõ")
    plan = layout([speech()], [uncertain], (1080, 1920))
    assert plan[0]["background"] == "yellow"
    assert uncertain["needs_review"] is True
    assert plan[0]["text"] == "Anh đã đến rồi."


def test_ass_and_srt_use_explicit_cues_without_word_reveal_or_stale_ocr(tmp_path):
    segments = [speech()]
    plan = layout(segments, [region()], (1080, 1920))
    generator = SubtitleGenerator()
    content = generator.generate_ass(segments, tmp_path / "auto.ass", screen_texts=[region()], video_size=(1080, 1920)).read_text(encoding="utf-8")
    assert "Bản OCR lỗi thời" not in content and "Câu chưa được phép" not in content
    assert content.count("Dialogue:") == 2
    assert "0:00:00.40,0:00:02.80" in content
    x, y, w, h = plan[0]["bbox"]
    assert f"\\pos({(x+w/2)*1080:.3f},{(y+h/2)*1920:.3f})" in content
    assert "&H00E5FF&" in content and "&H000000&" in content
    srt = generator.generate_srt(segments, tmp_path / "auto.srt").read_text(encoding="utf-8")
    assert "00:00:00,400 --> 00:00:02,800" in srt and "Anh đã đến rồi." in srt


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg/libass required")
def test_render_keeps_chinese_region_pixels_and_draws_compact_yellow_below(tmp_path):
    video_size = (360, 640)
    path = SubtitleGenerator().generate_ass([speech()], tmp_path / "render.ass", screen_texts=[region()], video_size=video_size)
    def frame(filters):
        completed = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-f", "lavfi", "-i", "color=c=blue:s=360x640:d=0.1",
            "-vf", filters, "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
            capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        assert completed.returncode == 0, completed.stderr.decode(errors="replace")
        return completed.stdout
    base = frame("setpts=PTS+1/TB")
    rendered = frame(f"setpts=PTS+1/TB,ass=filename={_escape_filter_filename(path)}")
    for y in range(448, 473):
        assert rendered[(y * 360 + 108)*3:(y * 360 + 252)*3] == base[(y * 360 + 108)*3:(y * 360 + 252)*3]
    assert sum(r > 150 and g > 150 and b < 70 for r, g, b in zip(rendered[::3], rendered[1::3], rendered[2::3])) > 100
    assert rendered[:440 * 360 * 3] == base[:440 * 360 * 3]


def test_export_and_legacy_composer_never_invent_a_blur_strip(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(settings, "SEPARATION_ENGINE", "none")
    exporter = HQExporter()
    observed = {}
    def compose(**kwargs):
        observed.update(kwargs)
        kwargs["output_path"].write_bytes(b"fixture")
    metadata = json.dumps({"format": {"duration": "3"}, "streams": [
        {"codec_type": "video", "width": 360, "height": 640}, {"codec_type": "audio"},
    ]}).encode()
    with patch("core.streaming.export.run_media", return_value=metadata), \
            patch.object(exporter, "_assemble_voice_timeline"), patch.object(exporter.mixer, "mix"), \
            patch.object(exporter.composer, "compose", side_effect=compose):
        exporter.export("auto-layout", tmp_path / "source.mp4", [speech()], 3, mask_chinese=True, screen_texts=None)
    assert observed["mask_chinese_sub"] is False
    with patch("core.video_composer.run_media") as run:
        VideoComposer().compose(tmp_path / "source.mp4", tmp_path / "voice.wav", tmp_path / "sub.ass", tmp_path / "out.mp4", True)
    args = run.call_args.args[0]
    filters = args[args.index("-filter_complex") + 1]
    assert "gblur" not in filters and "crop=" not in filters and "subtitles=" in filters


@pytest.mark.parametrize("position", ["auto", "top", "middle", "bottom"])
def test_bottom_preview_export_retains_source_blur_without_moving_captions(tmp_path, monkeypatch, position):
    monkeypatch.setattr(settings, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(settings, "SEPARATION_ENGINE", "none")
    exporter = HQExporter()
    observed = {}
    def compose(**kwargs):
        observed.update(kwargs)
        kwargs["output_path"].write_bytes(b"fixture")
    style = {"position": position, "blur_original": True}
    size = (360, 640)
    metadata = json.dumps({"format": {"duration": "3"}, "streams": [
        {"codec_type": "video", "width": size[0], "height": size[1]}, {"codec_type": "audio"},
    ]}).encode()
    with patch("core.streaming.export.run_media", return_value=metadata), \
            patch.object(exporter, "_assemble_voice_timeline"), patch.object(exporter.mixer, "mix"), \
            patch.object(exporter.sub_gen, "generate_ass") as ass, \
            patch.object(exporter.composer, "compose", side_effect=compose):
        exporter.export("source-mask", tmp_path / "source.mp4", [speech()], 3,
                        screen_texts=[], source_screen_texts=[region()], caption_style=style)
    plan = ass.call_args.kwargs["caption_layout"]
    assert plan["cues"] == build_caption_layout([speech()], [], size, style)["cues"]
    assert plan["source_masks"] == build_caption_layout([speech()], [region()], size, style)["source_masks"]
    assert observed["source_masks"] == plan["source_masks"]
    assert observed["source_masks"]
