"""ID defects repair/split without relaxing content or replaying healthy batches."""
from collections import Counter
from copy import deepcopy
import json

import pytest

from config import settings
from core.engines.translation.semantic_translator import SemanticTranslator
from core.structured_response import StructuredResponseError


def source_rows(count):
    return [{"id": 41 + index * 7, "start": index * 2.0, "end": index * 2.0 + 1,
             "text_zh": "你好"} for index in range(count)]


def request_rows(prompt):
    return json.JSONDecoder().raw_decode(prompt.split("chuyển ngữ:\n", 1)[1])[0]


def response_rows(batch):
    return [{"id": str(row["id"]), "literal_vi": "Xin chào.",
             "natural_vi": "Xin chào.", "final_vi": f"Xin chào {row['id']}.",
             "needs_review": True, "review_reason": "Chưa kiểm chứng."}
            for row in reversed(batch)]


def corrupt(items, defect):
    if defect == "missing_result":
        items.pop()
    elif defect == "missing_id":
        del items[0]["id"]
    elif defect == "duplicate_id":
        items.append(deepcopy(items[0]))
    elif defect == "unexpected_id":
        items[0]["id"] = 999999
    else:
        items[0]["id"] = {"boolean_id": True, "float_id": 41.0,
                          "object_id": {"id": 41}, "null_id": None,
                          "text_id": "not-an-id"}[defect]


ID_DEFECTS = ["missing_result", "missing_id", "duplicate_id", "unexpected_id",
              "boolean_id", "float_id", "object_id", "null_id", "text_id"]
OPENCODE_BATCH_PROVIDERS = sorted(SemanticTranslator.STRICT_PROVIDERS - {"ollama"})


@pytest.mark.parametrize("provider", OPENCODE_BATCH_PROVIDERS)
@pytest.mark.parametrize("defect", ID_DEFECTS)
def test_id_defects_get_two_repairs_then_split_to_singletons(monkeypatch, provider, defect):
    translator = SemanticTranslator(provider)
    monkeypatch.setattr(settings, "OPENCODE_SCHEMA_REPAIR_ATTEMPTS", 2)
    source = source_rows(12)
    original = deepcopy(source)
    calls = []

    def request(system, prompt):
        batch = request_rows(prompt)
        calls.append((tuple(row["id"] for row in batch), "Phản hồi trước" in prompt))
        items = response_rows(batch)
        # Keep the first eight targets healthy; recursively split the last four.
        if batch[0]["id"] >= source[8]["id"] and len(batch) > 1:
            corrupt(items, defect)
        return json.dumps({"results": items})

    monkeypatch.setattr(translator, "_opencode_request", request)
    result = translator._execute_3tier_translation(source, {})
    expected_counts = {tuple(row["id"] for row in source[:8]): 1,
                       tuple(row["id"] for row in source[8:]): 3,
                       tuple(row["id"] for row in source[8:10]): 3,
                       tuple(row["id"] for row in source[10:]): 3,
                       **{(row["id"],): 1 for row in source[8:]}}
    assert Counter(ids for ids, _ in calls) == expected_counts
    for ids, count in expected_counts.items():
        assert [repair for seen, repair in calls if seen == ids] == (
            [False, True, True] if count == 3 else [False])
    assert set(result) == {row["id"] for row in source}
    for row in source:
        assert result[row["id"]]["final_vi"] == f"Xin chào {row['id']}."
        assert result[row["id"]]["needs_review"] is True
    assert source == original


@pytest.mark.parametrize("defect", ID_DEFECTS)
def test_singleton_id_defect_stops_after_two_repairs(monkeypatch, defect):
    translator = SemanticTranslator("opencode")
    monkeypatch.setattr(settings, "OPENCODE_SCHEMA_REPAIR_ATTEMPTS", 2)
    calls = []

    def request(system, prompt):
        calls.append(prompt)
        items = response_rows(request_rows(prompt))
        corrupt(items, defect)
        return json.dumps({"results": items})

    monkeypatch.setattr(translator, "_opencode_request", request)
    with pytest.raises(StructuredResponseError) as caught:
        translator._execute_3tier_translation(source_rows(1), {})
    assert caught.value.code in {"invalid_type", "invalid_content", "missing_field"}
    assert caught.value.path.startswith("$.results")
    assert len(calls) == 3


@pytest.mark.parametrize("provider", OPENCODE_BATCH_PROVIDERS)
@pytest.mark.parametrize("failure", ["empty_translation", "wrong_language", "transport"])
def test_content_and_transport_failures_never_repair_or_split(monkeypatch, provider, failure):
    translator = SemanticTranslator(provider)
    calls = []

    def request(system, prompt):
        calls.append(prompt)
        if failure == "transport":
            raise TimeoutError("Offline transport failure")
        items = response_rows(request_rows(prompt))
        items[0]["final_vi"] = "" if failure == "empty_translation" else "你好"
        return json.dumps({"results": items})

    monkeypatch.setattr(translator, "_opencode_request", request)
    with pytest.raises((TimeoutError, StructuredResponseError)) as caught:
        translator._execute_3tier_translation(source_rows(12), {})
    if failure != "transport":
        assert caught.value.code == "semantic_content"
    assert len(calls) == 1


def test_repaired_id_response_finishes_without_splitting(monkeypatch):
    translator = SemanticTranslator("opencode")
    monkeypatch.setattr(settings, "OPENCODE_SCHEMA_REPAIR_ATTEMPTS", 2)
    source = source_rows(4)
    calls = []

    def request(system, prompt):
        batch = request_rows(prompt)
        calls.append(tuple(row["id"] for row in batch))
        items = response_rows(batch)
        if len(calls) == 1:
            corrupt(items, "missing_result")
        return json.dumps({"results": items})

    monkeypatch.setattr(translator, "_opencode_request", request)
    result = translator._execute_3tier_translation(source, {})
    assert calls == [tuple(row["id"] for row in source)] * 2
    assert set(result) == {row["id"] for row in source}
