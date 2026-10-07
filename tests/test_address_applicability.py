from unittest.mock import Mock

import pytest

from config import settings
from core.translation_context import contains_address_expression, needs_address_audit

from core.translation_review import AutomaticTranslationReviewer


def test_address_neutral_thant_tu_and_number_are_not_blocked_by_uncertain_reading():
    reading = {0: {"uncertain": True}}
    for source, candidate in (("啥", "Hả?"), ("十九", "Mười chín."), ("旁白", "Cô ấy bước vào.")):
        audit = {"address_applicable": False, "address_verified": False, "address_neutral_faithful": True,
                 "semantic_verified": True, "address_reason": "Câu cảm thán/số/lời kể giữ đủ ý không cần đổi xưng hô."}
        assert AutomaticTranslationReviewer._address_gate(reading, 0, audit, source, candidate) is False


def test_relation_bearing_candidate_still_requires_address_proof():
    reading = {0: {"uncertain": True}}
    audit = {"address_applicable": True, "address_verified": False, "address_reason": "chưa rõ"}
    assert AutomaticTranslationReviewer._address_gate(reading, 0, audit, "我问你", "Chị hỏi em.") is True
    assert AutomaticTranslationReviewer._address_gate(reading, 0, {"address_applicable": False}, "我问你", "Tôi thấy mẹ.") is True


def test_old_provider_reply_uses_conservative_relation_fallback():
    reading = {0: {"uncertain": True}}
    assert AutomaticTranslationReviewer._address_gate(reading, 0, {}, "啥", "Hả?") is True
    assert AutomaticTranslationReviewer._address_gate(reading, 0, {}, "我问你", "Chị hỏi em.") is True
    for candidate in ("Thế mày?", "Mẹ ơi!", "Em!", "Tôi thấy mẹ.", "Mày nhìn con mèo."):
        assert AutomaticTranslationReviewer._address_gate(reading, 0, {}, "我问你", candidate) is True


def test_provider_cannot_hide_visible_relation_by_calling_it_neutral():
    reading = {0: {"uncertain": True}}
    audit = {"address_applicable": False, "address_verified": False}
    assert AutomaticTranslationReviewer._address_applicable(audit, "我问你", "Thế mày?") is True
    assert AutomaticTranslationReviewer._address_gate(reading, 0, audit, "我问你", "Thế mày?") is True


def test_neutral_output_preserves_separate_not_applicable_flag():
    reading = {0: {"uncertain": True}}
    audit = {"address_applicable": False, "address_verified": False, "address_neutral_faithful": True,
             "semantic_verified": True, "address_reason": "Giữ đúng số tuổi, không cần phân vai."}
    assert AutomaticTranslationReviewer._address_applicable(audit, "十九", "Mười chín.") is False
    assert AutomaticTranslationReviewer._address_gate(reading, 0, audit, "十九", "Mười chín.") is False


def test_new_relationship_cannot_pass_when_no_source_reading_was_requested():
    assert AutomaticTranslationReviewer._address_gate({}, 0,
        {"address_applicable": False, "address_verified": False}, "你先走吧", "Em đi trước nhé.") is True


def test_dropping_essential_speaker_cannot_pass_only_because_words_are_neutral():
    assert AutomaticTranslationReviewer._address_gate({0: {"uncertain": True}}, 0,
        {"address_applicable": False, "address_neutral_faithful": False, "semantic_verified": True,
         "address_reason": "Bản dịch bỏ ý tôi mới là người hỏi."}, "这话应该我来问吧", "Phải hỏi chứ.") is True


@pytest.mark.parametrize("candidate", ["Tôi đang làm việc.", "Bạn đi trước.", "Cậu hỏi nhé.",
    "Cháu hỏi nhé.", "Dì đến đây.", "Ta sẽ đi.", "Mi nói đi.", "Thế mày?"])
def test_selector_and_verifier_use_the_same_pronoun_detection(candidate):
    assert contains_address_expression(candidate)
    assert needs_address_audit([{"text_zh": "我在工作", "final_vi": candidate}])
    assert AutomaticTranslationReviewer._address_applicable({}, "我在工作", candidate)


@pytest.mark.parametrize("candidate", ["Ba phút.", "Đi một mình.", "Con vật chạy qua."])
def test_unambiguous_non_address_spans_do_not_trigger_pronoun_audit(candidate):
    assert not contains_address_expression(candidate)
    assert not needs_address_audit([{"text_zh": "三分钟", "final_vi": candidate}])
    assert not AutomaticTranslationReviewer._address_applicable({"address_applicable": False}, "三分钟", candidate)


def test_excluded_numeric_span_does_not_remove_a_real_parent_address():
    assert contains_address_expression("Ba chờ ba phút.")
    assert contains_address_expression("Mày đi một mình.")


@pytest.mark.parametrize("initial,candidate,expected_reading", [
    ("Tôi đang làm việc.", "Tôi đang làm việc.", True),
    ("Đang làm việc.", "Em đang làm việc.", False),
])
def test_real_review_path_requests_reading_for_existing_pronoun_and_records_new_unproved_pronoun(
        monkeypatch, initial, candidate, expected_reading):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    source = {"id": 0, "start": 0., "end": 2., "text_zh": "我在工作",
              "literal_vi": initial, "natural_vi": initial, "final_vi": initial,
              "needs_review": False, "review_reason": ""}
    requests = []
    def respond(prompt, **kwargs):
        requests.append(prompt)
        if "ID cần kiểm định: " in prompt:
            return {"address_context": [{"id": 0, "self_address": "tôi", "listener_address": "",
                "uncertain": False, "reason": "Người nói tự nói đang làm việc, không suy thêm quan hệ.",
                "evidence": [{"id": 0, "quote": "我在工作"}]}]}
        return {"segments": [{**source, "literal_vi": candidate, "natural_vi": candidate, "final_vi": candidate,
            "semantic_verified": True, "verification_reason": "Giữ đang làm việc.",
            "source_evidence_ids": ["review0"], "address_applicable": True,
            "address_verified": True, "address_reason": "Đối chiếu lời nguồn."}], "screen_texts": [], "summary": ""}
    client = Mock(has_credentials=True, model="offline-selection", translate=Mock(side_effect=respond))
    scanner = Mock(extract=Mock(return_value=[{"start": 0., "end": 2., "text_zh": "我在工作",
        "confidence": .99, "bbox": [.2, .7, .5, .1]}]))
    reviewer = AutomaticTranslationReviewer(client, scanner, audio_evidence=False)
    result = reviewer.review("unused", [source], [])["segments"][0]
    assert any("ID cần kiểm định: " in prompt for prompt in requests) is expected_reading
    assert result["verification"]["address_applicable"] is True
    assert result["verification"]["address_verified"] is expected_reading
    assert result["needs_review"] is (not expected_reading)
