"""Accepted source corrections invalidate only address readings of old words."""
import json
from unittest.mock import Mock

import pytest

from config import settings
from core.translation_review import AutomaticTranslationReviewer


def row(sid, text):
    return {"id": sid, "start": sid * 3.0, "end": sid * 3.0 + 2,
            "text_zh": text, "literal_vi": "Con về rồi.", "natural_vi": "Con về rồi.",
            "final_vi": "Con về rồi.", "needs_review": False, "review_reason": ""}


def address_response(prompt):
    ids = json.loads(prompt.split("ID cần kiểm định: ")[1].split("\nNguồn thoại", 1)[0])
    context = json.loads(prompt.split("Nguồn thoại theo thời gian: ")[1])
    ref = context[-1]
    return {"address_context": [{"id": sid, "self_address": "con", "listener_address": "mẹ",
        "uncertain": False, "reason": "Mạch lời gọi phụ huynh.",
        "evidence": [{"id": ref["id"], "quote": ref["text_zh"]}]} for sid in ids]}


@pytest.mark.parametrize("batch_size", [1, 12])
@pytest.mark.parametrize("accept_correction", [True, False])
def test_future_citation_invalidates_same_or_previous_batch_only_when_correction_accepted(
        monkeypatch, batch_size, accept_correction):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    rows = [row(0, "我饿了"), row(1, "妈妈我回来了")]
    corrected = "爸爸我回来了"
    def respond(prompt, **kwargs):
        if "ID cần kiểm định: " in prompt:
            return address_response(prompt)
        batch = json.loads(prompt.split("Câu cần kiểm định: ")[1].split("\nOCR mới", 1)[0])
        return {"segments": [{**item, "text_zh": corrected if item["id"] == 1 else item["text_zh"],
            "semantic_verified": True, "verification_reason": "Đã kiểm tra nghĩa.",
            "source_evidence_ids": [f"review{item['id']}"], "address_verified": True,
            "address_reason": "Đã đối chiếu ngữ cảnh."} for item in batch], "screen_texts": [], "summary": ""}
    evidence = [{"start": item["start"], "end": item["end"], "text_zh":
        corrected if item["id"] == 1 and accept_correction else item["text_zh"],
        "confidence": .99, "bbox": [.2, .7, .5, .1]} for item in rows]
    scanner = Mock(extract=Mock(side_effect=lambda path, start, end, check:
        [item for item in evidence if item["start"] < end and item["end"] > start]))
    client = Mock(has_credentials=True, model="offline-version-test", translate=Mock(side_effect=respond))
    reviewer = AutomaticTranslationReviewer(client, scanner, audio_evidence=False)
    reviewer.BATCH_SIZE = batch_size
    result = reviewer.review("unused", rows, [{**item, "kind": "subtitle"} for item in evidence])
    first, last = result["segments"][0], result["segments"][1]
    assert last["text_zh"] == (corrected if accept_correction else rows[1]["text_zh"])
    assert first["needs_review"] is accept_correction
    assert first["verification"]["address_verified"] is (not accept_correction)
    if accept_correction:
        assert first["verification"]["address_stale_source_ids"] == [1]


def test_audio_agreement_is_the_source_seen_by_address_reading_not_a_later_invalidation(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")
    original = row(0, "源识别错误")
    original.update(needs_review=True, verification={"status": "unresolved", "source_supported": False})
    agreed = "妈妈我回来了"
    def respond(prompt, **kwargs):
        if "ID cần kiểm định: " in prompt:
            assert agreed in prompt and original["text_zh"] not in prompt
            return address_response(prompt)
        return {"segments": [{**original, "text_zh": agreed, "needs_review": False,
            "semantic_verified": True, "verification_reason": "Đối chiếu nguồn thống nhất.",
            "address_verified": True, "address_reason": "Người con gọi mẹ trực tiếp."}],
            "screen_texts": [], "summary": ""}
    client = Mock(has_credentials=True, model="offline-version-test", translate=Mock(side_effect=respond))
    audio = Mock(collect=Mock(return_value={0: {"sensevoice": agreed, "faster-whisper-small": agreed}}))
    audio.last_error = None
    reviewer = AutomaticTranslationReviewer(client, Mock(), audio_evidence=audio)
    result = reviewer.resolve_audio_uncertainty("unused", {"segments": {0: original},
        "screen_texts": [], "translation_sources": [], "summary": {}})
    accepted = result["segments"][0]
    assert accepted["text_zh"] == agreed
    assert accepted["needs_review"] is False
    assert accepted["verification"]["address_verified"] is True
    assert accepted["verification"]["address_context_sources"] == {"0": agreed}
