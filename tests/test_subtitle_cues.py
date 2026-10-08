"""Subtitle readability and positioned hardsub rendering; no service or model calls."""
import json
import shutil
import subprocess
from unittest.mock import patch

import pytest

from config import settings
from core.subtitle import SubtitleGenerator
from core.subtitle_cues import build_subtitle_cues, fit_title_text, normalize_screen_texts, uncovered_intervals, speech_caption_cues
from core.streaming.export import HQExporter
from core.video_composer import _escape_filter_filename


def test_measured_speech_cues_keep_the_allocated_dub_tail_without_mutating_source():
    segment = {"start": 1, "end": 1.62, "dub_start": .9, "dub_end": 1.8,
               "speech_start": .94, "speech_end": 1.77,
               "subtitle_cues": [{"start": .9, "end": 1.8, "text": "Mười chín."}]}
    assert speech_caption_cues(segment) == [{"start": .94, "end": 1.77, "text": "Mười chín."}]
    assert (segment["start"], segment["end"]) == (1, 1.62)


def test_generated_legacy_pages_follow_allocated_dub_bounds():
    cues = speech_caption_cues({"start": 1, "end": 2, "dub_start": .8,
                               "dub_end": 2.2, "final_vi": "Mười chín."})
    assert cues == [{"start": .8, "end": 2.2, "text": "Mười chín."}]


@pytest.mark.parametrize("bounds", [
    {"dub_start": .8}, {"dub_end": 2.2},
    {"dub_start": .64, "dub_end": 2.2}, {"dub_start": .8, "dub_end": 2.36},
    {"dub_start": "1", "dub_end": 2}, {"dub_start": True, "dub_end": 2},
    {"dub_start": 1, "dub_end": float("inf")},
])
def test_invalid_dub_caption_bounds_fail_closed(bounds):
    assert speech_caption_cues({"start": 1, "end": 2, "final_vi": "Mười chín.", **bounds}) == []


def test_long_translation_paginates_without_losing_words_or_time():
    text = ("Đều quay bằng điện thoại thôi. Giả, hoàn toàn không có video bùng nổ trong một giây đâu. "
            "Chỉ cần bạn bình luận thì người khác có thể vào xem tài khoản của bạn.")
    cues = build_subtitle_cues(text, 2, 14)
    assert len(cues) >= 3
    assert " ".join(" ".join(cue["text"].split()) for cue in cues) == text
    assert all(len(cue["text"].replace("\n", " ")) <= 60 for cue in cues)
    assert all(len(cue["text"].splitlines()) <= 2 for cue in cues)
    assert cues[0]["start"] == 2 and cues[-1]["end"] == 14
    assert all(left["end"] == right["start"] for left, right in zip(cues, cues[1:]))
    assert all(cue["end"] > cue["start"] for cue in cues)


@pytest.mark.parametrize("text", [
    "Sai. Video kém chất lượng còn làm giảm mức độ ưu tiên tài khoản.",
    "Một phần mở đầu khá dài để trình bày đầy đủ nội dung và ý nghĩa. "
    "Sai. Video kém chất lượng còn làm giảm mức độ ưu tiên tài khoản.",
])
def test_pagination_rebalances_a_short_tail_instead_of_flashing_one_word(text):
    cues = build_subtitle_cues(text, 0, len(text) / 14)
    assert " ".join(" ".join(cue["text"].split()) for cue in cues) == text
    assert len(cues[-1]["text"].split()) >= 3
    assert cues[-1]["end"] - cues[-1]["start"] >= 1
    assert all(len(cue["text"].replace("\n", " ")) <= 60 for cue in cues)
    assert all(a["end"] == b["start"] for a, b in zip(cues, cues[1:]))


@pytest.mark.parametrize("start,end", [(3, 2), (0, float("nan")), (float("inf"), 2), ("bad", 2)])
def test_invalid_timing_never_generates_ass_events(start, end):
    assert build_subtitle_cues("Xin chào", start, end) == []


def test_manual_lines_literal_characters_and_long_word_are_preserved():
    text = "Xin chào\r\nMọi người\rNhé"
    cues = build_subtitle_cues(text, 0, 3)
    assert [cue["text"] for cue in cues] == ["Xin chào\nMọi người", "Nhé"]
    assert cues[0]["start"] == 0 and cues[-1]["end"] == 3
    assert cues[0]["end"] == cues[1]["start"]
    literal = r"{\alpha&HFF&}A\NB"
    assert build_subtitle_cues(literal, 0, 1)[0]["text"] == literal
    word = "a" * 80
    assert build_subtitle_cues(word, 0, 1)[0]["text"] == word


def test_manual_lines_use_same_paired_pages_and_proportional_timing_as_preview(tmp_path):
    text = "Dòng thứ nhất\nDòng thứ hai\nDòng thứ ba\nDòng thứ tư"
    cues = build_subtitle_cues(text, 0, 8)
    assert [cue["text"] for cue in cues] == ["Dòng thứ nhất\nDòng thứ hai", "Dòng thứ ba\nDòng thứ tư"]
    assert cues[0]["start"] == 0 and cues[-1]["end"] == 8
    boundary = 8 * len(cues[0]["text"]) / sum(len(cue["text"]) for cue in cues)
    assert cues[0]["end"] == cues[1]["start"] == boundary
    assert "\n".join(cue["text"] for cue in cues) == text
    segment = [{"start": 0, "end": 8, "vi_text": text}]
    generator = SubtitleGenerator()
    ass = generator.generate_manual_ass(segment, tmp_path / "manual.ass").read_text(encoding="utf-8")
    srt = generator.generate_srt(segment, tmp_path / "manual.srt").read_text(encoding="utf-8")
    rows = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    assert len(rows) == srt.count("-->") == 2
    assert all(row.count(r"\N") == 1 for row in rows)
    assert [row.split(",", 9)[-1].replace(r"\N", "\n") for row in rows] == [cue["text"] for cue in cues]


def test_export_speech_style_matches_yellow_black_preview_without_a_background_box(tmp_path):
    ass = SubtitleGenerator().generate_manual_ass([], tmp_path / "style.ass", video_size=(1080, 1920)).read_text(encoding="utf-8")
    styles = ass.split("[V4+ Styles]\n", 1)[1].split("[Events]", 1)[0].splitlines()
    names = next(line for line in styles if line.startswith("Format:")).removeprefix("Format: ").split(", ")
    values = next(line for line in styles if line.startswith("Style: TikTokStyle,")).removeprefix("Style: ").split(",")
    style = dict(zip(names, values))
    assert style["PrimaryColour"] == "&H0000EBFF"
    assert style["Bold"] == "-1" and style["Alignment"] == "2"
    assert style["BorderStyle"] == "1", "An outline must not become an opaque rectangle behind the speech"
    font = float(style["Fontsize"])
    assert float(style["Outline"]) == pytest.approx(round(font * .10, 1))
    assert float(style["Shadow"]) == pytest.approx(round(font * .055, 1))
    assert int(style["MarginV"]) == round(1920 * .12)
    assert .81 < 1 - .12 - 2 * 1.25 * font / 1920 < .83


def test_srt_and_ass_use_the_same_display_pages(tmp_path):
    segments = [{"start": 0, "end": 8, "vi_text": "Đừng tin những mẹo câu tương tác vô căn cứ. "
                 "Nội dung chất lượng mới giúp người xem muốn theo dõi bạn lâu dài."}]
    generator = SubtitleGenerator()
    srt = generator.generate_srt(segments, tmp_path / "captions.srt").read_text(encoding="utf-8")
    ass = generator.generate_manual_ass(segments, tmp_path / "captions.ass").read_text(encoding="utf-8")
    cues = build_subtitle_cues(segments[0]["vi_text"], 0, 8)
    assert srt.count("-->") == len(cues)
    events = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    assert len(events) == len(cues)
    assert [line.split(",", 9)[-1].replace(r"\N", "\n") for line in events] == [cue["text"] for cue in cues]


def screen_row(**overrides):
    return {"start": 1, "end": 2, "text_zh": "原文", "text_vi": "Chữ trên màn hình",
            "bbox": [.1, .15, .8, .1], "kind": "subtitle", **overrides}


def test_visual_metadata_is_bounded_and_uncertain_text_does_not_cover_video():
    rows = normalize_screen_texts([
        screen_row(start=-1, end=7, bbox=[-.1, .8, .4, .5]),
        screen_row(needs_review=True), screen_row(bbox=[0, 0, float("nan"), 1]),
        screen_row(bbox=[1.2, 0, .2, .1]), screen_row(text_vi=""),
    ], duration=4)
    assert len(rows) == 1
    assert rows[0]["start"] == 0 and rows[0]["end"] == 4
    assert rows[0]["bbox"] == pytest.approx([0, .8, .3, .2])


def test_title_does_not_hide_speech_but_detected_hardsub_prevents_duplicates(tmp_path):
    rows = normalize_screen_texts([screen_row(), screen_row(start=0, end=3, kind="title")])
    assert uncovered_intervals(0, 3, rows) == [(0, 1), (2, 3)]
    ass = SubtitleGenerator().generate_manual_ass(
        [{"start": 0, "end": 3, "vi_text": "Lời thoại"}], tmp_path / "screen.ass",
        screen_texts=rows, video_size=(360, 640),
    ).read_text(encoding="utf-8")
    speech = [line for line in ass.splitlines() if line.startswith("Dialogue: 2")]
    assert len(speech) == 2
    assert "0:00:00.00,0:00:01.00" in speech[0]
    assert "0:00:02.00,0:00:03.00" in speech[1]
    assert "PlayResX: 360" in ass and "PlayResY: 640" in ass
    assert ass.count("Dialogue: 0") == 2
    assert ass.count("Dialogue: 1") == 2


def test_long_title_stays_complete_in_one_timed_event_and_fits_original_box(tmp_path):
    text = "Những lời đồn về lượt xem trên Douyin mà có thể bạn chưa biết."
    title = fit_title_text(text, 288, 64, 16)
    assert title.replace("\n", " ") == text
    assert len(title.splitlines()) <= 2
    assert title == "Những lời đồn về lượt xem trên\nDouyin mà có thể bạn chưa biết."
    ass = SubtitleGenerator().generate_manual_ass(
        [], tmp_path / "persistent-title.ass", video_size=(360, 640),
        screen_texts=[screen_row(start=0, end=32, text_vi=text, bbox=[.1, .1, .8, .1], kind="title")],
    ).read_text(encoding="utf-8")
    rows = [line for line in ass.splitlines() if line.startswith("Dialogue: 1")]
    assert len(rows) == 1
    assert ",0:00:00.00,0:00:32.00," in rows[0]
    assert rows[0].endswith(title.replace("\n", r"\N"))
    assert r"\pos(180.00,96.00)" in rows[0]
    assert "m 0 0 l 288.00 0 l 288.00 64.00 l 0 64.00" in ass


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg/libass is required")
def test_libass_title_is_identical_at_start_middle_and_end(tmp_path):
    text = "Những lời đồn về lượt xem trên Douyin mà có thể bạn chưa biết."
    path = SubtitleGenerator().generate_manual_ass(
        [], tmp_path / "persistent-render.ass", video_size=(360, 640),
        screen_texts=[screen_row(start=0, end=32, text_vi=text, bbox=[.1, .1, .8, .1], kind="title")],
    )
    frames = []
    for timestamp in (1, 16, 31):
        result = subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-f", "lavfi", "-i", "color=c=white:s=360x640:d=0.1",
            "-vf", f"setpts=PTS+{timestamp}/TB,ass=filename={_escape_filter_filename(path)}",
            "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
        ], capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if b"No such filter" in result.stderr:
            pytest.skip("FFmpeg lacks libass")
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        frames.append(result.stdout)
    assert frames[0] == frames[1] == frames[2], "A persistent title must not cycle incomplete phrases"
    frame = frames[0]
    assert len(frame) == 360 * 640 * 3
    in_box_white = sum(min(frame[(y * 360 + x) * 3:(y * 360 + x) * 3 + 3]) > 220
                       for y in range(67, 125) for x in range(39, 321))
    assert in_box_white > 100, "The complete title must produce visible glyphs"
    assert min(frame[(300 * 360 + 180) * 3:(300 * 360 + 180) * 3 + 3]) > 240


@pytest.mark.parametrize("mask", [False, True])
def test_edited_speech_is_shown_once_across_multiple_mask_only_ocr_rows(tmp_path, mask):
    text = "Một câu đã sửa giữ nguyên đầy đủ ý nghĩa qua nhiều vùng phụ đề cũ trên màn hình."
    rows = normalize_screen_texts([
        screen_row(start=0, end=2, text_vi="", mask_only=True),
        screen_row(start=2, end=4, text_vi="stale copy of full sentence", mask_only=True),
        screen_row(start=0, end=4, kind="title", text_vi="Tiêu đề"),
    ])
    assert uncovered_intervals(0, 4, rows) == [(0, 4)]
    assert rows[1]["text_vi"] == ""
    ass = SubtitleGenerator().generate_manual_ass(
        [{"start": 0, "end": 4, "vi_text": text}], tmp_path / "edited.ass",
        screen_texts=rows, mask_screen_text=mask,
    ).read_text(encoding="utf-8")
    speech = [line.split(",", 9)[-1].replace(r"\N", " ") for line in ass.splitlines() if line.startswith("Dialogue: 2")]
    assert " ".join(speech) == text
    assert ass.count("Dialogue: 0") == (3 if mask else 0)
    assert ass.count("Dialogue: 1") == 1, "Only the separate title remains in the original OCR locations"
    assert "stale copy" not in ass


def test_mask_only_never_approves_uncertain_or_oversized_geometry():
    assert normalize_screen_texts([
        screen_row(text_vi="", mask_only=True, needs_review=True),
        screen_row(text_vi="", mask_only=True, bbox=[0, 0, 1, .8]),
        screen_row(text_vi="", mask_only=True, bbox=[-.1, .1, .8, .1]),
        screen_row(text_vi="", mask_only="true"),
    ]) == []


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg/libass is required")
def test_libass_masks_only_detected_box_and_renders_translation_inside_it(tmp_path):
    ass = SubtitleGenerator().generate_manual_ass(
        [{"start": 0, "end": 1, "vi_text": "Không được hiện thêm phụ đề trùng"}],
        tmp_path / "box.ass", screen_texts=[screen_row(start=0, end=1)], video_size=(360, 640),
    )
    result = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "lavfi", "-i", "color=c=white:s=360x640:d=0.1",
        "-vf", f"ass=filename={_escape_filter_filename(ass)}", "-frames:v", "1",
        "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
    ], capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if b"No such filter" in result.stderr:
        pytest.skip("FFmpeg lacks libass")
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    frame = result.stdout
    assert len(frame) == 360 * 640 * 3

    def pixel(x, y):
        index = (y * 360 + x) * 3
        return frame[index:index + 3]

    assert max(pixel(39, 99)) < 40, "Detected source text must be covered"
    assert min(pixel(180, 300)) > 240, "The person's central image must remain uncovered"
    assert min(pixel(180, 560)) > 240, "No stale fixed bottom strip is rendered"
    lit = sum(min(pixel(x, y)) > 220 for y in range(100, 156) for x in range(40, 320))
    assert lit > 20, "Translated visible text must actually render inside its detected box"
    lower = frame[360 * 300 * 3:]
    assert not any(r > 100 and g > 100 and b < 80 for r, g, b in zip(lower[::3], lower[1::3], lower[2::3])), "No duplicate speech subtitle"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg/libass is required")
def test_libass_spoken_subtitle_renders_above_overlapping_mask_only_region(tmp_path):
    """A valid lower-screen mask must not erase the edited spoken caption."""
    def render(mask):
        ass = SubtitleGenerator().generate_manual_ass(
            [{"start": 0, "end": 1, "vi_text": "Lời thoại đã sửa"}],
            tmp_path / f"overlap-{mask}.ass", video_size=(360, 640),
            screen_texts=[screen_row(start=0, end=1, text_vi="", mask_only=True,
                                     bbox=[0, .78, 1, .2])], mask_screen_text=mask,
        )
        result = subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-f", "lavfi", "-i", "color=c=white:s=360x640:d=0.1",
            "-vf", f"ass=filename={_escape_filter_filename(ass)}", "-frames:v", "1",
            "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
        ], capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if b"No such filter" in result.stderr:
            pytest.skip("FFmpeg lacks libass")
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        frame = result.stdout
        yellow = sum(r > 100 and g > 100 and b < 80 for r, g, b in zip(frame[::3], frame[1::3], frame[2::3]))
        return frame, yellow

    plain, plain_yellow = render(False)
    masked, masked_yellow = render(True)
    assert plain_yellow > 100
    assert masked_yellow >= plain_yellow * .9, "Source masking must not paint over translated speech"
    pixel = (515 * 360 + 10) * 3
    assert min(plain[pixel:pixel + 3]) > 240
    assert max(masked[pixel:pixel + 3]) < 40, "The source region must still be masked"


def test_export_uses_detected_boxes_instead_of_the_old_fixed_mask(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(settings, "SEPARATION_ENGINE", "none")
    exporter = HQExporter()
    observed = {}

    def compose(**kwargs):
        observed.update(kwargs)
        observed["ass"] = kwargs["subtitle_path"].read_text(encoding="utf-8")
        kwargs["output_path"].write_bytes(b"test output")

    metadata = json.dumps({"format": {"duration": "3"}, "streams": [
        {"codec_type": "video", "width": 1920, "height": 1080}, {"codec_type": "audio"},
    ]}).encode()
    with patch("core.streaming.export.run_media", return_value=metadata), \
            patch.object(exporter, "_assemble_voice_timeline"), \
            patch.object(exporter.mixer, "mix"), \
            patch.object(exporter.composer, "compose", side_effect=compose):
        exporter.export("boxes", tmp_path / "source.mp4", [{"start":0,"end":3,"final_vi":"Lời thoại"}], 3, screen_texts=[screen_row()])
    assert observed["mask_chinese_sub"] is False
    assert "PlayResX: 1920" in observed["ass"] and "PlayResY: 1080" in observed["ass"]
    assert "Dialogue: 0" in observed["ass"] and "Dialogue: 1" in observed["ass"]
    assert not list(tmp_path.glob("hq_export_*"))


def test_rotated_video_uses_display_dimensions():
    metadata = json.dumps({"streams": [{"width": 1920, "height": 1080,
                                       "side_data_list": [{"rotation": -90}]}]}).encode()
    with patch("core.streaming.export.run_media", return_value=metadata):
        assert HQExporter._video_size("rotated.mp4") == (1080, 1920)
