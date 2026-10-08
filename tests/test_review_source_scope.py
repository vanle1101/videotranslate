"""Merged OCR tracks must not expand independently timed spoken rows."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from config import settings
from core.translation_review import AutomaticTranslationReviewer


def row(sid, start, end, text, vi="Bản nháp"):
    return {"id": sid, "start": start, "end": end, "asr_text": text,
        "text_zh": text, "literal_vi": vi, "natural_vi": vi, "final_vi": vi,
        "needs_review": True, "review_reason": "Cần kiểm tra nguồn"}


def review_row(monkeypatch, source, neighbors, text, proposal, final_vi, *,
               ocr_window=None, screen_window=None):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    evidence = {"start": min(item["start"] for item in [source, *neighbors]),
        "end": max(item["end"] for item in [source, *neighbors]),
        "text_zh": text, "confidence": .99, "bbox": [.1, .7, .8, .08]}
    if ocr_window:
        evidence.update(start=ocr_window[0], end=ocr_window[1])
    screen = {**evidence, "kind": "subtitle"}
    if screen_window:
        screen.update(start=screen_window[0], end=screen_window[1])
    client = Mock(has_credentials=True, model="offline-source-scope")
    client.translate.return_value = {"segments": [{**source, "text_zh": proposal,
        "literal_vi": final_vi, "natural_vi": final_vi, "final_vi": final_vi,
        "semantic_verified": True, "verification_reason": "Đã đối chiếu từng phần lời.",
        "needs_review": False, "review_reason": "", "source_evidence_ids": ["review0"]}],
        "screen_texts": [], "summary": ""}
    scanner = Mock()
    scanner.extract.return_value = [evidence]
    reviewer = AutomaticTranslationReviewer(client, scanner, audio_evidence=False)
    # Source ownership is orthogonal to role judgments, covered by the main
    # reviewer tests. Isolating it makes these tests independent of pronouns.
    reviewer._address_reading = Mock(return_value={})
    result = reviewer.review("unused", [source], [screen],
        context_segments=[source, *neighbors])
    return result["segments"][source["id"]], client


def test_long_ocr_must_not_import_future_name_and_age_into_one_second_row(monkeypatch):
    source = row(4, 7.68, 8.68, "我是你女儿", "Con là con gái của bố.")
    neighbors = [row(5, 8.68, 9.4, "小满"), row(6, 10.17, 11.01, "今年十九")]
    original = deepcopy(source)
    result, _ = review_row(monkeypatch, source, neighbors, "我是你女儿小满今年19",
        "我是你女儿小满今年19", "Con là con gái của bố, Tiểu Mãn, năm nay 19 tuổi.")
    assert result["text_zh"] == source["text_zh"]
    assert result["final_vi"] == source["final_vi"]
    assert result["needs_review"]
    assert result["verification"]["source_scope_conflict"]
    assert not result["verification"]["source_accepted"]
    assert not result["verification"]["source_supported"]
    assert not result["verification"]["semantic_verified"]
    assert "lặp thoại" in result["review_reason"]
    assert source == original


def test_spanning_subtitle_still_corroborates_correctly_scoped_short_source(monkeypatch):
    source = row(1, 1., 2., "明天去学校")
    neighbor = row(2, 2., 3., "找老师")
    result, client = review_row(monkeypatch, source, [neighbor], "明天去学校找老师",
        source["text_zh"], "Ngày mai đến trường.")
    assert not result["needs_review"]
    assert result["verification"]["source_supported"]
    assert result["verification"]["source_accepted"]
    assert result["verification"]["status"] == "corrected"
    assert result["verification"]["evidence"][0]["text_zh"] == source["text_zh"]
    assert result["verification"]["evidence"][0]["full_text_zh"] == "明天去学校找老师"
    assert result["verification"]["evidence"][0]["source_scope_ids"] == [1, 2]
    prompt = client.translate.call_args_list[0].args[0]
    assert "Không lặp nguyên dòng OCR ở mỗi ID" in prompt
    assert "asr_text" in prompt and "找老师" in prompt
    assert '"speech_scope_by_id": [{"id": 1, "text_zh": "明天去学校"' in prompt


def test_review_window_clipping_does_not_hide_original_spanning_track_ownership(monkeypatch):
    source = row(4, 7.68, 8.68, "我是你女儿", "Con là con gái của bố.")
    neighbors = [row(5, 8.68, 9.4, "小满"), row(6, 10.17, 11.01, "今年十九")]
    result, _ = review_row(monkeypatch, source, neighbors, "我是你女儿小满今年19",
        "我是你女儿小满今年19", "Con là con gái của bố, Tiểu Mãn, năm nay 19 tuổi.",
        ocr_window=(7.76, 8.68), screen_window=(8., 11.33))
    assert result["text_zh"] == source["text_zh"]
    assert result["final_vi"] == source["final_vi"]
    assert result["verification"]["source_scope_conflict"]
    proof = result["verification"]["evidence"][0]
    assert (proof["start"], proof["end"]) == (7.76, 8.68)
    assert proof["source_scope_window"] == {"start": 7.76, "end": 11.33}


def test_clipped_ocr_still_supports_the_measured_target_without_future_words(monkeypatch):
    source = row(1, 1., 2., "明天去学校")
    neighbor = row(2, 2., 3., "找老师")
    result, _ = review_row(monkeypatch, source, [neighbor], "明天去学校找老师",
        "明天去学校", "Ngày mai đến trường.", ocr_window=(1.1, 2.), screen_window=(1.2, 3.))
    assert result["verification"]["source_supported"]
    assert not result["needs_review"]
    assert result["verification"]["evidence"][0]["end"] == 2.


def test_spanning_subtitle_must_not_import_a_previous_row(monkeypatch):
    previous = row(1, 1., 2., "明天去学校")
    source = row(2, 2., 3., "找老师", "Tìm giáo viên.")
    result, _ = review_row(monkeypatch, source, [previous], "明天去学校找老师",
        "明天去学校找老师", "Ngày mai đến trường tìm giáo viên.")
    assert result["text_zh"] == "找老师"
    assert result["final_vi"] == "Tìm giáo viên."
    assert result["verification"]["source_scope_conflict"]


def test_typo_inside_target_can_be_corrected_without_importing_the_next_row(monkeypatch):
    source = row(1, 1., 2., "明天去学佼")
    neighbor = row(2, 2., 3., "找老师")
    result, _ = review_row(monkeypatch, source, [neighbor], "明天去学校找老师",
        "明天去学校", "Ngày mai đến trường.")
    assert result["text_zh"] == "明天去学校"
    assert result["verification"]["source_supported"]
    assert not result["verification"]["source_scope_conflict"]
    assert not result["needs_review"]


@pytest.mark.parametrize("proposal, supported", [("支持", False), ("不支持", True)])
def test_scope_alignment_keeps_negation_in_the_measured_target(monkeypatch, proposal, supported):
    source = row(1, 1., 2., "支持")
    neighbor = row(2, 2., 3., "这项计划")
    result, _ = review_row(monkeypatch, source, [neighbor], "不支持这项计划",
        proposal, "Không ủng hộ." if supported else "Ủng hộ.")
    assert result["verification"]["source_supported"] is supported
    assert result["needs_review"] is not supported


def test_replacement_across_a_row_boundary_is_not_proof_of_new_source(monkeypatch):
    source = row(1, 1., 2., "明天去学校")
    neighbor = row(2, 2., 3., "找老师")
    evidence = {"start": 1., "end": 3., "text_zh": "明天去找老师",
        "confidence": .99, "bbox": [.1, .7, .8, .08]}
    scoped = AutomaticTranslationReviewer._scope_evidence(evidence, source, [source, neighbor])
    # The deleted school word stays in this row, but the teacher phrase still
    # begins exactly at the neighbour boundary; it cannot be taken by row 1.
    assert scoped["text_zh"] == "明天去"
    result, _ = review_row(monkeypatch, source, [neighbor], evidence["text_zh"],
        "明天去找老师", "Ngày mai đi tìm giáo viên.")
    assert result["text_zh"] == source["text_zh"]
    assert result["verification"]["source_scope_conflict"]


def test_temporally_unrelated_context_does_not_partition_ocr():
    source = row(1, 1., 2., "学校")
    neighbor = row(2, 10., 11., "老师")
    evidence = {"start": 1., "end": 2., "text_zh": "学校老师"}
    assert AutomaticTranslationReviewer._scope_evidence(evidence, source, [neighbor]) == evidence


def test_unowned_insertion_between_two_rows_stays_ambiguous(monkeypatch, caplog):
    source = row(1, 1., 2., "明天去学校")
    neighbor = row(2, 2., 3., "找老师")
    evidence = {"start": 1., "end": 3., "text_zh": "明天去学校然后找老师"}
    scoped = AutomaticTranslationReviewer._scope_evidence(evidence, source, [source, neighbor])
    assert scoped["source_scope_ambiguous"] and scoped["text_zh"] == ""
    result, _ = review_row(monkeypatch, source, [neighbor], evidence["text_zh"],
        evidence["text_zh"], "Ngày mai đến trường rồi tìm giáo viên.")
    assert result["text_zh"] == source["text_zh"]
    assert result["verification"]["source_scope_conflict"]
    assert "REVIEW_SOURCE_SCOPE_REJECTED" in caplog.text
    assert "segment_id=1" in caplog.text
    assert evidence["text_zh"] not in caplog.text


def test_immutable_asr_owns_scope_even_when_draft_was_already_expanded():
    source = {**row(1, 1., 2., "明天去学校"), "text_zh": "明天去学校找老师"}
    neighbor = row(2, 2., 3., "找老师")
    evidence = {"start": 1., "end": 3., "text_zh": "明天去学校找老师"}
    scoped = AutomaticTranslationReviewer._scope_evidence(evidence, source, [source, neighbor])
    assert scoped["text_zh"] == "明天去学校"


def test_audio_fallback_keeps_wider_context_from_bounded_review(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    source = row(4, 7., 8., "你好", "Xin chào.")
    prior = row(1, 1., 2., "妈妈")
    later = row(5, 8., 9., "下一句")
    client = Mock(has_credentials=True, model="offline-source-scope")
    client.translate.return_value = {"segments": [{**source, "semantic_verified": True,
        "verification_reason": "Lời chào đầy đủ.", "needs_review": False,
        "review_reason": "", "source_evidence_ids": []}], "screen_texts": [], "summary": ""}
    scanner = Mock()
    scanner.extract.return_value = []
    audio = Mock()
    audio.last_error = None
    audio.collect.return_value = {4: {"sensevoice": "你好", "faster-whisper-small": "你好"}}
    reviewer = AutomaticTranslationReviewer(client, scanner, audio)
    reviewer._address_reading = Mock(return_value={})
    result = reviewer.review("unused", [source], [], context_segments=[prior, source, later])
    assert set(result["segments"]) == {4}
    assert result["segments"][4]["verification"]["source_supported"]
    assert reviewer._address_reading.call_count == 2
    for call in reviewer._address_reading.call_args_list:
        assert {item["id"] for item in call.args[2]} == {1, 4, 5}
    audio_prompt = client.translate.call_args_list[2].args[0]
    assert "妈妈" in audio_prompt and "下一句" in audio_prompt
