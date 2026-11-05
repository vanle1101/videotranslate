"""Editor drafts use real production validation; offline tests do not certify media."""
import json
from unittest.mock import Mock

import pytest

from core.engines.translation.semantic_translator import SemanticTranslator, PacingReviewRejected
from core.structured_response import StructuredResponseError


def reviewer(monkeypatch, *, equivalent=True, natural=True):
    translator = SemanticTranslator("opencode")
    request = Mock(side_effect=[
        json.dumps({"equivalent": equivalent, "natural": True,
                    "address_preserved": True, "reason": "Giữ đủ các ý nguồn."}),
        json.dumps({"natural": natural, "reason": "Cấu trúc câu Việt rõ ràng."}),
    ])
    monkeypatch.setattr(translator, "_opencode_request", request)
    return translator, request


def propose(translator, text="Phía sau có chìa dự phòng.", **kwargs):
    return translator.rewrite_for_pacing("后面放着备用钥匙", "Chìa dự phòng để ở sau.",
        1.5, [{"id": 93, "start": 133.2, "end": 134.24,
               "text_zh": "后面放着备用钥匙", "vi": "Chìa dự phòng để ở sau.", "is_focus": True}],
        proposed_vi=text, **kwargs)


def test_proposal_requires_both_reviews_and_retains_honest_origin(monkeypatch):
    translator, request = reviewer(monkeypatch)
    result = propose(translator)
    assert request.call_count == 2
    assert result["final_vi"] == "Phía sau có chìa dự phòng."
    proof = result["pacing_verification"]
    assert proof["status"] == "verified" and proof["meaning_preserved"] is True
    assert proof["fluency"]["natural"] is True
    assert proof["method"] == "external-proposal-dual-provider-review"
    meaning_input = json.loads(request.call_args_list[0].args[1])
    assert meaning_input["source"] == "后面放着备用钥匙"
    assert meaning_input["candidate"] == result["final_vi"]
    blind_input = request.call_args_list[1].args[1]
    assert "后面" not in blind_input and "Chìa dự phòng để ở sau." not in blind_input


@pytest.mark.parametrize("equivalent,natural,calls,code", [
    (False, True, 1, "semantic_mismatch"), (True, False, 2, "unnatural"),
])
def test_negative_review_cannot_publish_proposal(monkeypatch, equivalent, natural, calls, code):
    translator, request = reviewer(monkeypatch, equivalent=equivalent, natural=natural)
    with pytest.raises(PacingReviewRejected) as error:
        propose(translator)
    assert error.value.code == code and request.call_count == calls


@pytest.mark.parametrize("text", ["", "   ", 23, {"status": "verified"}, "a" * 2001])
def test_bad_draft_is_rejected_before_provider_calls(monkeypatch, text):
    translator, request = reviewer(monkeypatch)
    with pytest.raises(ValueError):
        propose(translator, text)
    assert request.call_count == 0


def test_duplicate_measured_proposal_is_rejected_before_requests(monkeypatch):
    translator, request = reviewer(monkeypatch)
    with pytest.raises(PacingReviewRejected) as error:
        propose(translator, feedback={"measured_candidates": [{"text": "Phía sau có chìa dự phòng."}]})
    assert error.value.code == "duplicate" and request.call_count == 0


def test_proposal_cannot_add_rude_address(monkeypatch):
    translator, request = reviewer(monkeypatch)
    with pytest.raises(PacingReviewRejected) as error:
        propose(translator, "Chìa của mày ở sau.")
    assert error.value.code == "semantic_mismatch" and request.call_count == 0


def test_proposal_uses_existing_vietnamese_content_validation(monkeypatch):
    translator, request = reviewer(monkeypatch)
    with pytest.raises(StructuredResponseError):
        propose(translator, "中文")
    assert request.call_count == 0
