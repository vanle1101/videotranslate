"""Fresh gates and real PCM measurements for historical pacing; no live providers."""
import json
from unittest.mock import Mock

import pytest

from test_pacing_checkpoint import durable, options
from test_natural_speech import RecordedSynthesizer, aligner, verified_candidate, reject_network
from test_natural_speech import rewrite_response
from core.engines.alignment import pacing_checkpoint as cache
from core.engines.alignment.natural_speech import synthesize_natural_speech
from core.engines.alignment.timing_aligner import SpeechBudgetError
from core.engines.translation.semantic_translator import SemanticTranslator, PacingReviewRejected


class CapableAdapter:
    supports_pacing_revalidation = True

    def __init__(self):
        self.validate = Mock(side_effect=lambda source, current, text, context, **kw: verified_candidate(text))
        self.rewrite_for_pacing = Mock(return_value=verified_candidate("Mới."))

    def revalidate_pacing_candidate(self, *args, **kwargs):
        return self.validate(*args, **kwargs)


def seed(args, rows=None, feedback=None):
    identity_args = {k: v for k, v in args.items() if k != "output_path"}
    identity_args.update(ref_audio=None, max_duration=args["duration"], max_duration_limit=None,
        allow_bidirectional_reflow=False, source_language=args.get("source_language"),
        max_pacing_proposals=args.get("max_pacing_proposals", 5),
        allow_review_draft=args.get("allow_review_draft", False))
    rows = rows or [("Cũ ngắn.", .2), ("Cũ dài.", 1.8)]
    cache.store(cache.identity(**identity_args), [dict(text=text, measured_seconds=seconds,
        pacing_verification=verified_candidate(text)["pacing_verification"]) for text, seconds in rows], feedback or {})


def test_shortest_historical_wording_requires_fresh_gates_and_current_pcm(durable, aligner):
    adapter = CapableAdapter()
    engine = RecordedSynthesizer({"Lời ban đầu.": 2., "Cũ ngắn.": .7})
    args = options(durable, aligner, adapter, engine)
    seed(args)
    result = synthesize_natural_speech(**args)
    assert engine.calls == ["Lời ban đầu.", "Cũ ngắn."]
    adapter.validate.assert_called_once()
    assert adapter.validate.call_args.args == (args["source"], args["text"], "Cũ ngắn.", args["context"])
    adapter.rewrite_for_pacing.assert_not_called()
    assert result["pacing_proposals_used"] == 1
    assert result["tts_duration"] == pytest.approx(.7, abs=.01)
    assert aligner.get_audio_duration(args["output_path"]) <= 1.0001


def test_fresh_rejection_does_not_synthesize_old_proof(durable, aligner):
    adapter = CapableAdapter()
    adapter.validate.side_effect = PacingReviewRejected("Fresh rejection", candidate="Cũ ngắn.", code="semantic_mismatch")
    engine = RecordedSynthesizer({"Lời ban đầu.": 2., "Mới.": .7})
    args = options(durable, aligner, adapter, engine)
    seed(args)
    result = synthesize_natural_speech(**args)
    assert engine.calls == ["Lời ban đầu.", "Mới."]
    assert result["pacing_proposals_used"] == 2
    adapter.validate.assert_called_once()
    assert adapter.rewrite_for_pacing.call_args.kwargs["feedback"]["rejected_candidates"][0]["code"] == "semantic_mismatch"


def test_changed_context_never_revalidates_old_identity(durable, aligner):
    adapter = CapableAdapter()
    args = options(durable, aligner, adapter, RecordedSynthesizer({"Lời ban đầu.": 2., "Mới.": .7}))
    seed(args)
    args["context"] = [{"id": 1, "text_zh": "Different context"}]
    assert synthesize_natural_speech(**args)["text"] == "Mới."
    adapter.validate.assert_not_called()


def test_history_attempt_shares_five_budget_and_failure_uses_fresh_duration(durable, aligner):
    adapter = CapableAdapter()
    adapter.rewrite_for_pacing.side_effect = [verified_candidate(f"Mới {i}.") for i in range(4)]
    engine = RecordedSynthesizer({"Lời ban đầu.": 2., "Cũ ngắn.": 1.5,
        **{f"Mới {i}.": 1.8 for i in range(4)}})
    args = options(durable, aligner, adapter, engine)
    seed(args)
    with pytest.raises(SpeechBudgetError) as caught:
        synthesize_natural_speech(**args)
    adapter.validate.assert_called_once()
    assert adapter.rewrite_for_pacing.call_count == 4
    assert len(engine.calls) == 6
    assert "Cũ dài." not in engine.calls
    error = caught.value
    assert error.pacing_proposals_used == 5
    assert error.candidate_text == "Cũ ngắn."
    assert error.measured_candidate["measured_seconds"] == pytest.approx(1.5, abs=.01)
    assert error.required_dub_duration == pytest.approx(1.5 / 1.15 + .012, abs=.01)
    assert not args["output_path"].exists()


def test_single_slot_rejection_never_allocates_extra_proposal(durable, aligner):
    adapter = CapableAdapter()
    adapter.validate.side_effect = PacingReviewRejected("Fresh rejection", candidate="Cũ ngắn.", code="unnatural")
    args = options(durable, aligner, adapter, RecordedSynthesizer(2.))
    args["max_pacing_proposals"] = 1
    seed(args)
    with pytest.raises(PacingReviewRejected) as caught:
        synthesize_natural_speech(**args)
    assert caught.value.pacing_proposals_used == 1
    adapter.validate.assert_called_once()
    adapter.rewrite_for_pacing.assert_not_called()


def test_substantive_rejection_excludes_candidate_but_duplicate_does_not(durable, aligner):
    adapter = CapableAdapter()
    engine = RecordedSynthesizer({"Lời ban đầu.": 2., "Cũ dài.": .7})
    args = options(durable, aligner, adapter, engine)
    seed(args, rows=[("Cũ ngắn.", .2), ("Cũ dài.", .8)], feedback={"rejected_candidates": [
        {"text": "Cũ ngắn.", "code": "semantic_mismatch", "reason": "Missing meaning"},
        {"text": "Cũ dài.", "code": "duplicate", "reason": "Repeated"}]})
    assert synthesize_natural_speech(**args)["text"] == "Cũ dài."
    assert engine.calls == ["Lời ban đầu.", "Cũ dài."]


def test_exact_scope_overlong_history_does_not_spend_revalidation_or_tts(durable, aligner):
    adapter = CapableAdapter()
    engine = RecordedSynthesizer({"Lời ban đầu.": 2., "Mới.": .7})
    args = options(durable, aligner, adapter, engine)
    seed(args, rows=[("Đã đo quá dài.", 1.51)])
    result = synthesize_natural_speech(**args)
    adapter.validate.assert_not_called()
    assert engine.calls == ["Lời ban đầu.", "Mới."]
    assert result["pacing_proposals_used"] == 1
    assert adapter.rewrite_for_pacing.call_args.kwargs["feedback"]["measured_candidates"][0] == {
        "text": "Đã đo quá dài.", "measured_seconds": 1.51}


def test_fitting_original_does_not_request_revalidation(durable, aligner):
    adapter = CapableAdapter()
    args = options(durable, aligner, adapter, RecordedSynthesizer(.7))
    seed(args)
    synthesize_natural_speech(**args)
    adapter.validate.assert_not_called()
    adapter.rewrite_for_pacing.assert_not_called()


def test_exact_historical_validation_uses_current_source_and_blind_fluency(monkeypatch):
    request = Mock(side_effect=[
        json.dumps(dict(equivalent=True, natural=True, address_preserved=True, reason="Fresh fidelity")),
        json.dumps(dict(natural=True, reason="Fresh fluency"))])
    monkeypatch.setattr(SemanticTranslator, "_opencode_request", request)
    context = [{"id": 1, "text_zh": "A new context", "text_vi": "Ngữ cảnh mới."}]
    result = SemanticTranslator("opencode").revalidate_pacing_candidate(
        "I have not done it yet.", "Vẫn chưa làm việc đó.", "Chưa làm.", context, source_language="en")
    assert request.call_count == 2  # No new draft generation, both fresh gates.
    fidelity = json.loads(request.call_args_list[0].args[1])
    assert fidelity["source"] == "I have not done it yet."
    assert fidelity["source_language_hint"] == "en"
    assert "A new context" in request.call_args_list[0].args[1]
    assert fidelity["candidate"] == "Chưa làm."
    assert "I have not done it yet." not in request.call_args_list[1].args[1]
    assert result["pacing_verification"]["reason"] == "Fresh fidelity"
    assert result["pacing_verification"]["fluency"]["reason"] == "Fresh fluency"


@pytest.mark.parametrize("fidelity,fluency", [(False, True), (True, False)])
def test_old_wording_rejected_by_either_current_gate(monkeypatch, fidelity, fluency):
    request = Mock(side_effect=[
        json.dumps(dict(equivalent=fidelity, natural=True, address_preserved=True, reason="Current fidelity")),
        json.dumps(dict(natural=fluency, reason="Current fluency"))])
    monkeypatch.setattr(SemanticTranslator, "_opencode_request", request)
    with pytest.raises(PacingReviewRejected):
        SemanticTranslator("opencode").revalidate_pacing_candidate("Source", "Lời gốc.", "Lời cũ.")
    assert request.call_count == (2 if fidelity else 1)


def test_uncertain_historical_wording_remains_draft_after_fresh_gates(monkeypatch):
    request = Mock(side_effect=[
        json.dumps(dict(equivalent=True, natural=True, address_preserved=True, reason="Fresh fidelity")),
        json.dumps(dict(natural=True, reason="Fresh fluency"))])
    monkeypatch.setattr(SemanticTranslator, "_opencode_request", request)
    result = SemanticTranslator("opencode").revalidate_pacing_candidate("Source", "Lời gốc.", "Lời cũ.",
        allow_review_draft=True, needs_review=True, review_reason="Unresolved role")
    assert result["pacing_verification"]["status"] == "draft"
    assert result["pacing_verification"]["review_reason"] == "Unresolved role"


def test_rewrite_and_exact_revalidation_share_identical_validation_requests(monkeypatch):
    fidelity = json.dumps(dict(equivalent=True, natural=True, address_preserved=True, reason="Fresh fidelity"))
    fluency = json.dumps(dict(natural=True, reason="Fresh fluency"))
    request = Mock(side_effect=[rewrite_response("Chưa làm."), fidelity, fluency, fidelity, fluency])
    monkeypatch.setattr(SemanticTranslator, "_opencode_request", request)
    translator = SemanticTranslator("opencode")
    context = [{"id": 1, "text_zh": "Not yet.", "text_vi": "Vẫn chưa."}]
    translator.rewrite_for_pacing("I have not done it.", "Vẫn chưa làm việc đó.", 1., context, source_language="en")
    translator.revalidate_pacing_candidate("I have not done it.", "Vẫn chưa làm việc đó.", "Chưa làm.", context,
                                          source_language="en")
    assert request.call_args_list[1:3] == request.call_args_list[3:5]


def test_instance_only_capability_never_enables_unknown_adapter(durable, aligner):
    adapter = Mock(supports_pacing_revalidation=True,
        revalidate_pacing_candidate=Mock(return_value=verified_candidate("Cũ ngắn.")),
        rewrite_for_pacing=Mock(return_value=verified_candidate("Mới.")))
    args = options(durable, aligner, adapter, RecordedSynthesizer({"Lời ban đầu.": 2., "Mới.": .7}))
    seed(args)
    assert synthesize_natural_speech(**args)["text"] == "Mới."
    adapter.revalidate_pacing_candidate.assert_not_called()


def test_revalidator_cannot_substitute_different_historical_text(durable, aligner):
    adapter = CapableAdapter()
    adapter.validate.side_effect = None
    adapter.validate.return_value = verified_candidate("Different unmeasured text.")
    engine = RecordedSynthesizer(2.)
    args = options(durable, aligner, adapter, engine)
    seed(args)
    with pytest.raises(RuntimeError, match="đổi lời đọc lịch sử"):
        synthesize_natural_speech(**args)
    assert engine.calls == ["Lời ban đầu."]
    assert not args["output_path"].exists()
