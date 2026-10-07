"""Offline guards for contextual address; real Muse fidelity is tested separately."""
import json
from copy import deepcopy
from unittest.mock import Mock

import pytest

from config import settings
from core.engines.translation.semantic_translator import SemanticTranslator, PacingReviewRejected
from core.translation_context import (
    VIETNAMESE_ADDRESS_POLICY, added_rude_address,
    dialogue_context, address_reading_prompt, validate_address_reading,
)
from core.translation_review import AutomaticTranslationReviewer
from core.video_intelligence import VISUAL_TRANSLATION_PROMPT


def source(index, text, vi="Lời nháp"):
    return {"id": index, "start": float(index * 3), "end": float(index * 3 + 2),
            "text_zh": text, "literal_vi": vi, "natural_vi": vi, "final_vi": vi,
            "needs_review": False, "review_reason": ""}


def test_context_retains_early_address_evidence_and_neighbors_beyond_ten_seconds():
    rows = [source(i, "这是普通的句子") for i in range(150)]
    rows[1] = source(1, "拜托姐", "Thôi mà chị.")
    rows[-1] = source(149, "这话应该我来问吧", "Câu này phải để tao hỏi mới đúng chứ.")
    before = deepcopy(rows)
    context = dialogue_context(rows, [rows[-1]])
    ids = [row["id"] for row in context]
    assert all(i in ids for i in (0, 1, 2, 148, 149))
    assert ids == sorted(set(ids))
    assert len(context) <= 64 and len(json.dumps(context, ensure_ascii=False)) <= 18000
    assert all(row["translation_is_draft"] for row in context)
    assert rows == before


def test_context_does_not_assign_relationships_from_a_kinship_keyword():
    context = dialogue_context([source(0, "他说他妈妈今天没来", "Anh ấy nói hôm nay mẹ không đến.")])
    assert "speaker_id" not in context[0] and "addressee_id" not in context[0]
    assert context[0]["text_zh"] == "他说他妈妈今天没来"


def test_address_guard_does_not_confuse_pronouns_with_other_vietnamese_words():
    assert added_rude_address("Em thử tìm hiểu.", "Em mày mò thử.") == []
    assert added_rude_address("Chị cau mặt.", "Chị nhíu mày.") == []
    assert added_rude_address("Trông rất đẹp.", "Trông thanh tao.") == []
    assert added_rude_address("Em thử xem.", "Mày mò rồi mày biết.") == ["mày"]


def test_same_policy_reaches_initial_translation_and_both_pacing_requests(monkeypatch):
    assert VIETNAMESE_ADDRESS_POLICY in VISUAL_TRANSLATION_PROMPT
    translator = SemanticTranslator("opencode")
    candidate = {"literal_vi": "Câu này phải để em hỏi mới đúng chứ.",
                 "natural_vi": "Để em hỏi mới đúng.", "final_vi": "Để em hỏi mới đúng."}
    request = Mock(side_effect=[json.dumps(candidate), json.dumps({
        "equivalent": True, "natural": True, "address_preserved": True, "reason": "Giữ lời đáp xưng em khi gọi chị."})])
    monkeypatch.setattr(translator, "_opencode_request", request)
    context = [{"zh": "拜托姐", "vi": "Thôi mà chị."}] + [
        {"zh": "这是普通的句子", "vi": "Lời thoại."} for _ in range(8)]
    result = translator.rewrite_for_pacing("这话应该我来问吧", candidate["literal_vi"], 2.0, context)
    assert result["final_vi"] == candidate["final_vi"]
    for call in request.call_args_list:
        assert VIETNAMESE_ADDRESS_POLICY in call.args[0]
        assert "拜托姐" in call.args[1]
    request.reset_mock(side_effect=True)
    request.return_value = json.dumps(candidate)
    translator.translate_single_segment("这话应该我来问吧", 2.0, context)
    assert VIETNAMESE_ADDRESS_POLICY in request.call_args.args[0]
    assert "拜托姐" in request.call_args.args[0]


@pytest.mark.parametrize(("draft", "candidate"), [
    ("Câu này phải để em hỏi mới đúng chứ.", "Phải để tao hỏi chứ."),
    ("Chị hỏi em bao nhiêu tuổi rồi, nói mau.", "Mày mấy tuổi, nói mau?"),
    ("Con chưa làm xong, mẹ ạ.", "Tao chưa làm xong."),
])
def test_pacing_cannot_introduce_hostile_address_even_if_provider_claims_success(monkeypatch, draft, candidate):
    translator = SemanticTranslator("opencode")
    request = Mock(return_value=json.dumps({"literal_vi": candidate, "natural_vi": candidate,
                                           "final_vi": candidate, "needs_review": False}))
    monkeypatch.setattr(translator, "_opencode_request", request)
    with pytest.raises(PacingReviewRejected) as error:
        translator.rewrite_for_pacing("我问你几岁了快说", draft, 1.0)
    assert error.value.code == "semantic_mismatch"
    assert request.call_count == 1


def test_batch_retains_unresolved_address_flag_instead_of_claiming_certain(monkeypatch):
    translator = SemanticTranslator("opencode")
    response = {"id": 0, "literal_vi": "Ai hỏi vậy?", "natural_vi": "Ai hỏi vậy?",
                "final_vi": "Ai hỏi vậy?", "needs_review": True,
                "review_reason": "Chưa xác định người nói và người nghe."}
    request = Mock(side_effect=[json.dumps({"theme": "Đối thoại", "terms": [],
                                           "pronouns": "Chưa xác định"}),
                               json.dumps({"results": [response]})])
    monkeypatch.setattr(translator, "_opencode_request", request)
    result = translator.translate([source(0, "谁问的")])[0]
    assert result["needs_review"] is True
    assert result["review_reason"] == response["review_reason"]
    assert all(VIETNAMESE_ADDRESS_POLICY in call.args[0] for call in request.call_args_list)


def test_reviewer_receives_early_source_context_in_both_passes(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    rows = [source(i, "这是普通的句子") for i in range(13)]
    rows[0] = source(0, "拜托姐", "Thôi mà chị.")
    rows[12] = source(12, "这话应该我来问吧", "Câu này phải để tao hỏi mới đúng chứ.")
    prompts = []
    def respond(prompt, **kwargs):
        prompts.append(prompt)
        if "ID cần kiểm định: " in prompt:
            ids = json.loads(prompt.split("ID cần kiểm định: ", 1)[1].split("\nNguồn thoại", 1)[0])
            assert "Lời nháp" not in prompt and "Câu này phải để tao" not in prompt
            return {"address_context": [{"id": sid, "self_address": "em", "listener_address": "chị",
                "uncertain": False, "reason": "Lời gọi chị trong mạch thoại.",
                "evidence": [{"id": 0, "quote": "拜托姐"}]} for sid in ids]}
        batch = json.loads(prompt.split("Câu cần kiểm định: ", 1)[1].split("\nOCR mới tại máy", 1)[0])
        return {"segments": [{**row, "semantic_verified": True,
                 "address_verified": True, "address_reason": "Đã kiểm tra chiều xưng hô theo nguồn.",
                 "verification_reason": "Đã đối chiếu lời nguồn và quan hệ xưng hô.",
                 "source_evidence_ids": [f"review{row['id']}"]} for row in batch],
                "screen_texts": [], "summary": ""}
    client = Mock(has_credentials=True, model="offline-context-probe")
    client.translate.side_effect = respond
    scanner = Mock()
    scanner.extract.side_effect = lambda path, start, end, check: [
        {"start": row["start"], "end": row["end"], "text_zh": row["text_zh"],
         "confidence": .99, "bbox": [.2, .7, .5, .1]} for row in rows
        if row["start"] < end and row["end"] > start]
    reviewer = AutomaticTranslationReviewer(client, scanner, audio_evidence=False)
    reviewer.review("unused", rows, [])
    assert len(prompts) == 6
    for prompt in prompts[4:]:
        assert VIETNAMESE_ADDRESS_POLICY in prompt
        context_text = prompt.split("Ngữ cảnh lân cận (không tạo thêm ID): ", 1)[1].split("\nĐã có lượt", 1)[0]
        context = json.loads(context_text)
        assert any(row["id"] == 0 and row["text_zh"] == "拜托姐" for row in context)
        assert any(row["id"] == 12 and row["translation_is_draft"] for row in context)


def test_source_only_address_reading_accepts_continuity_without_speaker_metadata():
    rows = [source(0, "妈妈我回来了", "Mẹ ơi, con về rồi."), source(1, "我饿了", "Tôi đói.")]
    context = dialogue_context(rows, rows)
    prompt = address_reading_prompt(rows, context)
    assert "Tôi đói." not in prompt
    data = {"address_context": [{"id": row["id"], "self_address": "con", "listener_address": "mẹ",
        "uncertain": False, "reason": "Lời gọi mẹ rồi tiếp tục nói mình đói.",
        "evidence": [{"id": 0, "quote": "妈妈我回来了"}, {"id": 1, "quote": "我饿了"}]} for row in rows]}
    reading = validate_address_reading(data, rows, context)
    assert not reading[1]["uncertain"]
    assert AutomaticTranslationReviewer._address_verified(reading, 1,
        {"semantic_verified": True, "address_verified": True, "address_reason": "Cùng mạch con nói với mẹ."})
    assert not AutomaticTranslationReviewer._address_verified(reading, 1, {"semantic_verified": True})
    data["address_context"][1]["evidence"][0]["quote"] = "爸爸"
    with pytest.raises(ValueError):
        validate_address_reading(data, rows, context)


def test_ocr_semantic_success_cannot_clear_uncertain_or_failed_address_review():
    reading = {3: {"uncertain": True}}
    assert not AutomaticTranslationReviewer._address_verified(reading, 3,
        {"semantic_verified": True, "address_verified": True, "address_reason": "OCR trùng chữ."})
    reading[3]["uncertain"] = False
    assert not AutomaticTranslationReviewer._address_verified(reading, 3,
        {"semantic_verified": True, "address_verified": False, "address_reason": "Chưa biết ai đang nói."})


@pytest.mark.parametrize("address_verified", [True, False, None])
def test_actual_reviewer_keeps_ocr_and_address_decisions_separate(monkeypatch, address_verified):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    rows = [source(0, "妈妈我回来了", "Mẹ ơi, con về rồi."), source(1, "我饿了", "Con đói rồi.")]
    def respond(prompt, **kwargs):
        if "ID cần kiểm định: " in prompt:
            assert "Con đói rồi." not in prompt
            return {"address_context": [{"id": row["id"], "self_address": "con", "listener_address": "mẹ",
                "uncertain": False, "reason": "Cùng mạch về nhà rồi nói đói với mẹ.",
                "evidence": [{"id": 0, "quote": "妈妈我回来了"}, {"id": 1, "quote": "我饿了"}]} for row in rows]}
        return {"segments": [{**row, "semantic_verified": True, "verification_reason": "OCR xác nhận lời nguồn.",
            "source_evidence_ids": [f"review{row['id']}"], "address_verified": address_verified,
            "address_reason": "Lượt nói tiếp tục cùng người con."} for row in rows], "screen_texts": [], "summary": ""}
    client = Mock(has_credentials=True, model="offline-context-probe", translate=Mock(side_effect=respond))
    scanner = Mock(extract=Mock(return_value=[{"start": row["start"], "end": row["end"],
        "text_zh": row["text_zh"], "confidence": .99, "bbox": [.2, .7, .5, .1]} for row in rows]))
    reviewer = AutomaticTranslationReviewer(client, scanner, audio_evidence=False)
    result = reviewer.review("unused", rows, [])
    for row in result["segments"].values():
        assert row["verification"]["source_supported"] is True
        assert row["verification"]["address_verified"] is (address_verified is True)
        assert row["needs_review"] is (address_verified is not True)
        assert row["verification"]["semantic_verified"] is (address_verified is True)
    assert client.translate.call_count == 3


def test_dialogue_context_sorts_timestamps_and_retains_reviewed_direction():
    rows = [source(1, "我饿了"), source(0, "妈妈我回来了")]
    rows[0]["verification"] = {"address_verified": True,
        "address_context": {"id": 1, "self_address": "con", "listener_address": "mẹ", "uncertain": False}}
    context = dialogue_context(rows)
    assert [row["id"] for row in context] == [0, 1]
    assert context[1]["reviewed_address_context"]["self_address"] == "con"


def test_duplicate_source_turns_keep_explicit_target_identity_in_pacing(monkeypatch):
    translator = SemanticTranslator("opencode")
    candidate = {"literal_vi": "Con chưa làm xong.", "natural_vi": "Con chưa xong.", "final_vi": "Con chưa xong."}
    request = Mock(side_effect=[json.dumps(candidate), json.dumps({"equivalent": True, "natural": True,
        "address_preserved": True, "reason": "Giữ con ở đúng lượt trả lời mẹ."})])
    monkeypatch.setattr(translator, "_opencode_request", request)
    context = [{**source(0, "我还没做完"), "is_focus": False},
               {**source(1, "我还没做完"), "is_focus": True}]
    translator.rewrite_for_pacing("我还没做完", "Con chưa làm xong.", 1.2, context)
    assert '"id": 1' in request.call_args_list[0].args[1]
    assert json.loads(request.call_args_list[1].args[1])["target"] == {"id": 1, "start": 3.0, "end": 5.0}


def test_fluent_pacing_cannot_pass_without_separate_address_preservation(monkeypatch):
    translator = SemanticTranslator("opencode")
    candidate = {"literal_vi": "Chị phải hỏi chứ.", "natural_vi": "Chị phải hỏi chứ.", "final_vi": "Chị phải hỏi chứ."}
    request = Mock(side_effect=[json.dumps(candidate), json.dumps({"equivalent": True, "natural": True,
        "address_preserved": False, "reason": "Đã đổi người đang nói từ em sang chị."})])
    monkeypatch.setattr(translator, "_opencode_request", request)
    with pytest.raises(PacingReviewRejected):
        translator.rewrite_for_pacing("这话应该我来问吧", "Câu này phải để em hỏi mới đúng chứ.", 1.2)
