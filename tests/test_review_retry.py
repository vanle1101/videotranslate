"""Typed review failure propagation and unchanged-row retry ownership."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from core.engines.translation.opencode_client import OpenCodeRequestError, OpenCodeTimeoutError
from core.review_retry import failure_descriptor, review_input_hashes, schedule_review_failures, prune_review_retries
from core.streaming.retry_schedule import due_entries, begin_retry, mark_intent
from test_auto_review import review, run, answer


def owner():
    rows = {sid: SimpleNamespace(id=sid, start=sid * 3, end=sid * 3 + 2,
        asr_text="你好", text_zh="你好", final_vi="Xin chào", natural_vi="Xin chào", literal_vi="Xin chào",
        revision=1, verification={"status":"incomplete"}, confirmed_silence=False)
        for sid in range(3)}
    return SimpleNamespace(segments=rows, screen_texts=[], source_language="zh",
        _retry_schedule=[], _restart_intent="active", is_stopped=False)


def test_second_pass_transport_failure_keeps_incomplete_and_exact_retry_scope(review):
    review.client.translate.side_effect = [answer(), OpenCodeTimeoutError("private response")]
    result = run(review)
    assert result["segments"][0]["verification"]["status"] == "incomplete"
    assert not result["segments"][0]["verification"]["semantic_verified"]
    assert result["retry_failures"] == [{"segment_ids":[0], "code":"provider_timeout"}]
    assert "private" not in str(result)


def test_first_pass_failure_propagates_typed_exception_with_scope(review):
    error = OpenCodeRequestError("private", retryable=True, code="provider_server")
    review.client.translate.side_effect = error
    with pytest.raises(OpenCodeRequestError) as caught:
        run(review)
    assert caught.value is error
    assert error.review_retry_failure == {"segment_ids":[0], "code":"provider_server"}


def test_auth_error_never_gets_retry_descriptor(review):
    review.client.translate.side_effect = [answer(), OpenCodeRequestError("auth", code="provider_authentication")]
    result = run(review)
    assert result["segments"][0]["verification"]["status"] == "incomplete"
    assert not result.get("retry_failures")


def test_only_unchanged_affected_rows_schedule_no_semantic_mutation():
    session = owner()
    snapshot = review_input_hashes(session)
    before = deepcopy(vars(session.segments[0]))
    session.segments[1].revision += 1
    errors = [{"segment_ids":[0,1], "code":"provider_timeout"}]
    records = schedule_review_failures(session, errors, snapshot, now=100)
    assert len(records) == 1 and records[0]["scope_id"] == "0"
    assert records[0]["next_attempt_at"] == 400
    assert vars(session.segments[0]) == before
    assert due_entries(session, now=399) == []
    assert begin_retry(session,"review","0",snapshot[0],now=400)


@pytest.mark.parametrize("intent", ["paused", "user_stopped"])
def test_pause_stop_never_reactivated_by_late_review_failure(intent):
    session = owner()
    hashes = review_input_hashes(session)
    mark_intent(session, intent)
    schedule_review_failures(session,[{"segment_ids":[0],"code":"provider_timeout"}],hashes,now=0)
    assert session._restart_intent == intent
    assert due_entries(session, now=1000) == []
    mark_intent(session,"active")
    assert len(due_entries(session,now=1000)) == 1


def test_source_context_change_and_manual_revision_invalidate_retry():
    session = owner()
    hashes = review_input_hashes(session)
    schedule_review_failures(session,[{"segment_ids":[0,1],"code":"provider_timeout"}],hashes,now=0)
    session.segments[1].verification = {"status":"manual"}
    assert [record["scope_id"] for record in prune_review_retries(session)] == ["0"]
    session.segments[2].text_zh = "source context changed"
    assert prune_review_retries(session) == []


def test_glossary_version_invalidates_review_retry():
    session = owner()
    hashes = review_input_hashes(session)
    from core.streaming.glossary import set_shared_glossary
    set_shared_glossary(session,[{"src":"你好","tgt":"Chào bạn","note":"",
        "provenance":{"kind":"user_supplied","reference":"edit-1"}}])
    assert schedule_review_failures(session,[{"segment_ids":[0],"code":"provider_timeout"}],hashes,now=0) == []


def test_retry_after_and_duplicate_failures_only_increment_once():
    session = owner()
    error = OpenCodeRequestError("private", retryable=True, code="provider_rate_limited")
    error.retry_after = 900
    descriptor = failure_descriptor(error,[{"id":0}])
    records = schedule_review_failures(session,[descriptor,descriptor],review_input_hashes(session),now=100)
    assert len(records) == 1 and records[0]["rounds"] == 1 and records[0]["next_attempt_at"] == 1000
