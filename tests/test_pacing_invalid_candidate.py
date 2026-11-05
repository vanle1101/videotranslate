"""Offline draft validation failures consume the existing pacing budget."""
import asyncio
import json
from unittest.mock import Mock

import pytest

from config import settings
from core.engines.alignment.natural_speech import synthesize_natural_speech
from core.engines.translation.semantic_translator import SemanticTranslator, PacingReviewRejected
from core.structured_response import StructuredResponseError
from test_natural_speech import RecordedSynthesizer, aligner


def line(text):
    return json.dumps({"literal_vi": text, "natural_vi": text, "final_vi": text,
                       "needs_review": False, "review_reason": ""}, ensure_ascii=False)


def approved(text):
    return [line(text), json.dumps({"equivalent": True, "natural": True,
                                   "address_preserved": True, "reason": "Giữ ý nguồn."}),
            json.dumps({"natural": True, "reason": "Lời Việt tự nhiên."})]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(SemanticTranslator, "_opencode_request",
                        Mock(side_effect=AssertionError("Provider must be stubbed")))
    monkeypatch.setattr(settings, "OPENCODE_SCHEMA_REPAIR_ATTEMPTS", 2)


def fit(tmp_path, aligner, translator, engine):
    return synthesize_natural_speech(text="Tôi vẫn chưa làm việc đó.", source="我还没做。",
        duration=1., output_path=tmp_path / "fit.wav", engine=engine, aligner=aligner,
        translator=translator)


def test_invalid_language_proposal_then_valid_draft_uses_normal_review_and_audio(tmp_path, aligner, monkeypatch):
    translator = SemanticTranslator("opencode")
    request = Mock(side_effect=[line("我没做"), *approved("Chưa làm.")])
    monkeypatch.setattr(translator, "_opencode_request", request)
    engine = RecordedSynthesizer({"Tôi vẫn chưa làm việc đó.": 3., "Chưa làm.": .8})
    result = fit(tmp_path, aligner, translator, engine)
    assert result["text"] == "Chưa làm."
    assert result["pacing_verification"]["status"] == "verified"
    assert request.call_count == 4
    assert "semantic_content" in request.call_args_list[1].args[1]
    assert engine.calls == ["Tôi vẫn chưa làm việc đó.", "Chưa làm."]


@pytest.mark.parametrize("bad,requests", [(line("中文"), 5), (line(""), 5), ('{"final_vi":', 15)])
def test_invalid_candidates_exhaust_five_total_proposals_without_publication(tmp_path, aligner, monkeypatch, bad, requests):
    translator = SemanticTranslator("opencode")
    request = Mock(return_value=bad)
    monkeypatch.setattr(translator, "_opencode_request", request)
    output = tmp_path / "fit.wav"
    output.write_bytes(b"previous accepted audio")
    engine = RecordedSynthesizer(3.)
    with pytest.raises(PacingReviewRejected) as caught:
        fit(tmp_path, aligner, translator, engine)
    assert caught.value.code == "invalid_candidate"
    assert isinstance(caught.value.__cause__, StructuredResponseError)
    assert request.call_count == requests
    assert engine.calls == ["Tôi vẫn chưa làm việc đó."]
    assert output.read_bytes() == b"previous accepted audio"
    assert caught.value.measured_candidate["text"] == "Tôi vẫn chưa làm việc đó."


def test_five_proposals_are_shared_across_validation_review_and_acoustic_rounds(tmp_path, aligner, monkeypatch):
    translator = SemanticTranslator("opencode")
    # One invalid + one verified but overlong + one semantic rejection + two
    # invalid proposals exhaust the same five slots across two acoustic rounds.
    negative = json.dumps({"equivalent": False, "natural": True,
                           "address_preserved": True, "reason": "Mất phủ định."})
    request = Mock(side_effect=[line("中文"), *approved("Tôi chưa làm."),
                               line("Đã làm."), negative, line("中文"), line("中文")])
    monkeypatch.setattr(translator, "_opencode_request", request)
    engine = RecordedSynthesizer(3.)
    output = tmp_path / "fit.wav"
    output.write_bytes(b"previous accepted audio")
    with pytest.raises(PacingReviewRejected) as caught:
        fit(tmp_path, aligner, translator, engine)
    assert caught.value.code == "invalid_candidate"
    assert request.call_count == 8
    assert engine.calls == ["Tôi vẫn chưa làm việc đó.", "Tôi chưa làm."]
    assert output.read_bytes() == b"previous accepted audio"


@pytest.mark.parametrize("failure", [RuntimeError("provider timeout"), asyncio.CancelledError()])
def test_transport_and_cancellation_are_not_invalid_proposal_retries(tmp_path, aligner, monkeypatch, failure):
    translator = SemanticTranslator("opencode")
    request = Mock(side_effect=failure)
    monkeypatch.setattr(translator, "_opencode_request", request)
    engine = RecordedSynthesizer(3.)
    with pytest.raises(type(failure)):
        fit(tmp_path, aligner, translator, engine)
    assert request.call_count == 1
    assert engine.calls == ["Tôi vẫn chưa làm việc đó."]
    assert not (tmp_path / "fit.wav").exists()


def test_ordinary_translation_keeps_strict_content_failure_without_repair(monkeypatch):
    translator = SemanticTranslator("opencode")
    request = Mock(return_value=line("中文"))
    monkeypatch.setattr(translator, "_opencode_request", request)
    with pytest.raises(StructuredResponseError):
        translator.translate_single_segment("妈妈", 1.)
    assert request.call_count == 1
