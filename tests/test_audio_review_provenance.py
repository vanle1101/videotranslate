from copy import deepcopy
from unittest.mock import Mock

import pytest

from config import settings
from core.translation_review import AutomaticTranslationReviewer


SOURCE = "\u5047\u7684\u53cd\u800c\u4f1a\u9650\u6d41"
VI = "Sai. Ng\u01b0\u1ee3c l\u1ea1i c\u00f2n b\u1ecb h\u1ea1n ch\u1ebf l\u01b0\u1ee3t ti\u1ebfp c\u1eadn."


def segment(sid=0, **changes):
    return {"id": sid, "start": 1.0, "end": 3.0, "text_zh": SOURCE,
            "literal_vi": VI, "natural_vi": VI, "final_vi": VI,
            "needs_review": True, "review_reason": "OCR chua du can cu", **changes}


def audio_result(source, verification=None):
    audit = {"status": "unresolved", "source_supported": False,
             "semantic_verified": False, **(verification or {})}
    return {"segments": {source["id"]: {**source, "verification": audit}},
            "screen_texts": [], "translation_sources": [],
            "summary": {"checked": 1, "verified": 0, "corrected": 0, "unresolved": 1}}


def audio_answer(source, **changes):
    return {"segments": [{**source, "needs_review": False, "review_reason": "",
        "semantic_verified": True, "verification_reason": "Doi chieu tung menh de voi loi nguon.",
        **changes}], "screen_texts": [], "summary": ""}


def reviewer(monkeypatch, answer):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    client = Mock(has_credentials=True, model="offline-audio-provenance")
    client.translate.side_effect = [answer, answer]
    audio = Mock()
    audio.collect.return_value = {0: {"sensevoice": SOURCE + ".",
                                     "faster-whisper-small": SOURCE}}
    return AutomaticTranslationReviewer(client, Mock(), audio)


def test_successful_audio_recomputes_stale_ocr_acceptance_and_keeps_applicable_evidence(monkeypatch):
    source = segment(text_zh="\u5047\u7684\u53cd\u800c\u4f1a\u9677\u6d41")
    accepted_ocr = {"id": "review-good", "start": 1.0, "end": 2.8,
                    "text_zh": SOURCE, "confidence": .98,
                    "bbox": [.1, .6, .4, .07], "source_scope_ambiguous": False}
    rejected_ocr = {"id": "review-rejected", "start": 1.0, "end": 2.8,
                    "text_zh": "\u5047\u7684\u53cd\u800c\u4f1a\u9677\u6d41", "confidence": .98,
                    "bbox": [.1, .6, .4, .07], "source_scope_conflict": True}
    prior = {"source_supported": False, "source_accepted": False,
             "source_scope_conflict": True, "evidence_ids": ["review-rejected", "review-good"],
             "evidence": [rejected_ocr, accepted_ocr]}
    review = reviewer(monkeypatch, audio_answer(source, text_zh=SOURCE))

    row = review.resolve_audio_uncertainty("source.mp4", audio_result(source, prior))["segments"][0]

    assert row["text_zh"] == SOURCE
    assert row["verification"]["status"] == "corrected"
    assert row["verification"]["semantic_verified"] is True
    assert row["verification"]["source_supported"] is True
    assert row["verification"]["source_accepted"] is True
    assert row["verification"]["source_scope_conflict"] is False
    assert row["verification"]["evidence_ids"] == ["review-good"]
    assert [item["id"] for item in row["verification"]["evidence"]] == ["review-good"]
    assert row["verification"]["evidence_mode"] == "dual-local-asr-text-review"


@pytest.mark.parametrize("changes,retained", [
    ({"text_zh": SOURCE[2:]}, True),
    ({"source_scope_ambiguous": True}, False),
    ({"source_scope_conflict": True}, False),
    ({"start": 4.0, "end": 5.0}, False),
    ({"confidence": .5}, False),
    ({"text_zh": "\u4e0d\u652f\u6301"}, False),
    ({"id": "not-cited"}, False),
])
def test_successful_audio_retains_only_applicable_cited_ocr(monkeypatch, changes, retained):
    source = segment()
    item = {"id": "review0", "start": 1.0, "end": 2.8,
            "text_zh": SOURCE, "confidence": .98, "bbox": [.1, .6, .4, .07], **changes}
    prior = {"source_accepted": False, "source_scope_conflict": True,
             "evidence_ids": ["review0", "missing"], "evidence": [item]}
    review = reviewer(monkeypatch, audio_answer(source))

    row = review.resolve_audio_uncertainty("source.mp4", audio_result(source, prior))["segments"][0]

    assert row["verification"]["source_accepted"] is True
    assert row["verification"]["evidence"] == ([item] if retained else [])
    assert row["verification"]["evidence_ids"] == (["review0"] if retained else [])


def test_audio_model_source_mismatch_preserves_rejected_provenance(monkeypatch):
    source = segment()
    prior = {"source_accepted": False, "source_scope_conflict": True,
             "evidence_ids": ["rejected"], "evidence": [{"id": "rejected"}]}
    review = reviewer(monkeypatch, audio_answer(source, text_zh="\u4e0d\u652f\u6301"))

    row = review.resolve_audio_uncertainty("source.mp4", audio_result(source, prior))["segments"][0]

    assert row["text_zh"] == source["text_zh"]
    assert row["verification"]["source_supported"] is False
    assert row["verification"]["source_accepted"] is False
    assert row["verification"]["source_scope_conflict"] is True
    assert row["verification"]["evidence_ids"] == prior["evidence_ids"]
    assert row["verification"]["evidence"] == prior["evidence"]


def test_audio_disagreement_does_not_rewrite_prior_ocr_provenance(monkeypatch):
    source = segment()
    prior = {"source_supported": False, "source_accepted": False,
             "source_scope_conflict": True, "evidence_ids": ["review-rejected"],
             "evidence": [{"id": "review-rejected", "text_zh": "cuu nguon"}]}
    result = audio_result(source, prior)
    review = reviewer(monkeypatch, audio_answer(source))
    review.audio_evidence.collect.return_value = {0: {"sensevoice": "\u652f\u6301", "faster-whisper-small": "\u4e0d\u652f\u6301"}}

    row = review.resolve_audio_uncertainty("source.mp4", result)["segments"][0]

    assert row["verification"]["source_accepted"] is False
    assert row["verification"]["source_scope_conflict"] is True
    assert row["verification"]["evidence_ids"] == ["review-rejected"]
    assert row["verification"]["evidence"] == prior["evidence"]
    assert row["verification"]["status"] == "unresolved"


def test_audio_review_does_not_touch_manual_rows(monkeypatch):
    source = segment()
    manual = segment(1, needs_review=False, review_reason=None, final_vi="Ban dich thu cong.",
        source_method="manual", verification={"status": "corrected", "source_supported": True,
        "source_accepted": True, "semantic_verified": True})
    result = audio_result(source)
    result["segments"][1] = manual
    review = reviewer(monkeypatch, audio_answer(source))
    before = deepcopy(manual)

    output = review.resolve_audio_uncertainty("source.mp4", result)["segments"][1]

    assert output == before


def test_audio_semantic_failure_leaves_stale_provenance_unchanged(monkeypatch):
    source = segment()
    prior = {"source_supported": False, "source_accepted": False,
             "source_scope_conflict": True, "evidence_ids": ["review-rejected"],
             "evidence": [{"id": "review-rejected", "text_zh": "cuu nguon"}]}
    review = reviewer(monkeypatch, audio_answer(source))
    review.client.translate.side_effect = RuntimeError("semantic unavailable")

    row = review.resolve_audio_uncertainty("source.mp4", audio_result(source, prior))["segments"][0]

    assert row["verification"]["source_accepted"] is False
    assert row["verification"]["source_scope_conflict"] is True
    assert row["verification"]["evidence_ids"] == ["review-rejected"]
    assert row["verification"]["evidence"] == prior["evidence"]
    assert row["verification"]["status"] == "incomplete"
    assert row["verification"]["semantic_verified"] is False


def test_audio_success_does_not_mark_source_verified_for_unresolved_address(monkeypatch):
    source = segment()
    prior = {"source_supported": False, "source_accepted": False,
             "source_scope_conflict": True, "semantic_verified": False}
    review = reviewer(monkeypatch, audio_answer(source))
    review._address_gate = Mock(return_value=True)

    row = review.resolve_audio_uncertainty("source.mp4", audio_result(source, prior))["segments"][0]

    assert row["verification"]["source_accepted"] is True
    assert row["verification"]["semantic_verified"] is False
    assert row["verification"].get("source_verified") is None
    assert row["verification"]["address_verified"] is False
    assert row["needs_review"] is True


def test_audio_no_consensus_path_keeps_existing_result(monkeypatch):
    source = segment()
    prior = {"source_supported": False, "source_accepted": False,
             "source_scope_conflict": True, "evidence_ids": ["review-rejected"],
             "evidence": [{"id": "review-rejected", "text_zh": "cuu nguon"}]}
    result = audio_result(source, prior)
    review = reviewer(monkeypatch, audio_answer(source))
    review.audio_evidence.collect.return_value = {0: {"sensevoice": "", "faster-whisper-small": ""}}

    row = review.resolve_audio_uncertainty("source.mp4", result)["segments"][0]

    assert row["verification"]["source_accepted"] is False
    assert row["verification"]["source_scope_conflict"] is True
    assert row["verification"]["evidence_ids"] == ["review-rejected"]
    assert row["verification"]["evidence"] == prior["evidence"]
    assert row["verification"]["status"] == "unresolved"
