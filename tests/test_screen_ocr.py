"""Local OCR timing/box contracts and one real, network-free inference."""
import math
from pathlib import Path
from unittest.mock import Mock

import pytest

from config import settings
from core.screen_ocr import ScreenOCR, ScreenOCRError


def detection(text="你好", bbox=None):
    return {"text_zh": text, "bbox": bbox or [.1, .6, .5, .06], "confidence": .98}


def sample(time, *detections):
    return {"time": time, "detections": list(detections)}


def test_consecutive_text_tracks_keep_measured_intervals_and_small_box_union():
    rows = ScreenOCR.track_samples([
        sample(10, detection()), sample(10 + 1 / 3, detection(bbox=[.102, .601, .5, .06])),
        sample(10 + 2 / 3, detection()),
    ], 10, 11)
    assert len(rows) == 1
    assert rows[0]["id"] == "o0"
    assert rows[0]["start"] == 10 and rows[0]["end"] == 11
    assert rows[0]["bbox"] == pytest.approx([.1, .6, .502, .061])


def test_reappearing_text_never_bridges_missing_frame_or_different_position():
    rows = ScreenOCR.track_samples([
        sample(0, detection()), sample(1 / 3), sample(2 / 3, detection()),
        sample(1, detection(bbox=[.1, .1, .5, .06])),
    ], 0, 1.2)
    assert len(rows) == 3
    assert [(r["start"], r["end"]) for r in rows] == [(0, .333333), (.666667, 1), (1, 1.2)]


def test_two_identical_words_at_different_positions_remain_separate():
    left, right = detection(bbox=[.1, .6, .2, .06]), detection(bbox=[.7, .6, .2, .06])
    rows = ScreenOCR.track_samples([sample(0, left, right), sample(1 / 3, right, left)], 0, .5)
    assert len(rows) == 2
    assert all(r["end"] == .5 for r in rows)
    assert rows[0]["bbox"][0] == .1 and rows[1]["bbox"][0] == .7


def test_detector_filters_latin_low_confidence_invalid_boxes_and_pads_two_pixels():
    box = [[10, 20], [50, 20], [50, 40], [10, 40]]
    rows = ScreenOCR.detections([
        [box, "你 好", .98], [box, "T-SHIRT", .99], [box, "低分", .74],
        [[[0, 0], [math.nan, 10]], "坏框", .98], [box, "坏分", math.nan],
    ], 100, 200)
    assert len(rows) == 1 and rows[0]["text_zh"] == "你好"
    assert rows[0]["bbox"] == pytest.approx([.08, .09, .44, .12])


def test_review_ocr_keeps_clear_evidence_around_a_blurred_transition():
    from core.translation_review import _ReviewScreenOCR, AutomaticTranslationReviewer

    # Real failure: .999/.889/.998 samples of the same subtitle were merged
    # with min=.889, discarding both clear observations at the .90 review
    # gate. Filter before merging; never extend evidence through the blur.
    box = [[40, 160], [160, 160], [160, 180], [40, 180]]
    samples = [sample(9.26 + index / _ReviewScreenOCR.FPS,
                      *_ReviewScreenOCR.detections([[box, "但够了", confidence]], 200, 200))
               for index, confidence in enumerate((.999, .889, .998))]
    rows = _ReviewScreenOCR.track_samples(samples, 9.26, 9.86)
    assert [(row["start"], row["end"]) for row in rows] == [(9.26, 9.46), (9.66, 9.86)]
    assert all(row["confidence"] >= .90 for row in rows)
    source = {"start": 9.0, "end": 9.84, "text_zh": "但够了"}
    assert all(AutomaticTranslationReviewer._speech_evidence(
        row, source, [], changed_source=False) for row in rows)
    blur_only_source = {"start": 9.47, "end": 9.65, "text_zh": "但够了"}
    assert not any(AutomaticTranslationReviewer._speech_evidence(
        row, blur_only_source, [], changed_source=False) for row in rows)


@pytest.mark.parametrize("start,end", [(0, 46), (1, 0), (-1, 3), (0, math.nan), (True, "bad")])
def test_invalid_or_overlong_ranges_fail_before_decoding(start, end):
    with pytest.raises(ScreenOCRError):
        ScreenOCR(engine=Mock()).extract(Path("unused"), start, end)


def test_extract_checks_cancellation_each_frame_and_cleans_temp_files(tmp_path, monkeypatch):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    monkeypatch.setattr(settings, "TEMP_DIR", tmp_path)
    commands = []
    def fake_decode(cmd, cancel_check):
        commands.append(cmd)
        for i in range(3):
            path = Path(cmd[-1].replace("%04d", f"{i + 1:04d}"))
            cv2.imencode(".jpg", np.zeros((100, 200, 3), dtype=np.uint8))[1].tofile(path)
    monkeypatch.setattr("core.screen_ocr.run_media", fake_decode)
    cancelled = False
    def infer(frame):
        nonlocal cancelled
        cancelled = True
        return [], []
    engine = Mock(side_effect=infer)
    with pytest.raises(ScreenOCRError, match="hủy"):
        ScreenOCR(engine=engine).extract(Path("unused"), 0, 1, lambda: cancelled)
    assert engine.call_count == 1 and not list(tmp_path.iterdir())
    assert commands[0][commands[0].index("-frames:v") + 1] == "3"


def test_extract_keeps_unicode_paths_and_exact_sample_windows(tmp_path, monkeypatch):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    directory = tmp_path / "Dịch chữ"
    directory.mkdir()
    monkeypatch.setattr(settings, "TEMP_DIR", directory)
    def fake_decode(cmd, cancel_check):
        for i in range(3):
            path = Path(cmd[-1].replace("%04d", f"{i + 1:04d}"))
            cv2.imencode(".jpg", np.zeros((100, 200, 3), dtype=np.uint8))[1].tofile(path)
    monkeypatch.setattr("core.screen_ocr.run_media", fake_decode)
    engine = Mock(return_value=([[[[20, 60], [100, 60], [100, 70], [20, 70]], "你好", .98]], []))
    rows = ScreenOCR(engine=engine).extract(Path("unused"), 2, 2.8)
    assert engine.call_count == 3 and rows[0]["start"] == 2 and rows[0]["end"] == 2.8
    assert not list(directory.iterdir())


def test_real_chinese_image_inference_offline(tmp_path):
    pytest.importorskip("rapidocr_onnxruntime")
    np = pytest.importorskip("numpy")
    Image = pytest.importorskip("PIL.Image")
    ImageDraw = pytest.importorskip("PIL.ImageDraw")
    ImageFont = pytest.importorskip("PIL.ImageFont")
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    if not font_path.exists():
        pytest.skip("Chinese system font unavailable")
    image = Image.new("RGB", (960, 260), "black")
    draw = ImageDraw.Draw(image)
    draw.text((80, 70), "你好中国", font=ImageFont.truetype(str(font_path), 64), fill="white")
    processor = ScreenOCR()
    result, _ = processor._get_engine()(np.asarray(image))
    rows = processor.detections(result, 960, 260)
    assert any("你好中国" in row["text_zh"] for row in rows)
    matched = next(row for row in rows if "你好中国" in row["text_zh"])
    assert matched["confidence"] >= .75
    assert .05 < matched["bbox"][0] < .15 and .2 < matched["bbox"][1] < .6
