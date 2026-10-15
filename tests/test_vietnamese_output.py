"""Script guard regressions, not evidence of semantic translation quality."""
from copy import deepcopy
import json
import unicodedata
from unittest.mock import Mock

import pytest

from core.vietnamese_output import has_foreign_letters
from core.engines.translation.semantic_translator import SemanticTranslator, PacingReviewRejected, fluency_dialogue_context
from core.video_intelligence import VideoIntelligence, VideoIntelligenceError


FOREIGN = ["안녕하세요", "こんにちは", "สวัสดี", "你好", "𠀀", "Привет", "مرحبا", "नमस्ते", "שלום", "Γεια"]
FIELDS = ("literal_vi", "natural_vi", "final_vi")


@pytest.mark.parametrize("text", FOREIGN)
@pytest.mark.parametrize("field", FIELDS)
def test_single_and_batch_cannot_accept_untranslated_script(text, field):
    translator = SemanticTranslator("opencode")
    fields = {name: "Xin chào." for name in FIELDS}
    fields[field] = text
    payload = [{"id": 2, "text_zh": text}]
    with pytest.raises(RuntimeError, match="chữ nước ngoài"):
        translator._parse_opencode_results(json.dumps(fields), payload, single=True)
    with pytest.raises(RuntimeError, match="chữ nước ngoài"):
        translator._parse_opencode_results(json.dumps({"results": [{"id": 2, **fields}]}), payload)


@pytest.mark.parametrize("text", FOREIGN)
def test_visual_speech_rejects_foreign_letters_but_never_rewrites_source(text):
    source = {"id": 2, "start": 1., "end": 2., "asr_text": text}
    row = {"id": 2, "start": 1., "end": 2., "text_zh": text,
           **dict.fromkeys(FIELDS, text), "needs_review": False, "review_reason": ""}
    raw = {"segments": [row], "screen_texts": [], "summary": ""}
    before = deepcopy(raw)
    with pytest.raises(VideoIntelligenceError, match="chữ nước ngoài"):
        VideoIntelligence.validate_result(raw, [source])
    assert raw == before
    row.update(dict.fromkeys(FIELDS, "Xin chào."))
    assert VideoIntelligence.validate_result(raw, [source])["segments"][2]["text_zh"] == text


@pytest.mark.parametrize("text", FOREIGN)
def test_blind_context_and_historical_revalidation_do_not_accept_foreign_speech(text, monkeypatch):
    context = fluency_dialogue_context([
        {"id": 1, "final_vi": text}, {"id": 2, "is_focus": True, "final_vi": "Cũ."}],
        target={"id": 2}, draft="Cũ.", candidate="Mới.", enriched=True)
    assert context["before"][0]["status"] == "invalid_translation"
    assert "text" not in context["before"][0]
    translator = SemanticTranslator("opencode")
    request = Mock(side_effect=AssertionError("Invalid historical wording must not reach provider"))
    monkeypatch.setattr(translator, "_opencode_request", request)
    with pytest.raises(PacingReviewRejected) as caught:
        translator.revalidate_pacing_candidate(text, "Xin chào.", text)
    assert caught.value.code == "invalid_translation"
    request.assert_not_called()


@pytest.mark.parametrize("text", ["Mẹ ơi, con về rồi!", unicodedata.normalize("NFD", "Đều sẽ sống tốt hơn."),
    "iPhone 15, Wi-Fi, 25°C, 50%", "João và François", "NʼDjamena", "DʼArtagnan", "Ｘｉｎ chào!", "Hello", "… 🎵"])
def test_latin_names_vietnamese_marks_numbers_symbols_are_not_foreign_script(text):
    assert not has_foreign_letters(text)


def test_apostrophe_exception_does_not_ignore_japanese_modifier_letters():
    assert has_foreign_letters("ー")


@pytest.mark.parametrize("text", FOREIGN)
def test_untranslated_ocr_is_review_required(text):
    screen = {"id": "o0", "start": 0., "end": 1., "bbox": [.1, .1, .2, .1],
              "text_zh": text, "needs_review": False, "confidence": .99}
    raw = {"segments": [], "screen_texts": [{"id": "o0", "text_vi": text,
        "kind": "title", "needs_review": False, "review_reason": ""}], "summary": ""}
    result = VideoIntelligence.validate_result(raw, [], 0., 1., [screen])
    assert result["screen_texts"][0]["needs_review"] is True
    assert result["screen_texts"][0]["text_vi"] == text


@pytest.mark.parametrize("text", ["안녕하세요", "こんにちは", "สวัสดี"])
def test_public_translation_stops_before_downstream_on_untranslated_response(text, monkeypatch):
    from core.structured_response import StructuredResponseError
    translator = SemanticTranslator("opencode")
    request = Mock(return_value=json.dumps(dict.fromkeys(FIELDS, text)))
    fallback = Mock(side_effect=AssertionError("No fallback success"))
    monkeypatch.setattr(translator, "_opencode_request", request)
    monkeypatch.setattr(translator, "_fallback_translate", fallback)
    with pytest.raises(StructuredResponseError) as caught:
        translator.translate_single_segment(text, 1., [])
    assert caught.value.code == "semantic_content"
    assert request.call_count == 1
    fallback.assert_not_called()
