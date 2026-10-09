"""Offline regression/fault tests; never counted as real provider acceptance."""
from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from config import settings
from core.engines.translation.opencode_client import OpenCodeZenClient, OpenCodeResponseValidationError
from core.structured_response import (
    parse_object, validate_schema, StructuredResponseError, TRANSLATION_SCHEMA,
    request_structured, retain_diagnostic,
    schema_attempts,
)
from core.translation_review import AutomaticTranslationReviewer
from core.video_intelligence import VideoIntelligenceError


def response():
    return {"segments": [{"id": 113, "start": 181., "end": 182.25,
        "text_zh": "真的吗", "literal_vi": "Thật sao?", "natural_vi": "Thật sao?",
        "final_vi": "Thật sao?", "needs_review": False, "review_reason": "",
        "semantic_verified": True, "verification_reason": "Giữ lời hỏi ngạc nhiên.",
        "address_applicable": False, "address_uses": [], "address_verified": False}],
        "screen_texts": [], "summary": "Câu hỏi ngạc nhiên; chưa xác định quan hệ."}


@pytest.mark.parametrize("raw,code", [
    ('{"segments":', "invalid_json"),
    ('{"id":1,"id":2}', "invalid_content"),
    ('{"duration":NaN}', "invalid_type"),
    ('{"duration":Infinity}', "invalid_type"),
    ('[]', "invalid_type"), (None, "invalid_type"),
])
def test_strict_parser_never_silently_repairs_or_accepts_ambiguous_json(raw, code):
    with pytest.raises(StructuredResponseError) as caught:
        parse_object(raw)
    assert caught.value.code == code


@pytest.mark.parametrize("change,code,path", [
    (lambda value: value.pop("summary"), "missing_field", "$.summary"),
    (lambda value: value.update(summary=None), "invalid_type", "$.summary"),
    (lambda value: value["segments"][0].update(needs_review="false"), "invalid_type", "$.segments[0].needs_review"),
    (lambda value: value["segments"][0].update(id=True), "invalid_type", "$.segments[0].id"),
    (lambda value: value["segments"][0].update(start=float("inf")), "invalid_type", "$.segments[0].start"),
])
def test_response_contract_separates_missing_fields_and_wrong_types(change, code, path):
    value = response()
    change(value)
    with pytest.raises(StructuredResponseError) as caught:
        validate_schema(value, TRANSLATION_SCHEMA)
    assert caught.value.code == code
    assert caught.value.path == path


def test_diagnostics_redact_known_and_inline_secrets_and_bound_retention(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "hidden-configured-credential")
    raw = '{"api_key":"hidden-configured-credential","authorization":"Bearer hidden-header",' \
        '"url":"https://example.test/result?access_token=hidden-query","text":"Dialogue"}'
    path = retain_diagnostic(raw, schema_id="qa-v1", task_kind="translation", attempt=2,
        error=StructuredResponseError("Missing field", code="missing_field", path="$.summary"), directory=tmp_path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["code"] == "missing_field" and saved["attempt"] == 2
    assert "Dialogue" in saved["raw_response"]
    assert all(secret not in path.read_text(encoding="utf-8") for secret in (
        "hidden-configured-credential", "hidden-header", "hidden-query"))
    foreign = tmp_path / "response_user.json"
    foreign.write_text("keep user file", encoding="utf-8")
    for index in range(66):
        retain_diagnostic("X" * 140000, schema_id="qa-v1", task_kind="translation",
            attempt=1, directory=tmp_path)
    files = [item for item in tmp_path.glob("response_*.json") if item != foreign]
    assert len(files) <= 64 and sum(item.stat().st_size for item in files) <= 8_000_000
    assert json.loads(files[-1].read_text(encoding="utf-8"))["truncated"]
    assert not list(tmp_path.glob("*.tmp"))
    assert foreign.read_text(encoding="utf-8") == "keep user file"


def test_diagnostic_disk_failure_does_not_convert_invalid_response_to_success(tmp_path, monkeypatch):
    occupied = tmp_path / "occupied"
    occupied.write_text("file", encoding="utf-8")
    assert retain_diagnostic("bad", schema_id="qa-v1", task_kind="translation", attempt=1,
        directory=occupied) is None


def test_adapter_credential_is_redacted_even_when_not_in_settings_or_a_named_field(tmp_path):
    path = retain_diagnostic("Provider accidentally echoed uncommon-opaque-value in prose",
        schema_id="qa-v1", task_kind="translation", attempt=1,
        directory=tmp_path, secrets=("uncommon-opaque-value",))
    assert "uncommon-opaque-value" not in path.read_text(encoding="utf-8")


def test_injected_adapter_is_validated_without_new_signature_or_production_diagnostics(monkeypatch):
    client = Mock(translate=Mock(return_value='{"value":2}'))
    recorder = Mock()
    monkeypatch.setattr("core.structured_response.retain_diagnostic", recorder)
    raw, checked = request_structured(client, "prompt", parse_object,
        schema_id="qa-v1", task_kind="translation", max_tokens=42)
    assert checked == {"value": 2} and raw == '{"value":2}'
    client.translate.assert_called_once_with("prompt", max_tokens=42)
    recorder.assert_not_called()


def test_typed_adapter_opts_into_validated_cache_and_unwraps_only_schema_error(monkeypatch):
    client = object.__new__(OpenCodeZenClient)
    received = []
    invalid = '{"segments":'

    def reject(prompt, **kwargs):
        received.append(kwargs)
        try:
            kwargs["response_validator"](invalid)
        except StructuredResponseError as error:
            raise OpenCodeResponseValidationError("Invalid structured output") from error

    client.translate = reject
    recorder = Mock()
    monkeypatch.setattr("core.structured_response.retain_diagnostic", recorder)
    with pytest.raises(StructuredResponseError) as caught:
        request_structured(client, "prompt", parse_object,
            schema_id="qa-v1", task_kind="semantic_review", max_tokens=42)
    assert caught.value.code == "invalid_json"
    assert caught.value.raw_response == invalid
    assert received[0]["schema_id"] == "qa-v1"
    assert received[0]["task_kind"] == "semantic_review"
    assert recorder.call_count == 1


def test_contradictory_neutral_address_response_is_repaired_without_flipping_verdict():
    good = response()
    broken = deepcopy(good)
    broken["segments"][0].update(address_applicable=True)
    client = Mock(translate=Mock(side_effect=[broken, good]))
    source = [{"id": 113, "start": 181., "end": 182.25, "text_zh": "真的吗"}]
    data, normalized = AutomaticTranslationReviewer._validated_review_request(client, "audit", source,
        181., 182.25, lambda: None, AutomaticTranslationReviewer._require_semantic_fields)
    assert client.translate.call_count == 2
    assert data["segments"][0]["address_applicable"] is False
    assert normalized["segments"][113]["final_vi"] == "Thật sao?"
    assert broken["segments"][0]["address_applicable"] is True


def test_unresolved_address_contradiction_exhausts_bound_instead_of_becoming_verified():
    broken = response()
    broken["segments"][0].update(address_applicable=True)
    client = Mock(translate=Mock(return_value=broken))
    source = [{"id": 113, "start": 181., "end": 182.25, "text_zh": "真的吗"}]
    with pytest.raises(VideoIntelligenceError):
        AutomaticTranslationReviewer._validated_review_request(client, "audit", source, 181., 182.25,
            lambda: None, AutomaticTranslationReviewer._require_semantic_fields)
    assert client.translate.call_count == 3


def test_configured_zero_repair_attempts_rejects_without_a_second_provider_request(monkeypatch):
    monkeypatch.setattr(settings, "OPENCODE_SCHEMA_REPAIR_ATTEMPTS", 0)
    client = Mock(translate=Mock(return_value="{broken"))
    source = [{"id": 113, "start": 181., "end": 182.25, "text_zh": "真的吗"}]
    with pytest.raises(VideoIntelligenceError):
        AutomaticTranslationReviewer._validated_review_request(client, "audit", source, 181., 182.25,
            lambda: None, AutomaticTranslationReviewer._require_semantic_fields)
    assert client.translate.call_count == 1
    assert schema_attempts(1) == schema_attempts(2) == 1


def test_stage_specific_repair_limit_cannot_be_expanded_by_global_setting(monkeypatch):
    monkeypatch.setattr(settings, "OPENCODE_SCHEMA_REPAIR_ATTEMPTS", 4)
    assert schema_attempts(1) == 2
    assert schema_attempts(2) == 3


def test_validation_does_not_mutate_the_callers_raw_object():
    original = response()
    parsed = parse_object(original)
    parsed["segments"][0]["address_uses"].append({"term": "Em", "role": "self"})
    assert original["segments"][0]["address_uses"] == []
