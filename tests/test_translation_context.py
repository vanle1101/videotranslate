"""Offline guards for contextual address; real Muse fidelity is tested separately."""
import json
from copy import deepcopy
from unittest.mock import Mock

import pytest

from config import settings
from core.engines.translation.semantic_translator import SemanticTranslator, PacingReviewRejected
from core.translation_context import (
    VIETNAMESE_ADDRESS_POLICY, added_rude_address,
    dialogue_context, unproven_relationship_address,
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


@pytest.mark.parametrize(("source", "candidate"), [
    ("我问你几岁了快说", "Chị hỏi em bao nhiêu tuổi rồi."),
    ("跟我走", "Đi theo chị."),
    ("这话应该我来问吧", "Câu này phải để chị hỏi mới đúng chứ."),
])
def test_relationship_pronoun_without_speaker_evidence_stays_unverified(source, candidate):
    assert unproven_relationship_address(source, candidate, [{"text_zh": "拜托姐"}])
    assert unproven_relationship_address(source, candidate, [{"speaker_id": "female"}])
    assert unproven_relationship_address(source, candidate, [{"speaker_id": "female", "text_zh": source}])


@pytest.mark.parametrize(("source", "candidate"), [
    ("妈妈我饿了", "Mẹ ơi, con đói rồi."),
    ("爸爸，我还没做完", "Bố ơi, con chưa làm xong."),
    ("姐你听我说", "Chị nghe em nói này."),
    ("老师我不明白", "Thầy ơi, em chưa hiểu."),
    ("我有一只猫", "Tôi có một con mèo."),
    ("我喜欢那个女孩", "Tôi thích cô gái đó."),
    ("我皱起眉毛", "Tôi nhíu mày."),
    ("我还有三个", "Tôi còn ba cái."),
    ("我哥哥说他会回来", "Anh trai tôi nói sẽ về."),
    ("我问妈妈", "Tôi hỏi mẹ."),
])
def test_local_source_address_and_noun_uses_do_not_need_speaker_ids(source, candidate):
    assert not unproven_relationship_address(source, candidate)


def test_neutral_wording_does_not_receive_a_relationship_warning():
    assert not unproven_relationship_address("这话应该我来问吧", "Câu này phải để tôi hỏi mới đúng chứ.")
    assert not unproven_relationship_address("跟我走", "Đi theo tôi.")


def test_explicit_source_noun_can_ground_only_the_matching_object():
    assert not unproven_relationship_address("我问妈妈", "Tôi hỏi mẹ.")
    assert unproven_relationship_address("我问妈妈", "Con hỏi mẹ.")
    assert unproven_relationship_address("我问妈妈", "Chị hỏi mẹ.")


def test_matching_speaker_and_listener_may_continue_source_grounded_address():
    context = [{"text_zh": "妈妈我饿了", "speaker_id": "child", "addressee_id": "mother"},
               {"text_zh": "我先吃了", "speaker_id": "child", "addressee_id": "mother"}]
    assert not unproven_relationship_address("我先吃了", "Con ăn trước đây.", context)
    context[1]["speaker_id"] = "other"
    assert unproven_relationship_address("我先吃了", "Con ăn trước đây.", context)


def test_same_policy_reaches_initial_translation_and_both_pacing_requests(monkeypatch):
    assert VIETNAMESE_ADDRESS_POLICY in VISUAL_TRANSLATION_PROMPT
    translator = SemanticTranslator("opencode")
    candidate = {"literal_vi": "Câu này phải để em hỏi mới đúng chứ.",
                 "natural_vi": "Để em hỏi mới đúng.", "final_vi": "Để em hỏi mới đúng."}
    request = Mock(side_effect=[json.dumps(candidate), json.dumps({
        "equivalent": True, "natural": True, "reason": "Giữ lời đáp xưng em khi gọi chị."})])
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
        batch = json.loads(prompt.split("Câu cần kiểm định: ", 1)[1].split("\nOCR mới tại máy", 1)[0])
        return {"segments": [{**row, "semantic_verified": True,
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
    assert len(prompts) == 4
    for prompt in prompts[2:]:
        assert VIETNAMESE_ADDRESS_POLICY in prompt
        context_text = prompt.split("Ngữ cảnh lân cận (không tạo thêm ID): ", 1)[1].split("\nĐÂY LÀ", 1)[0]
        context = json.loads(context_text)
        assert any(row["id"] == 0 and row["text_zh"] == "拜托姐" for row in context)
        assert any(row["id"] == 12 and row["translation_is_draft"] for row in context)
