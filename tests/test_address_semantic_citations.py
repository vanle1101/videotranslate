"""The citation validator and source-version snapshot must match shown context."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from core.translation_review import AutomaticTranslationReviewer as Reviewer
from core.video_intelligence import VideoIntelligence, VideoIntelligenceError


def sources():
    return [{"id": 92, "start": 10., "end": 11., "text_zh": "我回来了"},
            {"id": 93, "start": 11.1, "end": 12., "text_zh": "妈妈"}]


def reading(quote="妈妈", ref=93):
    return {"address_context": [{"id": 92, "self_address": "", "listener_address": "",
        "self_uncertain": True, "listener_uncertain": True, "uncertain": True,
        "reason": "Reference source does not itself establish speaker identity.",
        "evidence": [{"id": 92, "quote": "我回来了"}],
        "turn_check": {"ambiguous_roles": ["self", "listener"],
            "reason": "Adjacent words require competing turn readings.",
            "evidence": [{"id": 92, "quote": "我回来了"}, {"id": ref, "quote": quote}]}}]}


def test_semantic_only_source_citation_validates_and_is_checkpointed():
    rows = sources()
    client = Mock(model="offline-citations", translate=Mock(return_value=reading()))
    checkpoint = Mock(load=Mock(return_value=None), store=Mock())
    original = deepcopy(rows)
    result = Reviewer._address_reading(client, rows[:1], rows[:1], lambda: None,
        checkpoint, semantic_source=rows)
    assert set(result) == {92}
    assert client.translate.call_count == 1
    stage = checkpoint.store.call_args.args[0]
    assert {row["id"] for row in stage["source"]} == {92, 93}
    assert stage["semantic_context"] == VideoIntelligence._semantic_context(rows, rows[:1])
    assert rows == original
    assert result[92]["uncertain"] is True


@pytest.mark.parametrize("quote,ref", [("爸爸", 93), ("妈妈", 94)])
def test_wrong_or_unshown_semantic_citation_is_still_rejected(quote, ref):
    rows = sources()
    client = Mock(model="offline-citations", translate=Mock(return_value=reading(quote, ref)))
    with pytest.raises(VideoIntelligenceError):
        Reviewer._address_reading(client, rows[:1], rows[:1], lambda: None,
            semantic_source=rows)
    assert client.translate.call_count == 3


def test_semantic_units_contribute_exact_parts_without_joining_or_mutating_rows():
    rows = sources()
    context = [{**rows[0], "text_zh": "我", "final_vi": "Draft text."}]
    original = deepcopy(context)
    semantics = VideoIntelligence._semantic_context(rows, rows[:1])
    joined = Reviewer._address_evidence_context(context, semantics)
    assert [row["id"] for row in joined] == [92, 93]
    assert [row["text_zh"] for row in joined] == ["我回来了", "妈妈"]
    assert joined[0]["final_vi"] == "Draft text."
    assert context == original
    assert Reviewer._address_evidence_context(joined, semantics) == joined


def test_turn_only_citation_is_versioned_and_invalidated_on_source_correction():
    rows = sources()
    semantics = VideoIntelligence._semantic_context(rows, rows[:1])
    context = Reviewer._address_evidence_context(rows[:1], semantics)
    result = {92: reading()["address_context"][0]}
    snapshot = Reviewer._address_source_snapshot(result, 92, context)
    assert snapshot == {"92": "我回来了", "93": "妈妈"}
    assert Reviewer._address_cites_changed_source(result, 92, {93})
    assert not Reviewer._address_cites_changed_source(result, 92, {94})
    output = {92: {**rows[0], "needs_review": False, "verification": {
        "status": "verified", "address_applicable": True, "address_verified": True,
        "semantic_verified": True, "address_context_sources": snapshot}},
        93: {**rows[1], "text_zh": "爸爸"}}
    Reviewer._invalidate_changed_address_sources(output)
    assert output[92]["needs_review"]
    assert output[92]["verification"]["address_stale_source_ids"] == [93]
    assert output[92]["verification"]["address_verified"] is False
