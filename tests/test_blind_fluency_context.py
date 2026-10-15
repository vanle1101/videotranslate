"""Regressions for the reproduced blind-review context contamination."""
import json

from core.engines.translation.semantic_translator import (
    SemanticTranslator, fluency_dialogue_context,
)


def test_blind_context_excludes_focus_draft_and_selects_nearest_turns():
    rows = [{"id": i, "vi": f"Lời thoại {i}.", "text_zh": "秘密"} for i in range(24)]
    rows[6].update(is_focus=True, vi="Bản nháp cũ sai ngữ pháp.")
    result = fluency_dialogue_context(rows, target={"id": 6},
        draft=rows[6]["vi"], candidate="Bản sửa tự nhiên.")
    assert result == [f"Lời thoại {i}." for i in (3, 4, 5, 7, 8, 9)]
    assert "秘密" not in json.dumps(result, ensure_ascii=False)
    assert rows[6]["vi"] not in result


def test_legacy_context_excludes_identical_old_draft_and_untranslated_source():
    result = fluency_dialogue_context([
        {"vi": "Câu trước."}, {"vi": "Bản nháp cũ."},
        {"vi": "仍未翻译"}, {"final_vi": "Câu sau."},
    ], target={}, draft="Bản nháp cũ.", candidate="Lời sửa.")
    assert result == ["Câu trước.", "Câu sau."]


def test_focus_without_stable_id_does_not_exclude_every_legacy_row():
    result = fluency_dialogue_context([
        {"vi": "Câu trước."}, {"vi": "Bản nháp cũ."}, {"vi": "Câu sau."},
    ], target={"start": 1.0, "end": 2.0}, draft="Bản nháp cũ.", candidate="Lời sửa.")
    assert result == ["Câu trước.", "Câu sau."]


def test_actual_blind_request_contains_no_focus_draft_or_chinese(monkeypatch):
    translator = SemanticTranslator(provider="opencode")
    requests = []
    replies = iter([
        '{"literal_vi":"Lời mới.","natural_vi":"Lời mới.","final_vi":"Lời mới."}',
        '{"equivalent":true,"natural":true,"address_preserved":true,"reason":"Giữ đủ nghĩa."}',
        '{"natural":true,"reason":"Câu Việt có cấu trúc rõ."}',
    ])
    def request(system, prompt):
        requests.append((system, prompt))
        return next(replies)
    monkeypatch.setattr(translator, "_opencode_request", request)
    result = translator.rewrite_for_pacing("全新对话", "Bản nháp cũ.", .8, [
        {"id": 1, "vi": "Câu trước.", "text_zh": "之前"},
        {"id": 2, "is_focus": True, "vi": "Bản nháp cũ.", "text_zh": "全新对话"},
        {"id": 3, "vi": "Câu sau.", "text_zh": "之后"},
    ])
    blind = json.loads(requests[-1][1])
    assert blind["candidate"] == "Lời mới."
    context = blind["nearby_vietnamese_dialogue"]
    assert context["before"] == [{"id": 1, "status": "available", "text": "Câu trước.", "adjacent_in_supplied_context": True}]
    assert context["after"] == [{"id": 3, "status": "available", "text": "Câu sau.", "adjacent_in_supplied_context": True}]
    assert context["order"] == "supplied_unverified"
    assert result["pacing_verification"]["fluency"]["natural"] is True


def test_positioned_blind_context_retains_real_repeated_neighbor_without_focus_draft():
    rows = [
        {"id": 1, "vi": "Không nói đâu."},
        {"id": 2, "is_focus": True, "vi": "Bản cũ."},
        {"id": 3, "vi": "Không nói đâu."},
    ]
    result = fluency_dialogue_context(rows, target={"id": 2}, draft="Bản cũ.",
                                      candidate="Không nói đâu.", positioned=True)
    assert result == {"before": ["Không nói đâu."], "after": ["Không nói đâu."]}


def test_unknown_focus_position_does_not_label_legacy_turns_as_preceding():
    result = fluency_dialogue_context([{"vi": "Câu chưa rõ vị trí."}], target={},
        draft="Bản cũ.", candidate="Vậy", positioned=True)
    assert result == {"before": [], "after": [], "unpositioned": ["Câu chưa rõ vị trí."]}


def test_connector_review_receives_exact_position_and_still_requires_all_three_real_verdicts(monkeypatch):
    from unittest.mock import Mock
    translator = SemanticTranslator(provider="opencode")
    requests = Mock(side_effect=[
        '{"literal_vi":"Vậy","natural_vi":"Vậy","final_vi":"Vậy"}',
        '{"equivalent":true,"natural":true,"address_preserved":true,"reason":"Giữ quan hệ kết quả."}',
        '{"natural":true,"reason":"Từ nối nối vào mệnh đề liền sau."}',
    ])
    monkeypatch.setattr(translator, "_opencode_request", requests)
    translator.rewrite_for_pacing("所以", "Nên...", .62, [
        {"id": 151, "text_zh": "我十九", "vi": "Mười chín tuổi."},
        {"id": 152, "text_zh": "所以", "vi": "Nên...", "is_focus": True},
        {"id": 153, "text_zh": "你十八", "vi": "Bố mười tám tuổi."},
    ])
    assert requests.call_count == 3
    blind = json.loads(requests.call_args_list[-1].args[1])
    assert [entry["text"] for entry in blind["nearby_vietnamese_dialogue"]["before"]] == ["Mười chín tuổi."]
    assert [entry["text"] for entry in blind["nearby_vietnamese_dialogue"]["after"]] == ["Bố mười tám tuổi."]
    assert '每' not in json.dumps(blind) and 'Nên...' not in json.dumps(blind, ensure_ascii=False)
    assert 'không tự thêm/bỏ ý hay nối hai người nói' in requests.call_args_list[1].args[0]
    assert 'unpositioned không chứng minh' in requests.call_args_list[-1].args[0]


def enriched(rows, target=3):
    return fluency_dialogue_context(rows, target={"id": target}, draft="Bản cũ.",
                                    candidate="Ở sau.", enriched=True)


def test_missing_neighbors_remain_between_distant_available_translations():
    result = enriched([
        {"id": 1, "start": 0., "end": 1., "final_vi": "Cái chìa khóa ấy."},
        {"id": 2, "start": 80., "end": 81., "final_vi": ""},
        {"id": 3, "start": 82., "end": 83., "final_vi": "Bản cũ.", "is_focus": True},
        {"id": 4, "start": 84., "end": 85., "final_vi": "汉字"},
        {"id": 5, "start": 200., "end": 201., "final_vi": "Mình đi nhé."},
    ])
    assert result["order"] == "timestamp"
    assert result["before"][0]["gap_seconds_to_target"] == 81.
    assert result["before"][0]["adjacent_in_supplied_context"] is False
    assert result["before"][1]["status"] == "missing_translation"
    assert result["after"][0]["status"] == "invalid_translation"
    assert result["after"][1]["gap_seconds_to_target"] == 117.
    assert "汉字" not in json.dumps(result, ensure_ascii=False)
    assert "Bản cũ." not in json.dumps(result, ensure_ascii=False)


def test_timestamp_order_is_normalized_and_empty_alias_uses_translation():
    result = enriched([
        {"id": 4, "start": 4., "end": 5., "vi": "Sau."},
        {"id": 3, "start": 2., "end": 3., "vi": "Bản cũ."},
        {"id": 2, "start": 0., "end": 1., "vi": "  ", "final_vi": "Chìa khóa đâu?"},
    ])
    assert result["before"][0]["id"] == 2
    assert result["before"][0]["text"] == "Chìa khóa đâu?"
    assert result["after"][0]["id"] == 4
    assert result["target_interval"] == {"start": 2., "end": 3.}


def test_invalid_rows_and_unknown_focus_do_not_gain_fake_order_or_text():
    result = enriched([None, {"id": "秘密", "vi": "Bản cũ."}, {"id": 8, "vi": 42}], target=99)
    assert result["order"] == "supplied_unverified"
    assert [entry["status"] for entry in result["unpositioned"]] == [
        "invalid_translation", "focus_text_excluded", "invalid_translation"]
    assert result["before"] == result["after"] == []
    assert "秘密" not in json.dumps(result, ensure_ascii=False)


def test_canonical_translation_and_boolean_uncertainty_survive_without_reasons():
    result = enriched([
        {"id": 2, "vi": "Lời cũ.", "final_vi": "Lời hiện tại.", "translation_is_draft": True,
         "needs_review": True, "review_reason": "秘密", "verification": {"reason": "秘密"}},
        {"id": 3, "is_focus": True, "final_vi": "Bản cũ."},
        {"id": 4, "vi": "Lời sau.", "translation_is_draft": "false", "needs_review": False},
    ])
    before = result["before"][0]
    assert before["text"] == "Lời hiện tại."
    assert before["translation_is_draft"] is True and before["needs_review"] is True
    assert result["after"][0]["needs_review"] is False
    assert "translation_is_draft" not in result["after"][0]
    assert "秘密" not in json.dumps(result, ensure_ascii=False)
    assert fluency_dialogue_context([
        {"vi": "Lời cũ.", "final_vi": "Lời hiện tại."}], target={}, draft="", candidate="") == ["Lời hiện tại."]


def test_enriched_payload_is_bounded_and_does_not_leak_focus_or_source_metadata():
    rows = [{"id": i, "vi": "A" * 600, "text_zh": "秘密", "verification": {"reason": "秘密"}}
            for i in range(100)]
    rows[50].update(is_focus=True, vi="Bản cũ.")
    result = enriched(rows, target=50)
    entries = result["before"] + result["after"]
    assert len(entries) == 6 and all(len(entry["text"]) == 500 and entry["truncated"] for entry in entries)
    assert "秘密" not in json.dumps(result, ensure_ascii=False)
    assert "Bản cũ." not in json.dumps(result, ensure_ascii=False)
