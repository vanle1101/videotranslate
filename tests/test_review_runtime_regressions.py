"""Offline regressions; no provider or production cache is used."""
import json
from unittest.mock import Mock

import pytest

from config import settings
from core.runtime_context import execution_context
from core.translation_review import AutomaticTranslationReviewer
from core.video_intelligence import VideoIntelligenceError


def source(index=0):
    return {"id": index, "start": float(index), "end": float(index + 1),
            "text_zh": "你好", "literal_vi": "Bản cũ", "natural_vi": "Bản cũ",
            "final_vi": "Bản cũ", "needs_review": False, "review_reason": ""}


def components():
    client = Mock(has_credentials=True, model="offline-probe")
    scanner = Mock()
    scanner.extract.side_effect = lambda path, start, end, check: [
        {"start": float(i), "end": float(i + 1), "text_zh": "你好",
         "confidence": .99, "bbox": [.2, .7, .5, .1]}
        for i in range(int(start), int(end))]
    prompts = []
    def respond(prompt, **kwargs):
        prompts.append(prompt)
        rows = json.loads(prompt.split("Câu cần kiểm định: ", 1)[1].split("\nOCR mới tại máy", 1)[0])
        return {"segments": [{**row, "literal_vi": "Xin chào", "natural_vi": "Xin chào",
            "final_vi": "Xin chào", "needs_review": False, "review_reason": "",
            "semantic_verified": True, "verification_reason": "Đối chiếu lời chào.",
            "source_evidence_ids": [f"review{row['id']}"]} for row in rows],
            "screen_texts": [], "summary": ""}
    client.translate.side_effect = respond
    return client, scanner, prompts


def test_nested_review_json_retry_has_exact_location_without_repair_or_secret_leak(caplog):
    row = source()
    valid = {'segments': [{**row, 'semantic_verified': True,
        'verification_reason': 'Đối chiếu nguồn.'}], 'screen_texts': [], 'summary': ''}
    malformed = '{"segments":[{"address_uses":[]}}],"secret-do-not-log":"private"}'
    client = Mock(translate=Mock(side_effect=[malformed, valid]))
    data, checked = AutomaticTranslationReviewer._validated_review_request(
        client, 'Rà đúng schema.', [row], 0, 1, lambda: None,
        AutomaticTranslationReviewer._require_semantic_fields)
    assert data == valid and set(checked['segments']) == {0}
    assert client.translate.call_count == 2
    retry = client.translate.call_args_list[1].args[0]
    assert 'cú pháp tại dòng 1' in retry and 'đóng đúng một segment' in retry
    assert 'code=invalid_json' in caplog.text and 'line=1' in caplog.text
    assert 'secret-do-not-log' not in retry + caplog.text


def test_repeated_nested_review_json_failure_cannot_become_validated_output():
    client = Mock(translate=Mock(return_value='{"segments":[{]'))
    with pytest.raises(VideoIntelligenceError):
        AutomaticTranslationReviewer._validated_review_request(
            client, 'Rà đúng schema.', [source()], 0, 1, lambda: None)
    assert client.translate.call_count == 3


@pytest.mark.parametrize("recovered", [False, True])
def test_missing_translation_recovery_keeps_uncertainty_and_bounds_attempt(recovered):
    client, scanner, prompts = components()
    original = {**source(), "final_vi": "", "natural_vi": "", "literal_vi": "",
                "needs_review": True}
    respond = client.translate.side_effect
    def uncertain(prompt, **kwargs):
        value = respond(prompt, **kwargs)
        for row in value["segments"]:
            row.update(final_vi="Xin chào" if recovered else "", needs_review=True,
                semantic_verified=False, review_reason="Còn thiếu sắc thái nguồn.")
        return value
    client.translate.side_effect = uncertain
    reviewer = AutomaticTranslationReviewer(client, scanner, audio_evidence=False)
    row = reviewer.review("unused", [original], [])["segments"][0]
    assert client.translate.call_count == 2
    assert all("KHÔI PHỤC LỜI DỊCH CÒN THIẾU" in prompt for prompt in prompts)
    assert row["final_vi"] == ("Xin chào" if recovered else "")
    assert row["needs_review"] and row["verification"]["status"] == "unresolved"
    assert row["verification"]["semantic_verified"] is False
    assert row["verification"]["missing_translation_recovery"] == (
        reviewer.missing_translation_recovery_identity(row, [row]))


def test_missing_translation_recovery_identity_ignores_speech_and_tracks_source_model(monkeypatch):
    from copy import deepcopy
    original = {**source(), "final_vi": "", "speaker_id": "voice-1"}
    identity = AutomaticTranslationReviewer.missing_translation_recovery_identity
    key = identity(original, [original, source(1)])
    retry = {**deepcopy(original), "status": "FAILED", "tts_duration": 4,
             "revision": 9, "verification": {"status": "unresolved"}}
    assert key == identity(retry, [retry, source(1)])
    assert key != identity(retry, [retry, {**source(1), "text_zh": "妈妈"}])
    retry["speaker_confirmation"] = {"self_address": "con"}
    assert key != identity(retry, [retry, source(1)])
    monkeypatch.setattr(settings, "OPENCODE_MODEL", "changed-model")
    assert key != identity(original, [original, source(1)])


@pytest.mark.parametrize("failure_mode", ["raised", "row_failure", "unavailable"])
def test_missing_translation_does_not_negative_cache_failed_local_evidence(failure_mode):
    client, scanner, _ = components()
    scanner.extract.return_value = []
    scanner.extract.side_effect = None
    original = {**source(), "final_vi": "", "natural_vi": "", "literal_vi": "",
                "needs_review": True}
    respond = client.translate.side_effect

    def uncertain(prompt, **kwargs):
        value = respond(prompt, **kwargs)
        for row in value["segments"]:
            row.update(final_vi="", natural_vi="", literal_vi="", needs_review=True,
                semantic_verified=False, source_evidence_ids=[], review_reason="Chưa rõ lời nguồn.")
        return value

    client.translate.side_effect = uncertain
    audio = Mock()
    failure = FileNotFoundError("isolated model unavailable") if failure_mode == "unavailable" else TimeoutError("isolated decoder timeout")
    if failure_mode == "raised":
        audio.collect.side_effect = failure
    else:
        audio.collect.return_value = {0: {"faster-whisper-small": "你好"}}
        audio.last_errors = {0: {"sensevoice": failure}}
        audio.last_error = failure
    reviewer = AutomaticTranslationReviewer(client, scanner, audio)
    failed_result = reviewer.review("unused", [original], [])
    failed = failed_result["segments"][0]
    assert failed["verification"]["second_pass_status"] == "completed"
    assert failed["verification"]["diagnostic"]["stage"] == "audio_evidence"
    assert "missing_translation_recovery" not in failed["verification"]
    assert failed["needs_review"] and failed["final_vi"] == ""
    if failure_mode == "unavailable":
        assert not failed_result.get("retry_failures")
    else:
        assert failed["verification"]["status"] == "incomplete"
        assert failed_result["retry_failures"] == [{"segment_ids": [0], "code": "asr_timeout"}]

    # A completed pair of genuinely disagreeing readings is a bounded content
    # outcome, unlike a failed worker. It may record a negative recovery stamp.
    audio.collect.side_effect = None
    audio.collect.return_value = {0: {"sensevoice": "你好", "faster-whisper-small": "再见"}}
    audio.last_error, audio.last_errors = None, {}
    recovered_result = reviewer.review("unused", [failed], [])
    recovered = recovered_result["segments"][0]
    assert not recovered_result.get("retry_failures")
    assert recovered["verification"]["audio_consensus"] is False
    assert "audio_evidence_failures" not in recovered["verification"]
    assert "missing_translation_recovery" in recovered["verification"]
    assert recovered["verification"]["status"] == "unresolved"
    assert not recovered["verification"]["semantic_verified"]
    assert audio.collect.call_count == 2


def test_direct_audio_failure_removes_inherited_negative_recovery_stamp():
    client, scanner, _ = components()
    audio = Mock()
    audio.collect.side_effect = TimeoutError("isolated decoder timeout")
    reviewer = AutomaticTranslationReviewer(client, scanner, audio)
    source_row = {**source(), "needs_review": True, "final_vi": "", "verification": {
        "status": "unresolved", "source_supported": False, "missing_translation_recovery": "old-stamp"}}
    result = reviewer.resolve_audio_uncertainty("unused", {"segments": {0: source_row},
        "screen_texts": [], "translation_sources": []})
    assert "missing_translation_recovery" not in result["segments"][0]["verification"]
    assert source_row["verification"]["missing_translation_recovery"] == "old-stamp"


@pytest.fixture(autouse=True)
def provider(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "opencode")


def test_later_batch_uses_evidence_qualified_corrected_context():
    client, scanner, prompts = components()
    reviewer = AutomaticTranslationReviewer(client, scanner, audio_evidence=False)
    result = reviewer.review("unused", [source(i) for i in range(13)], [])
    context = json.loads(prompts[2].split("Ngữ cảnh lân cận (không tạo thêm ID): ", 1)[1])
    assert result["segments"][10]["verification"]["status"] == "corrected"
    # Eight targets per batch: the first request for the second batch has
    # reviewed context 0..7; target 10 has not been reviewed yet.
    assert next(row for row in context if row["start"] == 7)["final_vi"] == "Xin chào"


def test_subset_review_retains_remote_source_context_without_reviewing_other_ids():
    client, scanner, prompts = components()
    reviewer = AutomaticTranslationReviewer(client, scanner, audio_evidence=False)
    target = source(12)
    prior = {**source(0), "text_zh": "拜托姐"}
    later = {**source(30), "text_zh": "下一句"}
    reviewer._address_reading = Mock(return_value={})
    result = reviewer.review("unused", [target], [], context_segments=[prior, target, later])
    context = json.loads(prompts[0].split("Ngữ cảnh lân cận (không tạo thêm ID): ", 1)[1])
    assert next(row for row in context if row["id"] == 0)["text_zh"] == "拜托姐"
    assert next(row for row in context if row["id"] == 30)["text_zh"] == "下一句"
    assert set(result["segments"]) == {12}
    assert result["summary"]["checked"] == 1
    assert scanner.extract.call_args.args[1:3] == (12., 13.)


def test_resume_reuses_verified_work_but_force_review_runs_again(tmp_path):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"offline-source-identity")
    client, scanner, _ = components()
    reviewer = AutomaticTranslationReviewer(client, scanner, audio_evidence=False,
                                             checkpoint_dir=tmp_path / "checkpoints")
    first = reviewer.review(video, [source()], [])
    second = reviewer.review(video, [source()], [])
    assert first == second
    assert client.translate.call_count == 2
    assert scanner.extract.call_count == 1
    reviewer.review(video, [source()], [], force_review=True)
    assert client.translate.call_count == 4
    assert scanner.extract.call_count == 2


def test_repaired_semantic_pass_resumes_without_repeating_original_bad_request(tmp_path):
    from core.review_checkpoint import ReviewCheckpoint
    video = tmp_path / "input.mp4"
    video.write_bytes(b"source-identity")
    checkpoint = ReviewCheckpoint(video, "offline-model", directory=tmp_path / "checkpoints",
                                  runtime_revision={"test": True})
    row = source()
    reply = {"segments": [{**row, "semantic_verified": False,
        "verification_reason": "Chưa đủ căn cứ."}], "screen_texts": [], "summary": ""}
    client = Mock(translate=Mock(side_effect=['{"segments":[{]', reply]))
    request = lambda prompt="first pass": AutomaticTranslationReviewer._validated_review_request(
        client, prompt, [row], 0, 1, lambda: None,
        AutomaticTranslationReviewer._require_semantic_fields, checkpoint=checkpoint)
    first = request()
    assert request() == first
    assert client.translate.call_count == 2
    assert first[0]["segments"][0]["semantic_verified"] is False
    # The second pass and any context change have different exact identities.
    client.translate.side_effect = None
    client.translate.return_value = reply
    request("independent second pass")
    request("first pass with corrected source context")
    assert client.translate.call_count == 4


def test_interrupted_second_semantic_pass_preserves_first_but_never_approves(tmp_path):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"source-identity")
    client, scanner, prompts = components()
    respond = client.translate.side_effect
    second_calls = 0
    def fail_second_once(prompt, **kwargs):
        nonlocal second_calls
        if "ĐÂY LÀ LƯỢT KIỂM TRA NGỮ NGHĨA THỨ HAI" in prompt:
            second_calls += 1
            if second_calls == 1:
                raise TimeoutError("offline injected timeout")
        return respond(prompt, **kwargs)
    client.translate.side_effect = fail_second_once
    reviewer = AutomaticTranslationReviewer(client, scanner, audio_evidence=False,
                                             checkpoint_dir=tmp_path / "checkpoints")
    first = reviewer.review(video, [source()], [])
    assert first["segments"][0]["verification"]["status"] == "incomplete"
    assert first["segments"][0]["needs_review"] is True
    resumed = reviewer.review(video, [source()], [])
    assert resumed["segments"][0]["verification"]["status"] == "corrected"
    assert client.translate.call_count == 3  # first, failed second, retried second
    assert scanner.extract.call_count == 1


def test_audio_failure_retains_safe_diagnostic_and_segment_ids(caplog):
    client, scanner, _ = components()
    audio = Mock()
    audio.collect.side_effect = RuntimeError("secret-token-do-not-log decoder failed")
    reviewer = AutomaticTranslationReviewer(client, scanner, audio)
    data = {"segments": {0: {**source(), "needs_review": True,
        "verification": {"source_supported": False}}}, "screen_texts": [], "translation_sources": []}
    with execution_context("review-regression"):
        result = reviewer.resolve_audio_uncertainty("unused", data)
    diagnostic = result["segments"][0]["verification"]["diagnostic"]
    assert diagnostic["code"] == "asr_failed"
    assert diagnostic["run_id"] == "review-regression"
    assert diagnostic["segment_ids"] == [0]
    assert "REVIEW_STAGE_FAILED" in caplog.text
    assert "secret-token-do-not-log" not in caplog.text + json.dumps(result)


def test_audio_batch_failure_diagnoses_only_affected_rows_and_keeps_good_evidence():
    client = Mock(has_credentials=True, model="offline-partial-evidence")
    audio = Mock()
    audio.collect.return_value = {0: {"sensevoice": "你好", "faster-whisper-small": "你好"},
                                  1: {"faster-whisper-small": "你好"}}
    error = RuntimeError("private-failed-worker")
    audio.last_error = error
    audio.last_errors = {1: {"sensevoice": error}}
    reviewer = AutomaticTranslationReviewer(client, Mock(), audio)
    reviewer._address_reading = Mock(return_value={})
    reply = {"segments": [{**source(0), "literal_vi": "Xin chào", "natural_vi": "Xin chào",
        "final_vi": "Xin chào", "semantic_verified": True, "verification_reason": "Giữ lời chào."}],
        "screen_texts": [], "summary": ""}
    client.translate.return_value = reply
    data = {"segments": {i: {**source(i), "needs_review": True,
        "verification": {"source_supported": False}} for i in range(2)},
        "screen_texts": [], "translation_sources": []}
    result = reviewer.resolve_audio_uncertainty("unused", data)
    assert result["segments"][0]["verification"]["status"] == "corrected"
    assert "diagnostic" not in result["segments"][0]["verification"]
    assert result["segments"][1]["verification"]["diagnostic"]["segment_ids"] == [1]
    assert result["segments"][1]["verification"]["audio_evidence"][1]["text_zh"] == "你好"
    assert result["segments"][1]["needs_review"]
    assert "private-failed-worker" not in json.dumps(result)
    assert client.translate.call_count == 2

    audio.collect.return_value[1]["sensevoice"] = "你好"
    audio.last_error = None
    audio.last_errors = {}
    reply["segments"][0]["id"] = 1
    reply["segments"][0]["start"], reply["segments"][0]["end"] = 1., 2.
    recovered = reviewer.resolve_audio_uncertainty("unused", result)
    assert recovered["segments"][1]["verification"]["status"] == "corrected"
    assert "diagnostic" not in recovered["segments"][1]["verification"]
    assert "audio_evidence_failures" not in recovered["segments"][1]["verification"]
    assert recovered["segments"][0] == result["segments"][0]


@pytest.mark.parametrize("cancelled", [False, True])
def test_address_request_failure_isolated_to_audio_semantic_batch(cancelled):
    import asyncio
    client = Mock(has_credentials=True, model="offline-address-batches")
    audio = Mock()
    audio.collect.return_value = {i: {"sensevoice": "你好", "faster-whisper-small": "你好"}
                                  for i in range(13)}
    reviewer = AutomaticTranslationReviewer(client, Mock(), audio)
    error = asyncio.CancelledError("cancelled") if cancelled else RuntimeError("private-address-failure")
    reviewer._address_reading = Mock(side_effect=[error, {}])
    reply = {"segments": [{**source(i), "literal_vi": "Xin chào", "natural_vi": "Xin chào",
        "final_vi": "Xin chào", "semantic_verified": True, "verification_reason": "Giữ lời chào."}
        for i in range(8, 13)],
        "screen_texts": [], "summary": ""}
    client.translate.return_value = reply
    data = {"segments": {i: {**source(i), "needs_review": True,
        "verification": {"source_supported": False}} for i in range(13)},
        "screen_texts": [], "translation_sources": []}
    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            reviewer.resolve_audio_uncertainty("unused", data)
        assert reviewer._address_reading.call_count == 1
        client.translate.assert_not_called()
        return
    result = reviewer.resolve_audio_uncertainty("unused", data)
    assert all(result["segments"][i]["verification"]["status"] == "incomplete" for i in range(8))
    assert all(result["segments"][i]["verification"]["status"] == "corrected" for i in range(8, 13))
    assert result["segments"][12]["final_vi"] == "Xin chào"
    assert reviewer._address_reading.call_count == 2 and client.translate.call_count == 2
    assert "private-address-failure" not in json.dumps(result)


def test_owned_cli_deadline_is_reported_as_timeout_without_guessing_network(caplog):
    from core.engines.translation.opencode_client import OpenCodeTimeoutError

    with execution_context("real-deadline-category"):
        diagnostic = AutomaticTranslationReviewer._diagnostic(
            OpenCodeTimeoutError("private-cli-output"), "semantic_request", [source(16)])
    assert diagnostic["code"] == "provider_timeout"
    assert diagnostic["segment_ids"] == [16]
    assert diagnostic["run_id"] == "real-deadline-category"
    assert "quá thời gian" in AutomaticTranslationReviewer._diagnostic_message(diagnostic)
    assert "private-cli-output" not in caplog.text


def test_audio_schema_failure_retries_only_requested_id_without_repeating_asr():
    client = Mock(has_credentials=True, model="offline-schema-probe")
    audio = Mock()
    row = {**source(6), "needs_review": True, "verification": {"source_supported": False}}
    audio.collect.return_value = {6: {"sensevoice": "你好", "faster-whisper-small": "你好"}}
    corrected = {**source(6), "literal_vi": "Xin chào", "natural_vi": "Xin chào", "final_vi": "Xin chào",
        "semantic_verified": True, "verification_reason": "Giữ đúng lời chào nguồn."}
    bad_context_row = {**corrected, "id": 99}
    client.translate.side_effect = [
        {"segments": [corrected, bad_context_row], "screen_texts": [], "summary": ""},
        {"segments": [corrected], "screen_texts": [], "summary": ""},
        {"segments": [corrected], "screen_texts": [], "summary": ""},
    ]
    reviewer = AutomaticTranslationReviewer(client, Mock(), audio)
    reviewer._address_reading = Mock(return_value={})
    result = reviewer.resolve_audio_uncertainty("unused", {"segments": {6: row}, "screen_texts": [],
        "translation_sources": []}, context_segments=[source(99)])
    assert client.translate.call_count == 3
    audio.collect.assert_called_once()
    retry = client.translate.call_args_list[1].args[0]
    assert '"id": 6, "start": 6.0, "end": 7.0' in retry
    assert "các ID trong ngữ cảnh chỉ để đọc" in retry
    assert set(result["segments"]) == {6}
    assert result["segments"][6]["verification"]["semantic_verified"] is True


def test_audio_semantic_fields_are_retried_inside_bounded_schema_helper():
    client = Mock(has_credentials=True, model="offline-semantic-schema")
    row = {**source(6), "needs_review": True, "verification": {"source_supported": False}}
    valid = {**source(6), "literal_vi": "Xin chào", "natural_vi": "Xin chào", "final_vi": "Xin chào",
        "semantic_verified": True, "verification_reason": "Giữ đúng lời nguồn."}
    malformed = {**valid}
    malformed.pop("verification_reason")
    client.translate.side_effect = [
        {"segments": [malformed], "screen_texts": [], "summary": ""},
        {"segments": [valid], "screen_texts": [], "summary": ""},
    ]
    reviewer = AutomaticTranslationReviewer(client, Mock())
    data, _ = reviewer._validated_review_request(client, "prompt", [row], 6., 7., lambda: None,
                                                 reviewer._require_semantic_fields)
    assert client.translate.call_count == 2
    assert data["segments"][0]["verification_reason"] == "Giữ đúng lời nguồn."


@pytest.mark.parametrize("uses", [None, {}, ["con"], [{"term": "con", "role": "reference"}],
    [{"term": "", "role": "self"}], [{"term": "con" * 40, "role": "self"}],
    [{"term": "con\x00", "role": "self"}]])
def test_malformed_address_metadata_cannot_pass_known_role_then_break_checkpoint(uses):
    audit = {"address_verified": True, "semantic_verified": True,
        "address_reason": "Đã đối chiếu nguồn.", "address_uses": uses}
    reading = {0: {"uncertain": False}}
    assert not AutomaticTranslationReviewer._address_verified(reading, 0, audit, "Con về rồi.")
    assert AutomaticTranslationReviewer._address_gate(reading, 0, audit, "我回来了", "Con về rồi.")
    with pytest.raises(VideoIntelligenceError):
        AutomaticTranslationReviewer._require_semantic_fields({"segments": [
            {**source(), **audit, "verification_reason": "Giữ lời về nhà."}]})


def test_invalid_provider_address_metadata_retries_before_publication_and_preserves_storage_contract():
    from core.streaming.session_store import _clean
    row = {**source(6), "semantic_verified": True, "verification_reason": "Giữ lời chào.",
        "address_uses": [{"term": "con", "role": "self", "headers": {"Authorization": "unexpected"}}]}
    bad = {**row, "address_uses": [{"term": "con", "role": "reference"}]}
    client = Mock(translate=Mock(side_effect=[
        {"segments": [bad], "screen_texts": [], "summary": ""},
        {"segments": [row], "screen_texts": [], "summary": ""}]))
    data, _ = AutomaticTranslationReviewer._validated_review_request(
        client, "prompt", [source(6)], 6., 7., lambda: None,
        AutomaticTranslationReviewer._require_semantic_fields)
    assert client.translate.call_count == 2
    audit = data["segments"][0]
    retry_prompt = client.translate.call_args_list[1].args[0]
    assert "role self/listener" in retry_prompt and "Không thêm vai reference" in retry_prompt
    assert audit["address_uses"] == [{"term": "con", "role": "self"}]
    assert _clean({"address_uses": audit["address_uses"]}) == {"address_uses": audit["address_uses"]}


@pytest.mark.parametrize("field,value", [("address_verified", "true"), ("address_applicable", 1),
    ("address_neutral_faithful", None), ("address_reason", []), ("address_reason", "bad\x00")])
def test_present_address_fields_must_obey_the_provider_storage_contract(field, value):
    row = {**source(), "semantic_verified": True, "verification_reason": "Đã rà.", field: value}
    with pytest.raises(VideoIntelligenceError):
        AutomaticTranslationReviewer._require_semantic_fields({"segments": [row]})
