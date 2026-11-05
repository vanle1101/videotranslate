from copy import deepcopy
import json

import pytest

from core.engines.translation.opencode_client import OpenCodeRequestError, OpenCodeTimeoutError
from core.multilingual_translation import translate_and_review
from core.streaming.native_audit_retry import (
    audit_existing_native_rows, pending_native_audit, validate_pending_audit,
)
from test_multilingual_translation import SOURCE, response


class Client:
    model = "test-model"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def translate(self, prompt, **kwargs):
        self.prompts.append(prompt)
        value = self.replies.pop(0)
        if isinstance(value, BaseException):
            raise value
        return json.dumps(value, ensure_ascii=False)


def pending_row(monkeypatch, *, error=None, supported=True):
    monkeypatch.setattr("core.streaming.native_audit_retry.time.time", lambda: 1000.)
    row = {**SOURCE[0], "source_language": "ko", "revision": 3,
           "verification": {"status": "pending", "source_supported": supported},
           "audio_path": "preserved.wav"}
    return translate_and_review([row], client=Client(response(), error or OpenCodeTimeoutError("secret")),
                                keep_draft_on_review_failure=True)[0]


def test_timeout_records_validated_draft_and_minimum_deadline_without_provider_detail(monkeypatch):
    row = pending_row(monkeypatch)
    marker = pending_native_audit(row)
    assert marker["next_attempt_at"] == 1300
    assert marker["attempt"] == 1
    assert marker["model"] == "test-model"
    assert marker["draft"]["final_vi"] == row["final_vi"]
    assert "secret" not in json.dumps(marker)
    assert validate_pending_audit(json.loads(json.dumps(marker))) == marker
    assert row["needs_review"] and not row["multilingual_review"]["semantic_verified"]


def test_retry_only_audits_existing_text_and_preserves_audio(monkeypatch):
    row = pending_row(monkeypatch)
    before = deepcopy(row)
    client = Client(response(review=True))
    result = audit_existing_native_rows([row], client=client)["segments"][7]
    assert len(client.prompts) == 1 and "kiểm định độc lập" in client.prompts[0]
    assert result["final_vi"] == row["final_vi"]
    assert result["audio_path"] == "preserved.wav"
    assert result["verification"]["status"] == "verified"
    assert pending_native_audit(result) is None
    assert row == before


def test_retry_does_not_create_source_support(monkeypatch):
    row = pending_row(monkeypatch, supported=False)
    result = audit_existing_native_rows([row], client=Client(response(review=True)))["segments"][7]
    assert result["verification"]["status"] == "unresolved"
    assert result["verification"]["source_supported"] is False
    assert result["verification"]["semantic_verified"] is True
    assert pending_native_audit(result) is None


def test_audit_rewrite_keeps_original_words_audio_and_stops_retry(monkeypatch):
    row = pending_row(monkeypatch)
    reviewed = response(review=True)
    reviewed["segments"][0]["final_vi"] = "Chẳng phải chúng ta là bạn sao?"
    result = audit_existing_native_rows([row], client=Client(reviewed))["segments"][7]
    assert result["final_vi"] == row["final_vi"] and result["audio_path"] == "preserved.wav"
    assert result["needs_review"]
    assert "giữ nguyên" in result["review_reason"]
    assert pending_native_audit(result) is None


def test_repeated_transport_failures_keep_audit_only_and_new_cooldown(monkeypatch):
    row = pending_row(monkeypatch)
    for attempt in range(2, 9):
        now = 1000. + 301 * attempt
        monkeypatch.setattr("core.streaming.native_audit_retry.time.time", lambda: now)
        error = OpenCodeRequestError("secret", retryable=True, code="provider_rate_limited")
        error.retry_after = 900
        client = Client(error)
        row = audit_existing_native_rows([row], client=client)["segments"][7]
        marker = pending_native_audit(row)
        assert marker["attempt"] == attempt and marker["next_attempt_at"] == now + 900
        assert len(client.prompts) == 1 and "kiểm định độc lập" in client.prompts[0]
        assert row["audio_path"] == "preserved.wav"


@pytest.mark.parametrize("mutation", [
    {"text_zh": "다른 말"}, {"start": 2.2}, {"end": 4.3}, {"source_language": "ja"},
    {"final_vi": "Tôi đã sửa."}, {"literal_vi": "Tôi đã sửa."}, {"natural_vi": "Tôi đã sửa."},
    {"verification": {"status": "manual"}},
])
def test_manual_or_source_text_edits_are_not_retried(monkeypatch, mutation):
    row = pending_row(monkeypatch)
    row.update(mutation)
    client = Client()
    assert pending_native_audit(row) is None
    result = audit_existing_native_rows([row], client=client)["segments"][7]
    assert not client.prompts and result["final_vi"] == row["final_vi"]


def test_changed_model_retires_pending_without_redraft(monkeypatch):
    row = pending_row(monkeypatch)
    client = Client()
    client.model = "different-model"
    result = audit_existing_native_rows([row], client=client)["segments"][7]
    assert not client.prompts and pending_native_audit(result) is None
    assert result["final_vi"] == row["final_vi"]


def test_semantic_uncertainty_terminates_pending(monkeypatch):
    row = pending_row(monkeypatch)
    review = response(review=True)
    review["segments"][0].update(meaning_verified=False, needs_review=True)
    result = audit_existing_native_rows([row], client=Client(review))["segments"][7]
    assert pending_native_audit(result) is None and result["needs_review"]


def test_invalid_schema_terminates_pending_after_bounded_repairs(monkeypatch):
    row = pending_row(monkeypatch)
    client = Client({}, {}, {})
    result = audit_existing_native_rows([row], client=client)["segments"][7]
    assert len(client.prompts) == 3
    assert pending_native_audit(result) is None and result["needs_review"]


@pytest.mark.parametrize("change", [
    {"version": 999}, {"next_attempt_at": float("inf")}, {"attempt": True},
    {"draft": {}}, {"context": [{}]}, {"identity": "bad"}, {"code": "arbitrary"},
])
def test_invalid_persisted_marker_is_rejected(monkeypatch, change):
    marker = pending_native_audit(pending_row(monkeypatch))
    marker.update(change)
    assert validate_pending_audit(marker) is None


def test_cancel_before_audit_sends_no_request(monkeypatch):
    from asyncio import CancelledError
    row = pending_row(monkeypatch)
    client = Client()
    with pytest.raises(CancelledError):
        audit_existing_native_rows([row], client=client, cancel_check=lambda: True)
    assert not client.prompts and pending_native_audit(row)


def test_pending_nested_in_verification_survives_plain_json_restart(monkeypatch):
    row = pending_row(monkeypatch)
    row["verification"]["multilingual_review"] = row.pop("multilingual_review")
    restored = json.loads(json.dumps(row, ensure_ascii=False))
    client = Client(response(review=True))
    result = audit_existing_native_rows([restored], client=client)["segments"][7]
    assert len(client.prompts) == 1 and result["verification"]["status"] == "verified"


def test_saved_neighbor_context_is_reused_after_restart(monkeypatch):
    monkeypatch.setattr("core.streaming.native_audit_retry.time.time", lambda: 1000.)
    neighbor = {"id": 6, "start": 0., "end": 2., "text_zh": "안녕 친구", "source_language": "ko"}
    row = translate_and_review(SOURCE, context=[neighbor], client=Client(response(), OpenCodeTimeoutError("x")),
                               keep_draft_on_review_failure=True)[0]
    review = response(review=True)
    review["segments"][0]["evidence"].append({"id": 6, "quote": "친구"})
    client = Client(review)
    result = audit_existing_native_rows([json.loads(json.dumps(row))], client=client)["segments"][7]
    assert "안녕 친구" in client.prompts[0]
    assert result["multilingual_review"]["semantic_verified"] is True


def test_persistence_rejects_extra_context_fields(monkeypatch):
    marker = pending_native_audit(pending_row(monkeypatch))
    marker["context"] = [{**SOURCE[0], "id": 8, "source_language": "ko", "credential": "secret"}]
    assert validate_pending_audit(marker) is None

def test_revision_only_change_retires_pending(monkeypatch):
    row = pending_row(monkeypatch)
    row['revision'] += 1
    assert pending_native_audit(row) is None
