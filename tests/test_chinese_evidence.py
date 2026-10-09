import os

import pytest

from core.chinese_text import comparable_chinese, comparable_audio_chinese
from core.translation_review import AutomaticTranslationReviewer
from core.video_intelligence import VideoIntelligence


@pytest.mark.skipif(os.name != "nt", reason="Native Windows script converter")
@pytest.mark.parametrize("asr,ocr", [
    ("第一人稱視角體驗運動會400米", "第一人称视角体验运动会400m"),
    ("第一名打破校紀錄", "第一名打破校记录"),
])
def test_real_source_script_and_metre_spelling_match(asr, ocr):
    assert comparable_chinese(asr) == comparable_chinese(ocr)
    assert VideoIntelligence._ocr_supports_text(asr, [{"text_zh": ocr}])
    assert AutomaticTranslationReviewer._audio_text(asr) == AutomaticTranslationReviewer._audio_text(ocr)


@pytest.mark.parametrize("a,b", [
    ("不支持", "支持"), ("400米", "400毫米"), ("400m", "400mm"),
    ("1.5米", "15米"), ("-1米", "1米"), ("50%", "50"),
    ("全校记录", "学校记录"), ("会限流", "会陷流"),
    ("400ms", "400米"), ("400m/s", "400米"),
    ("400m/s", "400ms"), ("1/2", "12"), ("1:30", "130"), ("−1米", "1米"),
])
def test_comparison_never_erases_meaningful_distinctions(a, b):
    assert comparable_chinese(a) != comparable_chinese(b)
    assert not VideoIntelligence._ocr_supports_text(a, [{"text_zh": b}])


@pytest.mark.parametrize('a,b', [('19。', '十九'), ('零', '0'), ('〇', '0'),
    ('一', '1'), ('十', '10'), ('二十', '20'), ('九十九', '99')])
def test_independent_audio_numeric_spelling_is_exact(a, b):
    assert comparable_audio_chinese(a) == comparable_audio_chinese(b)
    assert AutomaticTranslationReviewer._audio_text(a) == AutomaticTranslationReviewer._audio_text(b)


@pytest.mark.parametrize('a,b', [('十九', '18'), ('十九', '九十'), ('十九', '019'),
    ('19', '1.9'), ('19', '-19'), ('19', '19%'), ('19', '十九岁'),
    ('乔一', '乔1'), ('今年十九', '今年19'), ('一九', '19'), ('十十', '20'),
    ('百', '100'), ('一/九', '19')])
def test_audio_numeric_equivalence_does_not_change_facts_units_or_names(a, b):
    assert comparable_audio_chinese(a) != comparable_audio_chinese(b)


def test_audio_number_normalization_does_not_change_ocr_scope_substrings():
    assert comparable_chinese('十九') == '十九'
    assert VideoIntelligence._ocr_supports_text('十九', [{'text_zh': '十九'}])
    assert not VideoIntelligence._ocr_supports_text('十九', [{'text_zh': '今年十九岁'}])
    assert not VideoIntelligence._ocr_supports_text('19', [{'text_zh': '十九'}])


def screen(index=0, **changes):
    return {"id": f"o{index}", "start": float(index), "end": float(index + .5),
            "text_zh": "运动会", "bbox": [.1, .1, .4, .1], "confidence": .96, **changes}


def test_repeated_background_text_shares_translation_but_keeps_every_region(monkeypatch):
    import json
    from unittest.mock import Mock
    from config import settings
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    client = Mock(has_credentials=True, model="offline")
    def respond(prompt, **kwargs):
        rows = json.loads(prompt.split("ID OCR cần xuất, vị trí/thời gian cố định: ", 1)[1].split("\nToàn bộ ngữ cảnh", 1)[0])
        return json.dumps({"segments": [], "screen_texts": [
            {"id": row["id"], "text_vi": "Hội thao", "kind": "title", "needs_review": False,
             "review_reason": ""} for row in rows], "summary": "Băng rôn hội thao"})
    client.translate.side_effect = respond
    monkeypatch.setattr("core.video_intelligence.OpenCodeZenClient", Mock(return_value=client))
    processor = object.__new__(VideoIntelligence)
    processor.provider, processor._checkpoint_context = "opencode", None
    observed = [screen(i) for i in range(30)]
    result = processor._translate_text([], observed, "", 0, 30)
    assert client.translate.call_count == 2  # draft and independent verification
    assert len(result["screen_texts"]) == 30
    for original, output in zip(observed, result["screen_texts"]):
        assert {key: output[key] for key in original} == original
        assert output["text_vi"] == "Hội thao"
        assert not output["source_region_verified"]
    assert result["segments"] == {}  # titles never become fabricated speech


@pytest.mark.parametrize("change", [{"text_zh": "运动会不开放"},
    {"bbox": [.6, .1, .3, .1]}, {"confidence": .8}, {"needs_review": True}])
def test_dedup_does_not_merge_different_evidence(change):
    representatives, _ = VideoIntelligence._group_repeated_screens([screen(), screen(1, **change)], [])
    assert len(representatives) == 2


def test_dedup_keeps_every_spoken_turn_independent():
    representatives, _ = VideoIntelligence._group_repeated_screens(
        [screen(), screen(1)], [{"start": 0, "end": .5}, {"start": 1, "end": 1.5}])
    assert len(representatives) == 2
