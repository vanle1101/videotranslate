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
    assert blind == {"candidate": "Lời mới.",
                     "nearby_vietnamese_dialogue": {"before": ["Câu trước."], "after": ["Câu sau."]}}
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
    assert blind["nearby_vietnamese_dialogue"] == {"before": ["Mười chín tuổi."], "after": ["Bố mười tám tuổi."]}
    assert '每' not in json.dumps(blind) and 'Nên...' not in json.dumps(blind, ensure_ascii=False)
    assert 'không tự thêm/bỏ ý hay nối hai người nói' in requests.call_args_list[1].args[0]
    assert 'unpositioned không chứng minh' in requests.call_args_list[-1].args[0]
