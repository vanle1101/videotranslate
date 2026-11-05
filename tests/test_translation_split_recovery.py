"""Corrupt batches split without replaying validated sibling checkpoints."""
from copy import deepcopy
import json
from unittest.mock import Mock
import pytest

from core.translation_review import AutomaticTranslationReviewer
from core.video_intelligence import VideoIntelligence


def rows():
    return [{"id": i, "start": float(i), "end": float(i + 1), "text_zh": "你好",
             "asr_text": "你好", "literal_vi": "Xin chào", "natural_vi": "Xin chào",
             "final_vi": "Xin chào", "needs_review": False, "review_reason": ""} for i in range(2)]


def response(items):
    return {"segments": deepcopy(items), "screen_texts": [], "summary": ""}


def test_review_split_preserves_successful_half_on_transport_failure():
    source = rows()
    saved = {}
    checkpoint = Mock()
    checkpoint.load.side_effect = lambda stage: deepcopy(saved.get(json.dumps(stage, sort_keys=True)))
    checkpoint.store.side_effect = lambda stage, value: saved.update({json.dumps(stage, sort_keys=True): deepcopy(value)})
    client = Mock(translate=Mock(side_effect=["{bad"] * 3 + [response(source[:1]), TimeoutError()]))
    invoke = lambda: AutomaticTranslationReviewer._validated_review_request(
        client, "source contract", source, 0, 2, lambda: None, checkpoint=checkpoint)
    with pytest.raises(TimeoutError):
        invoke()
    assert client.translate.call_count == 5 and len(saved) == 1
    client.translate.reset_mock()
    client.translate.side_effect = ["{bad"] * 3 + [response(source[1:])]
    raw, validated = invoke()
    assert client.translate.call_count == 4
    assert [row["id"] for row in raw["segments"]] == [0, 1]
    assert list(validated["segments"]) == [0, 1]
    assert len(saved) == 3


def test_translation_split_preserves_successful_half_and_original_times():
    source = rows()
    processor = object.__new__(VideoIntelligence)
    processor.provider = "opencode"
    saved = {}
    processor._read_checkpoint = lambda stage: deepcopy(saved.get(json.dumps(stage, sort_keys=True)))
    processor._write_checkpoint = lambda stage, data, check=None: saved.update({json.dumps(stage, sort_keys=True): deepcopy(data)})
    client = Mock(translate=Mock(side_effect=["{bad"] * 3 + [response(source[:1]), TimeoutError()]))
    invoke = lambda: processor._request_text_result(client, "source contract", source, [], 0, 2)
    with pytest.raises(TimeoutError):
        invoke()
    assert client.translate.call_count == 5 and len(saved) == 1
    client.translate.reset_mock()
    client.translate.side_effect = ["{bad"] * 3 + [response(source[1:])]
    raw, result = invoke()
    assert client.translate.call_count == 4
    assert list(result["segments"]) == [0, 1]
    assert [(row["start"], row["end"]) for row in json.loads(raw)["segments"]] == [(0, 1), (1, 2)]


def test_reordered_valid_reply_merges_by_source_id():
    source = rows()
    client = Mock(translate=Mock(return_value=response(source[::-1])))
    _, validated = AutomaticTranslationReviewer._validated_review_request(client, "source", source, 0, 2, lambda: None)
    assert validated["segments"][0]["start"] == 0
    assert validated["segments"][1]["start"] == 1


def test_checkpoint_keeps_uncertainty_from_all_repair_attempts():
    source = rows()[:1]
    processor = object.__new__(VideoIntelligence)
    processor.provider = "opencode"
    saved = {}
    processor._read_checkpoint = lambda stage: deepcopy(saved.get(json.dumps(stage, sort_keys=True)))
    processor._write_checkpoint = lambda stage, data, check=None: saved.update({json.dumps(stage, sort_keys=True): deepcopy(data)})
    rejected = response(source)
    rejected["segments"][0].update(needs_review=True, review_reason="Nguồn chưa rõ")
    rejected["segments"][0].pop("natural_vi")
    client = Mock(translate=Mock(side_effect=[rejected, "{bad", response(source)]))
    invoke = lambda: processor._request_text_result(client, "source contract", source, [], 0, 1)
    raw, result = invoke()
    assert result["segments"][0]["needs_review"]
    assert json.loads(raw)["segments"][0]["review_reason"] == "Nguồn chưa rõ"
    _, restored = invoke()
    assert restored["segments"][0]["needs_review"]
    assert client.translate.call_count == 3
