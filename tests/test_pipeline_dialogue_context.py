"""Regression tests for source context passed through the streaming pipeline."""

from types import SimpleNamespace

from core.streaming.pipeline import StreamingPipelineSession
from core.translation_context import dialogue_context


def _segment(seg_id, start, source, final_vi=""):
    return SimpleNamespace(
        id=seg_id,
        start=float(start),
        end=float(start + 1),
        text_zh=source,
        asr_text=source,
        final_vi=final_vi,
        needs_review=False,
    )


def _session(*segments):
    session = StreamingPipelineSession.__new__(StreamingPipelineSession)
    session.segments = {item.id: item for item in segments}
    return session


def test_context_keeps_current_and_later_source_turns_with_stable_metadata():
    first = _segment(0, 0, "拜托姐", "Thôi mà chị.")
    current = _segment(1, 1, "这话应该我来问吧", "Câu này phải để chị hỏi chứ.")
    later = _segment(2, 2, "你快说", "Em nói đi.")

    context = _session(first, current, later)._dialogue_context_before(current)

    assert [row["id"] for row in context] == [0, 1, 2]
    assert context[0]["zh"] == context[0]["text_zh"] == "拜托姐"
    assert context[1]["vi"] == context[1]["final_vi"] == "Câu này phải để chị hỏi chứ."
    assert context[2]["start"] == 2.0 and context[2]["end"] == 3.0


def test_review_context_replaces_focused_source_and_translation_before_tts():
    current = _segment(4, 4, "旧的识别", "Bản dịch cũ")
    later = _segment(5, 5, "后面的句子", "Câu sau")
    context = _session(current, later)._dialogue_context_before(
        current, focus_source="重新识别的句子", focus_vi="Bản dịch đã rà"
    )

    focused = next(row for row in context if row["id"] == current.id)
    assert focused["text_zh"] == focused["zh"] == "重新识别的句子"
    assert focused["final_vi"] == focused["vi"] == "Bản dịch đã rà"
    assert "旧的识别" not in str(focused)


def test_context_focus_does_not_drift_to_final_turn_when_later_sources_are_known():
    segments = [_segment(i, i, "普通句子" * 40, "Bản dịch nháp.") for i in range(200)]
    segments[1].text_zh = "拜托姐"
    current = segments[0]
    context = _session(*segments)._dialogue_context_before(current)

    assert len(context) <= 64
    assert {0, 1, 2}.issubset({row["id"] for row in context})
    assert context[0]["text_zh"] == current.text_zh
    # Formatting a context is another selector pass in the provider adapter.
    normalized_again = dialogue_context(context)
    assert {row["id"] for row in context} == {row["id"] for row in normalized_again}


def test_asr_only_future_source_stays_marked_uncertain():
    current = _segment(0, 0, "这话应该我来问吧")
    later = _segment(1, 1, "")
    later.asr_text = "拜托姐"
    context = _session(current, later)._dialogue_context_before(current)

    assert context[1]["text_zh"] == "拜托姐"
    assert context[1]["source_needs_review"] is True


def test_review_snapshot_keeps_session_timeline_and_does_not_publish_future_source():
    current = _segment(0, 0, "这话应该我来问吧", "Bản cũ")
    later = _segment(1, 1, "错误识别", "Câu sau cũ")
    snapshot = {0: {"id": 0, "text_zh": current.text_zh, "final_vi": "Bản rà"},
                1: {"id": 1, "start": 90, "end": 100, "text_zh": "姐你听我说",
                    "final_vi": "Chị nghe em nói này.", "source_needs_review": True},
                9: {"id": 9, "text_zh": "额外的句子", "final_vi": "Không có trong timeline"}}

    context = _session(current, later)._dialogue_context_before(current, _review_context=snapshot)

    assert [row["id"] for row in context] == [0, 1]
    assert context[1]["text_zh"] == "姐你听我说"
    assert context[1]["final_vi"] == "Chị nghe em nói này."
    assert (context[1]["start"], context[1]["end"]) == (1.0, 2.0)
    assert context[1]["source_needs_review"] is True
    assert context[1]["translation_is_draft"] is True
    assert later.text_zh == "错误识别" and later.final_vi == "Câu sau cũ"
    assert snapshot[1]["start"] == 90


def test_dialogue_context_preserves_source_risk_flags_when_normalized_twice():
    rows = [{
        "id": 0,
        "start": 0.0,
        "end": 1.0,
        "text_zh": "源" * 1600,
        "source_needs_review": True,
        "source_truncated": True,
        "translation_is_draft": True,
    }]

    first = dialogue_context(rows)
    second = dialogue_context(first)

    for result in (first, second):
        assert result[0]["source_truncated"] is True
        assert result[0]["source_needs_review"] is True
        assert result[0]["translation_is_draft"] is True
