"""Offline guards for contextual address; real Muse fidelity is tested separately."""
import json
from copy import deepcopy
from unittest.mock import Mock

import pytest

from config import settings
from core.engines.translation.semantic_translator import SemanticTranslator, PacingReviewRejected
from core.translation_context import (
    VIETNAMESE_ADDRESS_POLICY, added_rude_address,
    dialogue_context, source_dialogue, address_reading_prompt, validate_address_reading,
)
from core.translation_review import AutomaticTranslationReviewer
from core.video_intelligence import VISUAL_TRANSLATION_PROMPT, VideoIntelligenceError
from core.semantic_segments import source_speaker_confirmation


def source(index, text, vi="Lời nháp"):
    return {"id": index, "start": float(index * 3), "end": float(index * 3 + 2),
            "text_zh": text, "literal_vi": vi, "natural_vi": vi, "final_vi": vi,
            "needs_review": False, "review_reason": ""}


def confirmed_source(*, unknown=False, selection="scoped_voice"):
    row = source(12, "我真的不是回来", "Bản nháp Việt không phải bằng chứng.")
    speaker = None if unknown else "voice-a"
    row.update(speaker_id=speaker,
        speaker_evidence={"speaker_id": speaker, "verified": False,
            "method": "audio_diarization", "scope_id": "source-video", "model": "measured-model"},
        speaker_confirmation={"confirmation_id": "a" * 32, "method": "user_confirmation",
            "anchor_segment_id": 12, "affected_ids": [12], "speaker_id": speaker,
            "scope_id": "source-video", "label": "Nhân vật đã xác nhận", "self_address": "con",
            "listener_address": "bố", "voice_id": None, "created_at": 100., "selection": selection})
    return row


@pytest.mark.parametrize("mutation", ["unknown_becomes_known", "different_voice", "different_scope",
    "unselected_row", "conflicting_alias", "malformed_claim"])
def test_address_projections_reject_same_stale_confirmation_as_semantic_context(mutation):
    row = confirmed_source(unknown=mutation == "unknown_becomes_known",
                           selection="anchor" if mutation == "unknown_becomes_known" else "scoped_voice")
    if mutation in {"unknown_becomes_known", "different_voice"}:
        row["speaker_id"] = row["speaker_evidence"]["speaker_id"] = "newly-measured-voice"
    elif mutation == "different_scope":
        row["speaker_evidence"]["scope_id"] = "another-source-video"
    elif mutation == "unselected_row":
        row["id"] = 13
    elif mutation == "conflicting_alias":
        row["speaker"] = "another-voice"
    else:
        row["speaker_confirmation"] = {"label": "Forged or damaged role", "verified": True}
    before = deepcopy(row)
    assert source_speaker_confirmation(row) is None
    context = dialogue_context([row], [row])
    assert "speaker_confirmation" not in context[0]
    assert "speaker_confirmation" not in source_dialogue([row])[0]
    assert "speaker_confirmation" not in source_dialogue(context)[0]
    prompt_payload = json.loads(address_reading_prompt([row], [row]).split("Nguồn thoại theo thời gian: ", 1)[1])
    assert "speaker_confirmation" not in prompt_payload[0]
    assert row == before  # Persisted user action is kept for review, not erased.


@pytest.mark.parametrize("unknown,selection", [(False, "scoped_voice"), (False, "anchor"), (True, "anchor")])
def test_address_projections_preserve_valid_scoped_or_unknown_anchor_claim_without_aliasing(unknown, selection):
    row = confirmed_source(unknown=unknown, selection=selection)
    before = deepcopy(row)
    assert source_speaker_confirmation(row) == row["speaker_confirmation"]
    context = dialogue_context([row], [row])
    projected = source_dialogue(context)
    direct = source_dialogue([row])
    assert context[0]["speaker_confirmation"] == before["speaker_confirmation"]
    assert projected[0]["speaker_confirmation"] == direct[0]["speaker_confirmation"] == before["speaker_confirmation"]
    assert "final_vi" not in projected[0] and "verification" not in projected[0]
    projected[0]["speaker_confirmation"]["self_address"] = "Changed projected copy"
    context[0]["speaker_confirmation"]["label"] = "Changed context copy"
    direct[0]["speaker_confirmation"]["affected_ids"].append(99)
    assert row == before


@pytest.mark.parametrize("speaker_key", ["speaker", "diarization_speaker", "spk"])
def test_valid_voice_alias_confirmation_survives_reformatted_address_context(speaker_key):
    row = confirmed_source()
    row[speaker_key] = row.pop("speaker_id")
    context = dialogue_context([row], [row])
    projected = source_dialogue(context)
    repeated = dialogue_context(projected, projected)
    assert all(payload[0]["speaker_confirmation"] == row["speaker_confirmation"]
               for payload in (context, projected, repeated))
    assert all(payload[0][speaker_key] == "voice-a" for payload in (context, projected, repeated))


def test_unknown_anchor_without_diarization_remains_a_row_scoped_user_assertion():
    row = confirmed_source(unknown=True, selection="anchor")
    row.pop("speaker_evidence")
    row["speaker_confirmation"]["scope_id"] = None
    projected = source_dialogue(dialogue_context([row], [row]))
    assert projected[0]["speaker_confirmation"] == row["speaker_confirmation"]
    assert projected[0]["speaker_confirmation"]["affected_ids"] == [12]
    assert "speaker_id" not in projected[0]  # No invented diarization identity.


def test_batch_summary_preserves_turn_boundaries_and_timestamps(monkeypatch):
    translator = SemanticTranslator("opencode")
    rows = [source(1, "这话应该我来问吧"), source(0, "拜托姐")]
    summary = Mock(return_value={"theme": "Đối thoại", "pronouns": "em–chị", "terms": []})
    execute = Mock(return_value={row["id"]: {"literal_vi": "Chị ơi.",
        "natural_vi": "Chị ơi.", "final_vi": "Chị ơi."} for row in rows})
    monkeypatch.setattr(translator, "_extract_context_and_glossary", summary)
    monkeypatch.setattr(translator, "_execute_3tier_translation", execute)
    translator.translate(rows)
    context = json.loads(summary.call_args.args[0])
    assert [row["id"] for row in context] == [0, 1]
    assert context[0]["text_zh"] == "拜托姐"
    assert context[1]["start"] == 3 and context[1]["end"] == 5
    assert all("start" in row and "end" in row for row in execute.call_args.args[0])


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


def test_independent_address_reading_keeps_source_proof_without_a_translated_draft():
    row = source(12, "我真的不是回来", "Bản nháp Việt không phải bằng chứng.")
    row.update(speaker_id="voice-a", addressee_id="voice-b", utterance_id="utterance-a",
        speaker_evidence={"speaker_id": "voice-a", "verified": True,
            "method": "audio_diarization", "confidence": .94, "model": "measured-model",
            "scope_id": "source-video"},
        utterance_evidence={"utterance_id": "utterance-a", "verified": False,
            "reason": "Unknown continuity"},
        source_asr_row_id=4, source_asr_start=36., source_asr_end=39.,
        source_piece_index=0, source_piece_count=2,
        verification={"address_verified": True, "address_context": {"self_address": "con"}})
    before = deepcopy(row)
    context = dialogue_context([row], [row])
    payload = source_dialogue(context)
    for key in ("speaker_id", "speaker_evidence", "addressee_id", "utterance_id", "utterance_evidence",
            "source_asr_row_id", "source_asr_start", "source_asr_end", "source_piece_index", "source_piece_count"):
        assert payload[0][key] == row[key]
    assert not any(key in payload[0] for key in ("final_vi", "literal_vi", "natural_vi",
        "translation_is_draft", "reviewed_address_context", "verification"))
    prompt_payload = json.loads(address_reading_prompt([row], context).split("Nguồn thoại theo thời gian: ", 1)[1])
    assert prompt_payload == payload
    assert row["final_vi"] not in address_reading_prompt([row], context)
    assert payload[0]["utterance_evidence"]["verified"] is False
    payload[0]["speaker_evidence"]["confidence"] = 0
    payload[0]["utterance_evidence"]["verified"] = True
    assert row == before and context[0]["speaker_evidence"]["confidence"] == .94


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
        "equivalent": True, "natural": True, "address_preserved": True, "reason": "Giữ lời đáp xưng em khi gọi chị."}),
        json.dumps({"natural": True, "reason": "Câu thoại tự nhiên."})])
    monkeypatch.setattr(translator, "_opencode_request", request)
    context = [{"zh": "拜托姐", "vi": "Thôi mà chị."}] + [
        {"zh": "这是普通的句子", "vi": "Lời thoại."} for _ in range(8)]
    result = translator.rewrite_for_pacing("这话应该我来问吧", candidate["literal_vi"], 2.0, context)
    assert result["final_vi"] == candidate["final_vi"]
    for call in request.call_args_list[:2]:
        assert VIETNAMESE_ADDRESS_POLICY in call.args[0]
        assert "拜托姐" in call.args[1]
    assert "拜托姐" not in request.call_args_list[2].args[1]
    assert "Để em hỏi mới đúng." in request.call_args_list[2].args[1]
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
                "turn_check": {"ambiguous_roles": [], "reason": "Đã xét lời nối tiếp trong ngữ cảnh.",
                    "evidence": [{"id": sid, "quote": rows[sid]["text_zh"]}]},
                "evidence": [{"id": 0, "quote": "拜托姐"}]} for sid in ids]}
        batch = json.loads(prompt.split("Câu cần kiểm định: ", 1)[1].split("\nOCR mới tại máy", 1)[0])
        return {"segments": [{**row, "semantic_verified": True,
                 "address_applicable": True, "address_verified": True, "address_reason": "Đã kiểm tra chiều xưng hô theo nguồn.",
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
    assert len(prompts) == 8
    review_prompts = [prompt for prompt in prompts if 'Câu cần kiểm định: ' in prompt]
    assert len(review_prompts) == 4
    for prompt in review_prompts[-2:]:
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


@pytest.mark.parametrize("address_verified", [True, False])
def test_actual_reviewer_keeps_ocr_and_address_decisions_separate(monkeypatch, address_verified):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    rows = [source(0, "妈妈我回来了", "Mẹ ơi, con về rồi."), source(1, "我饿了", "Con đói rồi.")]
    def respond(prompt, **kwargs):
        if "ID cần kiểm định: " in prompt:
            assert "Con đói rồi." not in prompt
            return {"address_context": [{"id": row["id"], "self_address": "con", "listener_address": "mẹ",
                "uncertain": False, "reason": "Cùng mạch về nhà rồi nói đói với mẹ.",
                "turn_check": {"ambiguous_roles": [], "reason": "Lượt hiện tại tiếp tục lời gọi mẹ.",
                    "evidence": [{"id": row["id"], "quote": row["text_zh"]}]},
                "evidence": [{"id": 0, "quote": "妈妈我回来了"}, {"id": 1, "quote": "我饿了"}]} for row in rows]}
        return {"segments": [{**row, "semantic_verified": True, "verification_reason": "OCR xác nhận lời nguồn.",
            "source_evidence_ids": [f"review{row['id']}"], "address_applicable": True, "address_verified": address_verified,
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


def test_needs_review_for_address_does_not_mark_source_uncertain_when_source_supported():
    rows = [{**source(0, "这话应该我来问吧", "Câu này phải để em hỏi."), "needs_review": True,
        "verification": {"source_supported": True}}]
    context = dialogue_context(rows)
    assert "source_needs_review" not in context[0]


def test_duplicate_source_turns_keep_explicit_target_identity_in_pacing(monkeypatch):
    translator = SemanticTranslator("opencode")
    candidate = {"literal_vi": "Con chưa làm xong.", "natural_vi": "Con chưa xong.", "final_vi": "Con chưa xong."}
    request = Mock(side_effect=[json.dumps(candidate), json.dumps({"equivalent": True, "natural": True,
        "address_preserved": True, "reason": "Giữ con ở đúng lượt trả lời mẹ."}),
        json.dumps({"natural": True, "reason": "Câu thoại tự nhiên."})])
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


def test_source_turn_policy_compares_parses_without_assuming_real_world_chronology():
    rows = [source(0, "我是你儿子"), source(1, "我三十五"), source(2, "我二十七")]
    rows[1]["speaker_id"] = "A"
    prompt = address_reading_prompt(rows, dialogue_context(rows, rows))
    assert "cách đọc cùng người nói" in prompt
    assert "'không thấy đổi lượt' không đủ" in prompt
    assert "lịch hiện tại" in prompt and "du hành thời gian" in prompt
    assert "giữ nguyên đối lập" in prompt.casefold()
    assert "turn_check" in prompt and "ambiguous_roles" in prompt
    payload = json.loads(prompt.split("Nguồn thoại theo thời gian: ", 1)[1])
    assert payload[1]["speaker_id"] == "A"
    assert all("final_vi" not in row for row in payload)


def turn_reading(rows, *, roles=("self",)):
    return {"address_context": [{"id": rows[-1]["id"], "self_address": "con",
        "listener_address": "bố", "self_uncertain": False, "listener_uncertain": False,
        "uncertain": False, "reason": "Cách đọc nối vai ban đầu.",
        "evidence": [{"id": rows[0]["id"], "quote": rows[0]["text_zh"]}],
        "turn_check": {"ambiguous_roles": list(roles),
            "reason": "Lời tuổi khác nhau có thể là sửa lời hoặc hai người đối chiếu; chưa đủ căn cứ loại một cách.",
            "evidence": [{"id": row["id"], "quote": row["text_zh"]} for row in rows]}}]}


def test_unresolved_competing_turns_cannot_be_verified_by_confidence_or_exact_ocr():
    rows = [source(0, "我是你女儿"), source(1, "我十八"), source(2, "我十九")]
    data = turn_reading(rows)
    original = deepcopy(data)
    reading = validate_address_reading(data, [rows[-1]], rows)
    assert reading[2]["uncertain"] is True and reading[2]["self_uncertain"] is True
    assert reading[2]["listener_uncertain"] is False
    assert "Kiểm tra phân lượt:" in reading[2]["reason"]
    audit = {"semantic_verified": True, "address_verified": True,
        "address_reason": "OCR trùng từng chữ.", "address_applicable": True,
        "address_uses": [{"term": "Con", "role": "self"}]}
    assert AutomaticTranslationReviewer._address_gate(reading, 2, audit, "我十九", "Con mười chín.")
    assert data == original
    # A later correction to either side of this exchange must invalidate the
    # reading, not just a correction to the original relationship anchor.
    assert AutomaticTranslationReviewer._address_cites_changed_source(reading, 2, {1})
    assert AutomaticTranslationReviewer._address_cites_changed_source(reading, 2, {2})


def test_competing_speaker_parses_preserve_independently_grounded_listener():
    rows = [source(0, "你怎么知道"), source(1, "拜托姐")]
    data = turn_reading(rows)
    data["address_context"][0]["listener_address"] = "chị"
    reading = validate_address_reading(data, [rows[-1]], rows)
    audit = {"semantic_verified": True, "address_verified": True,
        "address_reason": "Chỉ giữ lời gọi chị trực tiếp trong câu, không thêm tự xưng.",
        "address_applicable": True, "address_uses": [{"term": "Chị", "role": "listener"}]}
    assert not AutomaticTranslationReviewer._address_gate(reading, 1, audit, "拜托姐", "Chị ơi!")


@pytest.mark.parametrize("change", [
    {"ambiguous_roles": ["speaker"]}, {"ambiguous_roles": [{}]},
    {"ambiguous_roles": "self"}, {"reason": ""}, {"evidence": []},
    {"evidence": [{"id": 0, "quote": "我是你女儿"}]},
    {"evidence": [{"id": 2, "quote": "我二十"}]},
])
def test_turn_analysis_requires_valid_current_source_evidence(change):
    rows = [source(0, "我是你女儿"), source(1, "我十八"), source(2, "我十九")]
    data = turn_reading(rows)
    data["address_context"][0]["turn_check"].update(change)
    with pytest.raises(ValueError):
        validate_address_reading(data, [rows[-1]], rows)


def test_resolved_turn_analysis_does_not_force_alternation_or_clear_existing_uncertainty():
    rows = [source(0, "妈妈我回来了"), source(1, "我饿了")]
    data = turn_reading(rows, roles=())
    reading = validate_address_reading(data, [rows[-1]], rows)
    assert reading[1]["uncertain"] is False
    data["address_context"][0].update(self_uncertain=True, uncertain=True)
    reading = validate_address_reading(data, [rows[-1]], rows)
    assert reading[1]["self_uncertain"] is True and reading[1]["uncertain"] is True


def test_explicit_null_turn_check_is_rejected_instead_of_becoming_legacy_success():
    rows = [source(0, "妈妈我回来了")]
    data = turn_reading(rows)
    data["address_context"][0]["turn_check"] = None
    with pytest.raises(ValueError):
        validate_address_reading(data, rows, rows)


def test_fresh_address_reading_cannot_skip_turn_analysis_but_legacy_remains_readable():
    rows = [source(0, "我十八", "Tôi mười tám.")]
    legacy = {"address_context": [{"id": 0, "self_address": "tôi", "listener_address": "",
        "uncertain": False, "reason": "Người nói nêu tuổi.",
        "evidence": [{"id": 0, "quote": "我十八"}]}]}
    assert validate_address_reading(legacy, rows, rows)[0]["self_address"] == "tôi"
    with pytest.raises(ValueError, match="phân lượt"):
        validate_address_reading(legacy, rows, rows, require_turn_check=True)
    client = Mock(translate=Mock(return_value=legacy))
    with pytest.raises(VideoIntelligenceError):
        AutomaticTranslationReviewer._address_reading(client, rows, rows, lambda: None)
    assert client.translate.call_count == 2


def test_invalid_source_citation_retry_explains_rejection_without_loosening_the_gate(caplog):
    rows = [source(0, "妈妈我回来了"), source(1, "我饿了")]
    valid = turn_reading(rows, roles=())
    invalid = deepcopy(valid)
    invalid['address_context'][0]['turn_check']['evidence'][-1]['quote'] = '爸爸'
    client = Mock(translate=Mock(side_effect=[invalid, valid]))
    reading = AutomaticTranslationReviewer._address_reading(client, [rows[-1]], rows, lambda: None)
    assert reading[1]['uncertain'] is False
    second_prompt = client.translate.call_args_list[1].args[0]
    assert 'LƯỢT TRƯỚC KHÔNG QUA KIỂM TRA CẤU TRÚC' in second_prompt
    assert 'Dẫn chứng phân lượt không khớp lời nguồn.' in second_prompt
    assert 'Không đoán vai để sửa schema.' in second_prompt
    assert 'ADDRESS_CONTEXT_INVALID' in caplog.text


def address_reply(ids, rows):
    by_id = {row['id']: row for row in rows}
    return {'address_context': [{
        'id': sid, 'self_address': '', 'listener_address': '',
        'self_uncertain': True, 'listener_uncertain': True, 'uncertain': True,
        'reason': 'Chưa đủ căn cứ phân vai.',
        'evidence': [{'id': sid, 'quote': by_id[sid]['text_zh']}],
        'turn_check': {'ambiguous_roles': ['self', 'listener'],
            'reason': 'Còn nhiều cách phân lượt.',
            'evidence': [{'id': sid, 'quote': by_id[sid]['text_zh']}]},
    } for sid in ids]}


def address_prompt_ids(prompt):
    return json.loads(prompt.split('ID cần kiểm định: ', 1)[1].split('\n', 1)[0])


def test_address_subbatches_keep_identical_full_source_context_without_drafts():
    rows = [source(i, '我问你') for i in range(12)]
    context = [source(-8, '爸爸我回来了'), *rows, source(40, '你是我女儿')]
    requests = []
    def reply(prompt, **kwargs):
        requests.append((prompt, kwargs))
        return address_reply(address_prompt_ids(prompt), rows)
    client = Mock(translate=Mock(side_effect=reply))
    result = AutomaticTranslationReviewer._address_reading(client, rows, context, lambda: None)
    assert set(result) == set(range(12))
    assert [address_prompt_ids(prompt) for prompt, _ in requests] == [list(range(i, i + 4)) for i in (0, 4, 8)]
    sources = [json.loads(prompt.split('Nguồn thoại theo thời gian: ', 1)[1]) for prompt, _ in requests]
    assert sources[0] == sources[1] == sources[2]
    assert [row['id'] for row in sources[0]] == [-8, *range(12), 40]
    assert all('final_vi' not in row and 'literal_vi' not in row for row in sources[0])
    assert all(kwargs == {'max_tokens': 7000} for _, kwargs in requests)


def test_exhausted_subread_never_returns_partial_success_and_resume_reuses_only_valid_reads():
    rows = [source(i, '爸我问你') for i in range(9)]
    saved = {}
    checkpoint = Mock()
    checkpoint.load.side_effect = lambda stage: deepcopy(saved.get(json.dumps(stage, sort_keys=True)))
    checkpoint.store.side_effect = lambda stage, data: saved.update({json.dumps(stage, sort_keys=True): deepcopy(data)})
    client = Mock(translate=Mock(side_effect=[address_reply(range(4), rows), '{bad', '{bad']))
    with pytest.raises(VideoIntelligenceError):
        AutomaticTranslationReviewer._address_reading(client, rows, rows, lambda: None, checkpoint)
    assert client.translate.call_count == 3 and len(saved) == 1
    assert set(next(iter(saved.values()))['address_context'][i]['id'] for i in range(4)) == set(range(4))
    client.translate.side_effect = lambda prompt, **kwargs: address_reply(address_prompt_ids(prompt), rows)
    result = AutomaticTranslationReviewer._address_reading(client, rows, rows, lambda: None, checkpoint)
    assert set(result) == set(range(9)) and client.translate.call_count == 5
    assert len(saved) == 3


def test_address_syntax_retry_has_location_and_nesting_guidance_without_raw_response(caplog):
    rows = [source(0, '爸我问你')]
    client = Mock(translate=Mock(side_effect=['{"secret-do-not-log": "unterminated', address_reply([0], rows)]))
    result = AutomaticTranslationReviewer._address_reading(client, rows, rows, lambda: None)
    assert set(result) == {0}
    retry = client.translate.call_args_list[1].args[0]
    assert 'dòng 1, cột' in retry and 'BÊN TRONG đối tượng turn_check' in retry
    assert 'code=invalid_json' in caplog.text and 'line=1' in caplog.text
    assert 'secret-do-not-log' not in retry + caplog.text


def test_cancellation_between_address_subbatches_prevents_next_request():
    rows = [source(i, '爸我问你') for i in range(8)]
    cancelled = False
    def reply(prompt, **kwargs):
        nonlocal cancelled
        cancelled = True
        return address_reply(address_prompt_ids(prompt), rows)
    def check():
        if cancelled:
            raise RuntimeError('cancelled')
    client = Mock(translate=Mock(side_effect=reply))
    checkpoint = Mock(load=Mock(return_value=None))
    with pytest.raises(RuntimeError, match='cancelled'):
        AutomaticTranslationReviewer._address_reading(client, rows, rows, check, checkpoint)
    assert client.translate.call_count == 1 and not checkpoint.store.called
