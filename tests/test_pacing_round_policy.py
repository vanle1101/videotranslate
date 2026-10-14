"""Offline policy regressions; synthetic PCM is never real-provider evidence."""
import json
from unittest.mock import Mock

import pytest

from test_natural_speech import (RecordedSynthesizer, aligner, verified_candidate,
                                rewrite_response, reject_network)
from core.engines.alignment.natural_speech import synthesize_natural_speech
from core.engines.alignment.timing_aligner import SpeechBudgetError
from core.engines.translation.semantic_translator import SemanticTranslator, PacingReviewRejected


def test_fifth_distinct_proposal_can_fit_and_source_hint_reaches_rewriter(tmp_path, aligner):
    original = "Tôi vẫn chưa làm việc đó."
    candidates = [f"Tôi chưa làm việc {i}." for i in range(5)]
    engine = RecordedSynthesizer({original: 2., **{t: 1.8 for t in candidates[:-1]}, candidates[-1]: .7})
    translator = Mock(rewrite_for_pacing=Mock(side_effect=[verified_candidate(t) for t in candidates]))
    result = synthesize_natural_speech(text=original, source="아직 안 했어요", duration=1,
        output_path=tmp_path / "out.wav", engine=engine, aligner=aligner,
        translator=translator, source_language="ko")
    assert result["text"] == candidates[-1]
    assert engine.calls == [original, *candidates]
    assert translator.rewrite_for_pacing.call_count == 5
    assert all(c.kwargs["source_language"] == "ko" for c in translator.rewrite_for_pacing.call_args_list)
    assert result["speed_ratio"] <= 1.15
    assert aligner.get_audio_duration(tmp_path / "out.wav") <= 1.0001


def test_round_budget_is_shared_by_rejected_and_measured_candidates(tmp_path, aligner):
    original = "Tôi vẫn chưa làm việc đó."
    rejected = PacingReviewRejected("Rejected", candidate="Sai nghĩa.", code="semantic_mismatch")
    translator = Mock(rewrite_for_pacing=Mock(side_effect=[rejected,
        *[verified_candidate(f"Câu số {i}.") for i in range(4)]]))
    engine = RecordedSynthesizer(2.)
    out = tmp_path / "out.wav"
    out.write_bytes(b"previous complete output")
    with pytest.raises(SpeechBudgetError):
        synthesize_natural_speech(text=original, source="原文", duration=1, output_path=out,
            engine=engine, aligner=aligner, translator=translator)
    assert translator.rewrite_for_pacing.call_count == 5
    assert len(engine.calls) == 5  # Original plus four independently approved proposals.
    assert out.read_bytes() == b"previous complete output"


def test_duplicate_replies_do_not_create_an_unbounded_round(tmp_path, aligner):
    original = "Giữ đúng câu này."
    translator = Mock(rewrite_for_pacing=Mock(return_value=verified_candidate(original)))
    engine = RecordedSynthesizer(2.)
    with pytest.raises(PacingReviewRejected) as caught:
        synthesize_natural_speech(text=original, source="原文", duration=1,
            output_path=tmp_path / "out.wav", engine=engine, aligner=aligner, translator=translator)
    assert caught.value.code == "duplicate"
    assert translator.rewrite_for_pacing.call_count == 5
    assert engine.calls == [original]


@pytest.mark.parametrize("language,source", [("ko", "아직 안 했어요"), ("en", "I have not done it yet."),
    ("auto", "아직 안 했어요. Not yet."), ("th", "ยังไม่ได้ทำ")])
def test_source_language_policy_reaches_both_semantic_passes(language, source, monkeypatch):
    request = Mock(side_effect=[rewrite_response("Chưa làm."),
        json.dumps({"equivalent": True, "natural": True, "address_preserved": True, "reason": "Giữ nghĩa."}),
        json.dumps({"natural": True, "reason": "Văn nói."})])
    monkeypatch.setattr(SemanticTranslator, "_opencode_request", request)
    result = SemanticTranslator("opencode").rewrite_for_pacing(
        source, "Vẫn chưa làm việc đó.", 1., source_language=language)
    assert result["pacing_verification"]["status"] == "verified"
    for call in request.call_args_list[:2]:
        assert "không mặc định tiếng Trung" in call.args[0]
        assert source in call.args[1]
        assert language in call.args[1]
    assert source not in request.call_args_list[2].args[1]  # Blind Vietnamese gate stays blind.


def _review_answers(*, equivalent=True, natural=True, address=True):
    return [rewrite_response("Tôi chưa làm.", needs_review=True, reason="Chưa chắc cách gọi."),
        json.dumps({"equivalent": equivalent, "natural": natural, "address_preserved": address,
                    "reason": "Đánh giá độc lập."}),
        json.dumps({"natural": True, "reason": "Câu tự nhiên."})]


def test_only_uncertainty_can_be_explicit_draft_after_independent_gates(tmp_path, aligner, monkeypatch):
    request = Mock(side_effect=_review_answers())
    monkeypatch.setattr(SemanticTranslator, "_opencode_request", request)
    engine = RecordedSynthesizer({"Tôi vẫn chưa làm việc đó.": 2., "Tôi chưa làm.": .7})
    result = synthesize_natural_speech(text="Tôi vẫn chưa làm việc đó.", source="I have not done it yet.",
        duration=1, output_path=tmp_path / "out.wav", engine=engine, aligner=aligner,
        translator=SemanticTranslator("opencode"), source_language="en", allow_review_draft=True)
    assert result["pacing_verification"]["status"] == "draft"
    assert result["pacing_verification"]["semantic_verified"] is True
    assert result["needs_review"] is True
    assert result["review_reason"] == "Chưa chắc cách gọi."
    assert request.call_count == 3
    assert aligner.get_audio_duration(tmp_path / "out.wav") <= 1.0001


@pytest.mark.parametrize("verdict", [{"equivalent": False}, {"natural": False}, {"address": False}])
def test_draft_opt_in_never_accepts_negative_fidelity(verdict, monkeypatch):
    request = Mock(side_effect=_review_answers(**verdict))
    monkeypatch.setattr(SemanticTranslator, "_opencode_request", request)
    with pytest.raises(PacingReviewRejected):
        SemanticTranslator("opencode").rewrite_for_pacing("我还没做。", "Tôi vẫn chưa làm việc đó.",
            1., allow_review_draft=True)
    assert request.call_count == 2


def test_draft_still_cannot_publish_audio_that_exceeds_real_slot(tmp_path, aligner):
    original, candidate = "Câu ban đầu quá dài.", "Câu ngắn hơn."
    proof = {"status": "draft", "text": candidate, "semantic_verified": True,
             "review_required": True, "review_reason": "Chưa chắc ngữ cảnh."}
    translator = Mock(rewrite_for_pacing=Mock(return_value={
        "final_vi": candidate, "pacing_verification": proof}))
    out = tmp_path / "out.wav"
    out.write_bytes(b"retained valid output")
    engine = RecordedSynthesizer(2.)
    with pytest.raises(SpeechBudgetError) as caught:
        synthesize_natural_speech(text=original, source="Original speech.", duration=1,
            output_path=out, engine=engine, aligner=aligner, translator=translator,
            max_pacing_proposals=1, allow_review_draft=True)
    assert caught.value.pacing_proposals_used == 1
    assert out.read_bytes() == b"retained valid output"
    assert engine.calls == [original, candidate]
