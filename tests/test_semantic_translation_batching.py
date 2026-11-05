"""Bound provider requests without losing full-video context or source identity."""
from copy import deepcopy
from collections import Counter
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from config import settings
from core.engines.translation.ollama_client import OllamaClientError
from core.engines.translation.semantic_translator import SemanticTranslator
from core.structured_response import StructuredResponseError


def rows(count):
    return [{"id": 100 + index * 3, "start": index * 2., "end": index * 2. + 1.,
             "text_zh": "你好", "speaker_id": "speaker-a", "revision": 7,
             "source_asr_row_id": index, "custom_metadata": {"keep": index}}
            for index in range(count)]


def response(batch):
    return json.dumps({"results": [{"id": row["id"], "literal_vi": "Xin chào.",
        "natural_vi": "Xin chào.", "final_vi": f"Xin chào {row['id']}.",
        "needs_review": True, "review_reason": "Offline fixture."} for row in reversed(batch)]})


def targets(prompt):
    return json.JSONDecoder().raw_decode(prompt.split("chuyển ngữ:\n", 1)[1])[0]


def test_ollama_fast_context_preserves_family_roles_and_detected_terms():
    translator = SemanticTranslator("ollama")
    source = json.dumps([
        {"id": 1, "text_zh": "小元是哥哥了"},
        {"id": 2, "text_zh": "是啊爸爸妹妹好可爱"},
        {"id": 3, "text_zh": "亲家你不能作弊啊"},
        {"id": 4, "text_zh": "他抓住的是魔方"},
    ], ensure_ascii=False)

    context = translator._extract_context_and_glossary(source)

    assert "gia đình" in context["theme"].lower()
    assert "không đảo vai" in context["pronouns"]
    assert "giữ đúng vai" in context["pronouns"].lower()
    assert {row["src"]: row["tgt"] for row in context["terms"]} == {
        "小元": "Tiểu Nguyên", "魔方": "khối Rubik", "亲家": "thông gia"}


@pytest.mark.parametrize("provider", ["opencode", "openrouter-free", "muse", "gemini"])
def test_translate_bounds_requests_and_keeps_summary_neighbors_and_identity(monkeypatch, provider):
    translator = SemanticTranslator(provider)
    source = rows(19)[::-1]
    original = deepcopy(source)
    calls, summaries = [], []
    context = {"theme": "Full-video summary", "pronouns": "Unknown roles", "terms": [{"src": "名", "tgt": "Tên", "note": "shared"}]}

    def request(system, prompt):
        if prompt.startswith("Transcript video:"):
            summaries.append(json.loads(prompt.split("\n", 1)[1]))
            return json.dumps(context)
        batch = targets(prompt)
        neighbors = json.JSONDecoder().raw_decode(system.split("trả ID của chúng: ", 1)[1])[0]
        assert context["theme"] in system and context["pronouns"] in system
        assert json.dumps(context["terms"], ensure_ascii=False) in system
        calls.append((batch, neighbors))
        return response(batch)

    monkeypatch.setattr(translator, "_opencode_request", request)
    result = translator.translate(source)
    assert len(summaries) == 1 and len(summaries[0]) == 19
    assert [len(batch) for batch, _ in calls] == [8, 8, 3]
    chronological = rows(19)
    assert [[row["id"] for row in neighbors] for _, neighbors in calls] == [
        [row["id"] for row in chronological[8:11]],
        [row["id"] for row in chronological[5:8] + chronological[16:19]],
        [row["id"] for row in chronological[13:16]]]
    assert Counter(row["id"] for batch, _ in calls for row in batch) == Counter(row["id"] for row in source)
    for actual, expected in zip(result, source):
        assert all(actual[key] == value for key, value in expected.items())
        assert actual["final_vi"] == f"Xin chào {expected['id']}."
    assert source == original


def test_ollama_uses_configured_target_limit_and_preserves_all_ids(monkeypatch):
    translator = SemanticTranslator("ollama")
    source = rows(10)
    calls = []

    def translate(batch, _context, _neighbors):
        identities = tuple(row["id"] for row in batch)
        calls.append(identities)
        return {row["id"]: {"literal_vi": "Chào.", "natural_vi": "Chào.",
                             "final_vi": f"Câu {row['id']}."} for row in batch}

    monkeypatch.setattr(translator, "_execute_translation_batch", translate)
    result = translator._execute_3tier_translation(source, {})

    assert [len(batch) for batch in calls] == [10]
    assert [identity for batch in calls for identity in batch] == [row["id"] for row in source]
    assert set(result) == {row["id"] for row in source}


def test_ollama_structural_batch_error_splits_only_the_bad_source_range(monkeypatch):
    translator = SemanticTranslator("ollama")
    monkeypatch.setattr(settings, "OLLAMA_MODEL", "qwen3:4b")
    source = rows(4)
    calls = []

    def request(_system, prompt, *, max_tokens, response_format):
        targets = json.loads(prompt.split(
            "Dịch các câu sau sang tiếng Việt tự nhiên. Mỗi ID xuất hiện đúng một lần:\n", 1)[1])
        identities = tuple(row["id"] for row in targets)
        calls.append(identities)
        results = [{"id": row["id"], "final_vi": f"VI {row['id']}"} for row in targets]
        if len(results) > 1:
            results.pop()  # Simulate a provider response with missing IDs.
        return json.dumps({"results": results})

    monkeypatch.setattr(translator, "_ollama_text_request", request)
    result = translator._execute_3tier_translation(source, {})

    assert [len(batch) for batch in calls] == [4, 2, 1, 1, 2, 1, 1]
    assert set(result) == {row["id"] for row in source}
    assert all(result[row["id"]]["final_vi"] == f"VI {row['id']}" for row in source)


def test_malformed_batch_repairs_then_splits_without_repeating_successful_siblings(monkeypatch):
    translator = SemanticTranslator("opencode")
    monkeypatch.setattr(settings, "OPENCODE_SCHEMA_REPAIR_ATTEMPTS", 2)
    calls = []

    def request(system, prompt):
        batch = targets(prompt)
        calls.append(tuple(row["id"] for row in batch))
        return "{" if len(batch) > 2 and batch[0]["id"] >= 124 else response(batch)

    monkeypatch.setattr(translator, "_opencode_request", request)
    source = rows(16)
    result = translator._execute_3tier_translation(source, {})
    assert set(result) == {row["id"] for row in source}
    assert calls.count(tuple(row["id"] for row in source[:8])) == 1
    assert calls.count(tuple(row["id"] for row in source[8:])) == 3
    assert calls.count(tuple(row["id"] for row in source[8:12])) == 3
    assert calls.count(tuple(row["id"] for row in source[12:])) == 3
    assert all(len(call) <= 8 for call in calls)


@pytest.mark.parametrize("failure", [RuntimeError("transport"),
    StructuredResponseError("content", code="semantic_content"),
    StructuredResponseError("config", code="invalid_configuration")])
def test_nonstructural_failure_does_not_fan_out(monkeypatch, failure):
    translator = SemanticTranslator("opencode")
    calls = []

    def fail(*args):
        calls.append(args)
        raise failure

    monkeypatch.setattr(translator, "_execute_translation_batch", fail)
    with pytest.raises(type(failure)):
        translator._execute_3tier_translation(rows(16), {})
    assert len(calls) == 1


def test_ollama_untranslated_text_retries_only_the_bad_batch_as_smaller_batches(monkeypatch):
    translator = SemanticTranslator("ollama")
    source = rows(8)
    calls = []

    def translate(batch, context, neighbors):
        identities = tuple(row["id"] for row in batch)
        calls.append(identities)
        if len(batch) > 1:
            raise StructuredResponseError("untranslated Chinese characters", code="untranslated_text")
        return {row["id"]: {"final_vi": f"VI {row['id']}"} for row in batch}

    monkeypatch.setattr(translator, "_execute_translation_batch", translate)
    result = translator._execute_3tier_translation(source, {})
    assert set(result) == {row["id"] for row in source}
    assert all(value["final_vi"] == f"VI {identity}" for identity, value in result.items())
    assert len(calls[0]) == 8
    assert calls.count(tuple(row["id"] for row in source[4:])) == 1
    assert Counter(map(len, calls)) == Counter({8: 1, 4: 2, 2: 4, 1: 8})
    assert len(calls) == 15


def test_ollama_permanent_single_row_failure_does_not_discard_successful_siblings(monkeypatch):
    translator = SemanticTranslator("ollama")
    source = rows(4)
    def translate(batch, context, neighbors):
        if len(batch) > 1:
            raise StructuredResponseError("one row contains Chinese", code="untranslated_text")
        row = batch[0]
        if row["id"] == source[1]["id"]:
            raise StructuredResponseError("one row contains Chinese", code="untranslated_text")
        return {row["id"]: {"final_vi": f"VI {row['id']}"}}
    monkeypatch.setattr(translator, "_execute_translation_batch", translate)
    result = translator._execute_3tier_translation(source, {})
    assert set(result) == {source[0]["id"], source[2]["id"], source[3]["id"]}
    assert set(result.errors) == {source[1]["id"]}
    assert result[source[0]["id"]]["final_vi"] == f"VI {source[0]['id']}"


def test_ollama_truncated_json_splits_only_the_incomplete_batch(monkeypatch):
    translator = SemanticTranslator("ollama")
    source = rows(8)
    calls = []

    def translate(batch, context, neighbors):
        identities = tuple(row["id"] for row in batch)
        calls.append(identities)
        if len(batch) > 1:
            raise OllamaClientError("truncated JSON", code="provider_incomplete")
        return {row["id"]: {"final_vi": f"VI {row['id']}"} for row in batch}

    monkeypatch.setattr(translator, "_execute_translation_batch", translate)
    result = translator._execute_3tier_translation(source, {})
    assert set(result) == {row["id"] for row in source}
    assert all(value["final_vi"] == f"VI {identity}" for identity, value in result.items())
    assert Counter(map(len, calls)) == Counter({8: 1, 4: 2, 2: 4, 1: 8})
    assert len(calls) == 15


def test_single_bad_row_stops_after_bounded_repairs(monkeypatch):
    translator = SemanticTranslator("opencode")
    monkeypatch.setattr(settings, "OPENCODE_SCHEMA_REPAIR_ATTEMPTS", 2)
    calls = []
    monkeypatch.setattr(translator, "_opencode_request", lambda *args: calls.append(args) or "{")
    with pytest.raises(StructuredResponseError):
        translator._execute_3tier_translation(rows(1), {})
    assert len(calls) == 3


def test_duplicate_source_ids_rejected_before_translation(monkeypatch):
    translator = SemanticTranslator("opencode")
    monkeypatch.setattr(translator, "_execute_translation_batch", lambda *args: pytest.fail("Provider called"))
    source = rows(9)
    source[-1]["id"] = str(source[0]["id"])
    with pytest.raises(ValueError, match="Duplicate source IDs"):
        translator._execute_3tier_translation(source, {})


@pytest.mark.parametrize("provider", ["openai", "deepseek"])
def test_legacy_provider_requests_also_respect_eight_target_bound(monkeypatch, provider):
    translator = SemanticTranslator(provider)
    monkeypatch.setattr(translator, "_api_keys", lambda: (
        None, "offline" if provider == "deepseek" else None, "offline" if provider == "openai" else None))
    client = Mock()
    calls = []

    def create(**options):
        batch = targets(options["messages"][1]["content"])
        calls.append(batch)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=response(batch)))])

    client.chat.completions.create.side_effect = create
    monkeypatch.setattr("openai.OpenAI", lambda **kwargs: client)
    monkeypatch.setattr(translator, "_fallback_translate", lambda *args: pytest.fail("Unexpected fallback"))
    source = rows(18)
    source[0]["id"] = str(source[0]["id"])
    result = translator.translate(source)
    assert [len(batch) for batch in calls] == [8, 8, 2]
    assert [row["id"] for row in result] == [row["id"] for row in source]
