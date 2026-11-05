from copy import deepcopy
import json

import pytest

from config import settings
from core import multilingual_translation
from core.multilingual_translation import translate_and_review
from core.structured_response import StructuredResponseError


SOURCE = [{"id": 7, "start": 2.1, "end": 4.2, "text_zh": "우리 친구잖아?"}]


def response(*, review=False):
    row = {**SOURCE[0], "literal_vi": "Chúng ta là bạn mà?", "natural_vi": "Chúng ta là bạn mà?",
        "final_vi": "Chúng ta là bạn mà?", "needs_review": False, "review_reason": ""}
    if review:
        row.update(meaning_verified=True, roles_verified=True,
            review_reason="우리 và 친구 nêu quan hệ bạn bè; câu Việt giữ người nói và người nghe.",
            evidence=[{"id": 7, "quote": "우리 친구잖아?"}])
    return {"segments": [row]}


class Client:
    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def translate(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return json.dumps(self.replies.pop(0), ensure_ascii=False)


def test_native_source_and_timings_preserved_independent_audit_invalidates_old_verdict():
    rows = deepcopy(SOURCE)
    rows[0]["verification"] = {"status": "verified", "source_supported": True, "semantic_verified": True}
    before = deepcopy(rows)
    client = Client([response(), response(review=True)])
    actual = translate_and_review(rows, client=client)
    assert rows == before
    assert actual[0]["text_zh"] == SOURCE[0]["text_zh"]
    assert actual[0]["start"] == 2.1
    assert actual[0]["multilingual_review"]["semantic_verified"] is True
    assert actual[0]["verification"] == {"status": "pending", "source_supported": True,
        "semantic_verified": False,
        "reason": "Bản Việt đã được dịch lại; cần kết hợp kiểm định nghĩa với bằng chứng nguồn hiện tại."}
    assert len(client.prompts) == 2


def test_native_default_client_honors_ollama_provider(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(settings, "OLLAMA_MODEL", "translategemma:4b-studio")
    client = Client([response(), response(review=True)])
    monkeypatch.setattr("core.engines.translation.ollama_client.OllamaClient",
        lambda **kwargs: client)
    monkeypatch.setattr(multilingual_translation, "OpenCodeZenClient",
        lambda **kwargs: pytest.fail("native recovery must honor the selected local provider"))

    actual = translate_and_review(SOURCE)

    assert actual[0]["final_vi"] == "Chúng ta là bạn mà?"
    assert len(client.prompts) == 2
    assert "TIẾNG HÀN" in client.prompts[0]
    assert "kiểm định độc lập" in client.prompts[1]


@pytest.mark.parametrize("change", [
    {"id": True}, {"start": 2.1001}, {"end": float("nan")}, {"text_zh": "我们是朋友"},
    {"final_vi": "우리 친구잖아?"}, {"final_vi": "我们是朋友"}, {"final_vi": ""},
    {"final_vi": "[không rõ nghĩa]"}, {"final_vi": "[không nghe rõ lời]"},
    {"final_vi": "Đừng... (nghe không rõ)."},
    {"needs_review": "false"}, {"needs_review": True, "review_reason": ""},
])
def test_reject_malformed_or_foreign_translation_before_review(change):
    bad = response()
    bad["segments"][0].update(change)
    client = Client([bad])
    with pytest.raises(StructuredResponseError):
        translate_and_review(SOURCE, client=client)
    assert len(client.prompts) == 1


@pytest.mark.parametrize("evidence", [[], [{"id": 7, "quote": "엄마"}],
    [{"id": 8, "quote": "우리 친구잖아?"}], [{"id": True, "quote": "우리 친구잖아?"}]])
def test_review_cannot_cite_invented_source(evidence):
    bad = response(review=True)
    bad["segments"][0]["evidence"] = evidence
    with pytest.raises(StructuredResponseError):
        translate_and_review(SOURCE, client=Client([response(), bad]))


def test_review_may_cite_measured_read_only_neighbor_without_publishing_it():
    neighbor = {"id": 6, "start": 0., "end": 2., "text_zh": "안녕 친구", "source_language": "ko"}
    review = response(review=True)
    review["segments"][0]["evidence"].append({"id": 6, "quote": "친구"})
    result = translate_and_review(SOURCE, context=[neighbor], client=Client([response(), review]))
    assert [row["id"] for row in result] == [7]
    assert result[0]["multilingual_review"]["evidence"][-1] == {"id": 6, "quote": "친구"}
    review["segments"][0]["evidence"][-1]["quote"] = "엄마"
    with pytest.raises(StructuredResponseError):
        translate_and_review(SOURCE, context=[neighbor], client=Client([response(), review]))


def test_split_review_may_cite_original_batch_sibling_but_cannot_output_it():
    from core.multilingual_translation import _validate
    sibling = {"id": 6, "start": 0., "end": 2., "text_zh": "안녕 친구"}
    review = response(review=True)
    review["segments"][0]["evidence"].append({"id": 6, "quote": "친구"})
    assert len(_validate(review, SOURCE, review=True, evidence_rows=[sibling])) == 1
    review["segments"].append({**review["segments"][0], **sibling})
    with pytest.raises(StructuredResponseError):
        _validate(review, SOURCE, review=True, evidence_rows=[sibling])


def test_uncertain_roles_stay_unverified_even_when_meaning_is_correct():
    review = response(review=True)
    review["segments"][0].update(roles_verified=False, review_reason="Chưa rõ người nghe.")
    row = translate_and_review(SOURCE, client=Client([response(), review]))[0]
    assert row["needs_review"] is True
    assert row["multilingual_review"]["semantic_verified"] is False
    assert "verification" not in row


def test_explicit_draft_recovery_retains_text_without_certifying_failed_review():
    bad = response(review=True)
    bad["segments"][0]["evidence"] = [{"id": 7, "quote": "invented"}]
    result = translate_and_review(SOURCE, client=Client([response(), bad]),
                                  keep_draft_on_review_failure=True)[0]
    assert result["text_vi"] == "Chúng ta là bạn mà?"
    assert result["needs_review"] is True
    assert result["multilingual_review"]["semantic_verified"] is False
    assert result["multilingual_review"]["evidence"] == []


def test_repaired_draft_survives_new_client_without_replaying_invalid_request(tmp_path, monkeypatch):
    from core.ai_execution import AIExecutionLayer
    from core.engines.translation.opencode_client import OpenCodeZenClient, OpenCodeTimeoutError
    monkeypatch.setattr("core.structured_response.retain_diagnostic", lambda *args, **kwargs: None)
    layer = AIExecutionLayer(cache_root=tmp_path)
    client = OpenCodeZenClient(api_key="fixture-key", execution_layer=layer)
    replies = iter(["{bad", json.dumps(response()), OpenCodeTimeoutError("timeout")])
    calls = []
    def translate(prompt, *args):
        calls.append(prompt)
        result = next(replies)
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(client, "_translate", translate)
    with pytest.raises(OpenCodeTimeoutError):
        translate_and_review(SOURCE, client=client, schema_repairs=2)
    assert len(calls) == 3
    restored = OpenCodeZenClient(api_key="fixture-key", execution_layer=AIExecutionLayer(cache_root=tmp_path))
    resumed = []
    def audit_only(prompt, *args):
        resumed.append(prompt)
        assert "kiểm định độc lập" in prompt
        return json.dumps(response(review=True))
    monkeypatch.setattr(restored, "_translate", audit_only)
    assert translate_and_review(SOURCE, client=restored, schema_repairs=2)[0]["multilingual_review"]["semantic_verified"]
    assert len(resumed) == 1


def test_native_audit_splits_without_retranslating_successful_draft():
    source = [SOURCE[0], {**SOURCE[0], "id": 8, "start": 5, "end": 6}]
    draft = response()
    draft["segments"].append({**draft["segments"][0], **source[1]})
    second = response(review=True)
    second["segments"][0].update(source[1], evidence=[{"id": 8, "quote": source[1]["text_zh"]}])
    client = Client([draft, {}, {}, {}, response(review=True), second])
    result = translate_and_review(source, client=client, schema_repairs=2, keep_draft_on_review_failure=True)
    assert len(client.prompts) == 6
    assert all("kiểm định độc lập" in prompt for prompt in client.prompts[1:])
    assert [row["id"] for row in result] == [7, 8]
    assert all(row["multilingual_review"]["semantic_verified"] for row in result)


@pytest.mark.parametrize("rows", [[], [SOURCE[0], SOURCE[0]], [{**SOURCE[0], "end": 2.1}]])
def test_source_input_validation(rows):
    client = Client([])
    if not rows:
        assert translate_and_review(rows, client=client) == []
    else:
        with pytest.raises(StructuredResponseError):
            translate_and_review(rows, client=client)
    assert client.prompts == []


def test_missing_and_duplicate_ids_rejected():
    for segments in ([], response()["segments"] * 2):
        with pytest.raises(StructuredResponseError):
            translate_and_review(SOURCE, client=Client([{"segments": segments}]))


def test_second_pass_can_correct_draft_without_mutating_source():
    draft = response()
    draft["segments"][0]["final_vi"] = "Chúng ta là người thân mà?"
    actual = translate_and_review(SOURCE, client=Client([draft, response(review=True)]))
    assert actual[0]["text_vi"] == "Chúng ta là bạn mà?"


def test_mixed_source_uses_each_rows_language_and_keeps_both_context_languages():
    sources = [{**SOURCE[0], "source_language": "ko"},
        {"id": 8, "start": 5, "end": 6, "text_zh": "我们是朋友", "source_language": "zh"}]
    draft, audit = response(), response(review=True)
    draft["segments"][0]["source_language"] = "ko"
    audit["segments"][0]["source_language"] = "ko"
    for payload in (draft, audit):
        row = deepcopy(payload["segments"][0])
        row.update(sources[1])
        if "evidence" in row:
            row["evidence"] = [{"id": 8, "quote": "我们是朋友"}]
        payload["segments"].append(row)
    client = Client([draft, audit])
    result = translate_and_review(sources, language="mixed", context=sources, client=client)
    assert [row["source_language"] for row in result] == ["ko", "zh"]
    assert [row["multilingual_review"]["source_language"] for row in result] == ["ko", "zh"]
    assert [row["text_zh"] for row in result] == ["우리 친구잖아?", "我们是朋友"]
    for prompt in client.prompts:
        assert '"source_language": "ko"' in prompt
        assert '"source_language": "zh"' in prompt
        assert "không chứng minh ASR đúng" in prompt
        assert "phiên âm sai lời Trung" in prompt


@pytest.mark.parametrize("language", ["zh", "ko"])
def test_explicit_row_language_wins_over_batch_default(language):
    rows = [{**SOURCE[0], "source_language": "ko"}]
    result = translate_and_review(rows, language=language, client=Client([response(), response(review=True)]))
    assert result[0]["source_language"] == "ko"


@pytest.mark.parametrize("rows,context", [
    (SOURCE, []),
    ([{**SOURCE[0], "source_language": "xx"}], []),
    ([{**SOURCE[0], "source_language": "ko"}], SOURCE),
])
def test_mixed_source_rejects_missing_or_unsupported_labels(rows, context):
    client = Client([])
    with pytest.raises(StructuredResponseError):
        translate_and_review(rows, language="mixed", context=context, client=client)
    assert not client.prompts


def test_model_cannot_relabel_source_language():
    bad = response()
    bad["segments"][0]["source_language"] = "zh"
    with pytest.raises(StructuredResponseError):
        translate_and_review(SOURCE, client=Client([bad]))


def test_schema_repairs_exhaust_then_split_only_failed_batch():
    sources = [{**SOURCE[0], "source_language": "ko"},
               {**SOURCE[0], "id": 8, "start": 5., "end": 6., "source_language": "ko"}]
    second, second_audit = response(), response(review=True)
    for data in (second, second_audit):
        data["segments"][0].update(id=8, start=5., end=6.)
        if "evidence" in data["segments"][0]:
            data["segments"][0]["evidence"][0]["id"] = 8
    client = Client([{}, {}, {}, response(), response(review=True), second, second_audit])
    result = translate_and_review(sources, language="mixed", client=client, schema_repairs=2)
    assert [row["id"] for row in result] == [7, 8]
    assert len(client.prompts) == 7
    assert '"id": 8' not in client.prompts[3]
    assert '"id": 7' not in client.prompts[5]


def test_transport_failure_never_triggers_schema_split():
    from core.engines.translation.opencode_client import OpenCodeTimeoutError
    class Failing:
        calls = 0
        def translate(self, *args, **kwargs):
            self.calls += 1
            raise OpenCodeTimeoutError("timeout")
    client = Failing()
    with pytest.raises(OpenCodeTimeoutError):
        translate_and_review(SOURCE, client=client, schema_repairs=2)
    assert client.calls == 1
